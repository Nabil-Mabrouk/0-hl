"""Generic offline study of any strategy version — read-only (spec 09-recherche/08-etude-generique.md,
ADR-025).

Usage (production): docker exec 0-hl_worker python -m app.domain.trading.research.study
(--strategy ID@VER | --file F.json) [--grid G.json] [--days N] [--min-days D] [--min-trades T]
[--order-by FEATURE] [--json]. Reads `hl_trade_minute`, `hl_book_snapshot`, `hl_instrument`,
strategy and policy versions; writes nothing.

A version is data: its entry and exit conditions are evaluated on the features computed by the
SCANNER code (`scanner_features`, the market view) at each closed minute m, reading only minutes
before m. Costs, split, ranking, report and launch criteria are those of the STRAT-02 study
(09-recherche/05). Features are computed once per pair and minute and shared by every variant;
they are stored as double-precision floats (comparison with a threshold converted the same way
gives the SCANNER verdict, T-R41).
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import itertools
import json
import math
import re
import sys
from array import array
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.trading.agents.engine import DecisionEngine
from app.domain.trading.agents.features import (
    BTC_IID,
    DAY_FEATURES,
    DAY_MINUTES,
    HISTORY_MINUTES,
    MARKET_FEATURES,
    MINUTE,
    OK,
    change_feature,
    market_view,
    scanner_features,
)
from app.domain.trading.config_schemas import (
    StrategyConfig,
    stored_strategy_config,
    validate_strategy_config,
)
from app.domain.trading.enums import PolicyVersionStatus
from app.domain.trading.errors import ValidationFailed
from app.domain.trading.models import BookSnapshot, Instrument, RiskPolicyVersion
from app.domain.trading.research.strat01_study import (
    SLIPPAGE,
    Bar,
    PairData,
    Trade,
    load_series,
    split_at,
)
from app.domain.trading.research.strat02_study import Row, StudyReport, rank
from app.domain.trading.types import sha256_hex, utcnow
from app.domain.trading.versioning import get_strategy_version, strategy_coherence_errors

D = Decimal
ONE = D(1)
HUNDRED = D(100)
NAN = math.nan
MAX_VARIANTS = 500
SPREAD = "spread_bps"
PASS, FAIL, UNKNOWN = "PASS", "FAIL", "UNKNOWN"


class StudyError(Exception):
    """Invalid input (version, grid, variant): the study does not run."""


# ---------------------------------------------------------------- grid

_TOKEN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]")


def _path_tokens(path: str) -> list[str | int]:
    out: list[str | int] = []
    for part in path.split("."):
        pos = 0
        for m in _TOKEN.finditer(part):
            if m.start() != pos:
                raise StudyError(f"chemin de grille invalide : {path}")
            out.append(m.group(1) if m.group(1) is not None else int(m.group(2)))
            pos = m.end()
        if pos != len(part) or not part:
            raise StudyError(f"chemin de grille invalide : {path}")
    return out


def set_path(config: dict[str, Any], path: str, value: Any) -> None:
    """Set an existing field of a version config (`entry.conditions[3].threshold`)."""
    tokens = _path_tokens(path)
    node: Any = config
    for i, tok in enumerate(tokens):
        last = i == len(tokens) - 1
        if isinstance(tok, int):
            if not isinstance(node, list) or tok >= len(node):
                raise StudyError(f"chemin de grille inexistant : {path}")
        elif not isinstance(node, dict) or (tok not in node and not last):
            raise StudyError(f"chemin de grille inexistant : {path}")
        if last:
            node[tok] = value
        else:
            node = node[tok]


def expand_grid(base: dict[str, Any], grid: dict[str, list[Any]] | None) -> list[tuple[str, dict[str, Any]]]:
    """Cartesian product of the grid; (label, config). Refused over MAX_VARIANTS."""
    if not grid:
        return []
    for path, values in grid.items():
        if not isinstance(values, list) or not values:
            raise StudyError(f"grille : liste de valeurs vide ou invalide pour {path}")
        _path_tokens(path)
    total = math.prod(len(v) for v in grid.values())
    if total > MAX_VARIANTS:
        raise StudyError(f"grille : {total} variantes, au plus {MAX_VARIANTS}")
    paths = list(grid)
    out: list[tuple[str, dict[str, Any]]] = []
    for combo in itertools.product(*(grid[p] for p in paths)):
        cfg = copy.deepcopy(base)
        for p, v in zip(paths, combo):
            set_path(cfg, p, v)
        out.append((" ".join(f"{_short(p)}={v}" for p, v in zip(paths, combo)), cfg))
    return out


def _short(path: str) -> str:
    return (path.replace("entry.conditions", "e").replace("exit.conditions.conditions", "x")
            .replace(".threshold", "").replace("exit.", ""))


def parse_config(config: dict[str, Any], where: str = "version") -> StrategyConfig:
    try:
        return validate_strategy_config(config)
    except ValidationFailed as exc:
        raise StudyError(f"{where} invalide : {exc.details or exc}") from exc


# ---------------------------------------------------------------- features

@dataclass
class FeatureTable:
    """Features of every pair at every decision minute of [start, end], as floats (NaN = UNKNOWN)."""
    start: datetime
    n: int
    values: dict[str, dict[str, array]] = field(default_factory=dict)       # iid -> feature -> values
    ref_close: dict[str, list[Decimal | None]] = field(default_factory=dict)
    spread_assumed: dict[str, bytearray] = field(default_factory=dict)

    def idx(self, at: datetime) -> int:
        return int((at - self.start) / MINUTE)

    def at(self, i: int) -> datetime:
        return self.start + MINUTE * i


def _f(feat: Any) -> float:
    return float(feat.value) if feat is not None and feat.status == OK and feat.value is not None else NAN


def build_features(series: dict[str, PairData], pairs: list[str], needed: set[str],
                   spreads: dict[tuple[str, datetime], Decimal], start: datetime,
                   end: datetime) -> FeatureTable:
    """Computed once by the SCANNER code; the market view uses every universe pair (as the
    engine); the spread is read in the snapshot of minute m (missing ⇒ assumed, counted)."""
    n = int((end - start) / MINUTE) + 1
    table = FeatureTable(start, n)
    names = sorted(needed)
    for iid in pairs:
        table.values[iid] = {nm: array("d", [NAN]) * n for nm in names}
        table.ref_close[iid] = [None] * n
        table.spread_assumed[iid] = bytearray(n)
    use_market = bool(needed & MARKET_FEATURES)
    btc = series.get(BTC_IID)
    for i in range(n):
        m = start + MINUTE * i
        view = None
        if use_market:
            trends = {iid: change_feature("trend_24h", pd.minutes, m, DAY_MINUTES)
                      for iid, pd in series.items()}
            btc_ch = change_feature("btc_change_3h", btc.minutes if btc is not None else {}, m, 180)
            view = market_view(trends, btc_ch)
        for iid in pairs:
            pd = series[iid]
            last = pd.minutes.get(m - MINUTE)
            if last is not None and last.close is not None:
                table.ref_close[iid][i] = last.close
            feats: dict[str, Any] = scanner_features(pd.minutes, m)
            if view is not None:
                feats.update(view.for_pair(iid))
            vals = table.values[iid]
            for nm in names:
                if nm == SPREAD:
                    s = spreads.get((iid, m))
                    if s is None:
                        table.spread_assumed[iid][i] = 1
                    else:
                        vals[nm][i] = float(s)
                else:
                    vals[nm][i] = _f(feats.get(nm))
    return table


def needed_features(configs: list[StrategyConfig], order_by: str | None) -> set[str]:
    out: set[str] = set()
    for c in configs:
        out |= DecisionEngine.needed_features(c)
    if order_by:
        out.add(order_by)
    return out


# ---------------------------------------------------------------- conditions

def condition_result(table: FeatureTable, iid: str, i: int, cond: Any) -> str:
    """PASS / FAIL / UNKNOWN as `evaluate_conditions`; a spread without snapshot is assumed PASS."""
    if cond.feature == SPREAD and table.spread_assumed[iid][i]:
        return PASS
    col = table.values[iid].get(cond.feature)
    v = NAN if col is None else col[i]
    if math.isnan(v):
        return UNKNOWN
    t = float(D(cond.threshold))
    ok = {"gt": v > t, "gte": v >= t, "lt": v < t, "lte": v <= t}[cond.op]
    return PASS if ok else FAIL


def block_verdict(table: FeatureTable, iid: str, i: int, block: Any) -> str:
    results = [condition_result(table, iid, i, c) for c in block.conditions]
    if block.operator == "AND":
        return FAIL if FAIL in results else (UNKNOWN if UNKNOWN in results else PASS)
    return PASS if PASS in results else (UNKNOWN if UNKNOWN in results else FAIL)


def entry_signals(table: FeatureTable, pairs: list[str], entry: Any) -> dict[str, list[int]]:
    """Minutes where the entry block is PASS and the decision price (close of m − 1) is known."""
    return {iid: [i for i in range(table.n) if table.ref_close[iid][i] is not None
                  and block_verdict(table, iid, i, entry) == PASS] for iid in pairs}


def pass_rates(table: FeatureTable, pairs: list[str], entry: Any) -> list[dict[str, Any]]:
    """Share of evaluable minutes (known decision price) where each entry condition is PASS/FAIL/UNKNOWN."""
    out = []
    for k, cond in enumerate(entry.conditions):
        counts = {PASS: 0, FAIL: 0, UNKNOWN: 0}
        total = 0
        for iid in pairs:
            for i in range(table.n):
                if table.ref_close[iid][i] is None:
                    continue
                total += 1
                counts[condition_result(table, iid, i, cond)] += 1
        out.append({"index": k, "condition": f"{cond.feature} {cond.op} {cond.threshold}",
                    "evaluable": total,
                    **{r.lower() + "_pct": None if not total else f"{D(c) / total * HUNDRED:.1f}"
                       for r, c in counts.items()}})
    return out


# ---------------------------------------------------------------- exits

@dataclass(frozen=True)
class VariantEntry:
    label: str


@dataclass(frozen=True)
class VariantExit:
    key: str
    label: str


def exit_key(cfg: StrategyConfig) -> VariantExit:
    ex = cfg.exit
    conds = "" if ex.conditions is None else " sortie si " + f" {ex.conditions.operator} ".join(
        f"{c.feature} {c.op} {c.threshold}" for c in ex.conditions.conditions)
    hold = "∞" if ex.max_hold_seconds is None else f"{ex.max_hold_seconds // 60}m"
    label = (f"H={hold} stop={ex.initial_stop_pct}% tp={ex.take_profit_r or '—'}"
             f"{'R' if ex.take_profit_r else ''}{conds} cooldown={(cfg.cooldown_seconds or 0) // 60}m")
    key = json.dumps({"exit": ex.model_dump(exclude_none=True), "cooldown": cfg.cooldown_seconds},
                     sort_keys=True)
    return VariantExit(key, label)


def simulate_exit(table: FeatureTable, iid: str, bars: dict[datetime, Bar], i: int, cfg: StrategyConfig,
                  end: datetime) -> Trade:
    """Entry at close(m − 1) × (1 + slippage). Each held minute t: stop, target, exit condition
    (features at t + 1, i.e. minutes before it; exit at the close of t; UNKNOWN never sells), then
    time. Missing minute ⇒ excluded; no time limit and still open at the end ⇒ OPEN_AT_END."""
    ex = cfg.exit
    at = table.at(i)
    ref = table.ref_close[iid][i]
    entry = ref * (ONE + SLIPPAGE)  # type: ignore[operator]
    stop_pct = D(ex.initial_stop_pct)
    stop = entry * (ONE - stop_pct / HUNDRED)
    tp = None if ex.take_profit_r is None else entry * (ONE + D(ex.take_profit_r) * stop_pct / HUNDRED)
    hold = None if ex.max_hold_seconds is None else ex.max_hold_seconds // 60
    last = ref
    j = 0
    while True:
        if hold is not None and j >= hold:
            return Trade(iid, at, entry, last, "TIME", at + MINUTE * hold)
        t = at + MINUTE * j
        if t >= end:
            return Trade(iid, at, entry, None, "OPEN_AT_END", end)
        bar = bars.get(t)
        if bar is None:
            return Trade(iid, at, entry, None, "EXCLUDED",
                         at + MINUTE * hold if hold is not None else t + MINUTE)
        if bar.low is not None and bar.low <= stop:
            px = bar.open if bar.open is not None and bar.open < stop else stop
            return Trade(iid, at, entry, px * (ONE - SLIPPAGE), "STOP", t + MINUTE)
        if tp is not None and bar.high is not None and bar.high >= tp:
            px = bar.open if bar.open is not None and bar.open > tp else tp
            return Trade(iid, at, entry, px, "TAKE_PROFIT", t + MINUTE)
        if bar.close is not None:
            last = bar.close
        if ex.conditions is not None:
            k = i + j + 1
            if k < table.n and block_verdict(table, iid, k, ex.conditions) == PASS:
                return Trade(iid, at, entry, last, "EXIT_CONDITION", t + MINUTE)
        j += 1


def run_portfolio(table: FeatureTable, series: dict[str, PairData], signals: dict[str, list[int]],
                  cfg: StrategyConfig, end: datetime, max_open: int | None,
                  order: Callable[[str, int], tuple]) -> list[Trade]:
    """One position per pair, cooldown after each exit, at most `max_open` simultaneous positions;
    candidates of the same minute taken in `order`. With a time limit, the hold must end before
    `end`."""
    cooldown = MINUTE * ((cfg.cooldown_seconds or 0) // 60) + timedelta(
        seconds=(cfg.cooldown_seconds or 0) % 60)
    hold = None if cfg.exit.max_hold_seconds is None else cfg.exit.max_hold_seconds // 60
    by_minute: dict[int, list[str]] = {}
    for iid, idxs in signals.items():
        for i in idxs:
            by_minute.setdefault(i, []).append(iid)
    trades: list[Trade] = []
    open_until: dict[str, datetime] = {}
    free_at: dict[str, datetime] = {}
    for i in sorted(by_minute):
        m = table.at(i)
        if hold is not None and m + MINUTE * hold > end:
            continue
        for iid in [k for k, c in open_until.items() if c <= m]:
            del open_until[iid]
        cands = [iid for iid in by_minute[i]
                 if iid not in open_until and not (iid in free_at and m < free_at[iid])]
        cands.sort(key=lambda iid: order(iid, i))
        slots = len(cands) if max_open is None else max(0, max_open - len(open_until))
        for iid in cands[:slots]:
            tr = simulate_exit(table, iid, series[iid].bars, i, cfg, end)
            trades.append(tr)
            open_until[iid] = tr.closed_at
            free_at[iid] = tr.closed_at + cooldown
    return trades


def baseline_signals(table: FeatureTable, pairs: list[str]) -> dict[str, list[int]]:
    """Reference: one entry per pair and per full hour, no entry condition."""
    return {iid: [i for i in range(table.n) if table.at(i).minute == 0
                  and table.ref_close[iid][i] is not None] for iid in pairs}


# ---------------------------------------------------------------- report

@dataclass
class GenericReport(StudyReport):
    config_sha256: str = ""
    grid_sha256: str | None = None
    minutes_read: int = 0
    pass_rates: list[dict[str, Any]] = field(default_factory=list)
    spread_assumed_pct: str | None = None
    open_at_end: int = 0
    max_open_positions: int | None = None
    order_by: str | None = None

    def as_json(self) -> dict[str, Any]:
        out = super().as_json()
        out["assumptions"]["spread"] = self.spread_note
        out["assumptions"]["max_open_positions"] = self.max_open_positions
        out["assumptions"]["order_by"] = self.order_by or "instrument_id"
        out["reproducibility"] = {"config_sha256": self.config_sha256, "grid_sha256": self.grid_sha256,
                                  "minutes_read": self.minutes_read}
        out["pass_rates"] = self.pass_rates
        out["spread_assumed_pct"] = self.spread_assumed_pct
        out["open_at_end"] = self.open_at_end
        return out

    def as_text(self) -> str:
        lines = [super().as_text(), "",
                 f"Reproductibilité : config_sha256 {self.config_sha256}, grille "
                 f"{self.grid_sha256 or '—'}, {self.minutes_read} minutes lues ; positions simultanées "
                 f"≤ {self.max_open_positions or '∞'}, ordre {self.order_by or 'identifiant'}",
                 "Taux de passage des conditions d'entrée (version de départ, minutes évaluables) :"]
        for r in self.pass_rates:
            lines.append(f"  [{r['index']}] {r['condition']:<36} PASS {r['pass_pct'] or '—':>5}%  "
                         f"FAIL {r['fail_pct'] or '—':>5}%  UNKNOWN {r['unknown_pct'] or '—':>5}%")
        if self.spread_assumed_pct is not None:
            lines.append(f"Spread supposé (sans instantané) : {self.spread_assumed_pct} % des minutes")
        if self.open_at_end:
            lines.append(f"Positions ouvertes à la fin (exclues) : {self.open_at_end}")
        return "\n".join(lines)


def run_study(series: dict[str, PairData], pairs: list[str], base: StrategyConfig,
              variants: list[tuple[str, StrategyConfig]], spreads: dict[tuple[str, datetime], Decimal],
              start: datetime, end: datetime, *, max_open: int | None = None,
              order_by: str | None = None, min_trades: int = 20, not_judgeable: str | None = None,
              grid_sha256: str | None = None, name: str | None = None) -> GenericReport:
    cut = split_at(start, end)
    table = build_features(series, pairs, needed_features([base] + [c for _, c in variants], order_by),
                           spreads, start, end)

    def order(iid: str, i: int) -> tuple:
        if order_by is None:
            return (0, 0.0, iid)
        v = table.values[iid][order_by][i]
        return (1 if math.isnan(v) else 0, 0.0 if math.isnan(v) else v, iid)

    sig_cache: dict[str, dict[str, list[int]]] = {}

    def signals(cfg: StrategyConfig) -> dict[str, list[int]]:
        key = json.dumps(cfg.entry.model_dump(), sort_keys=True)
        if key not in sig_cache:
            sig_cache[key] = entry_signals(table, pairs, cfg.entry)
        return sig_cache[key]

    def split(entry: Any, ex: VariantExit, trades: list[Trade]) -> Row:
        return Row(entry, ex, [t for t in trades if t.at < cut], [t for t in trades if t.at >= cut])  # type: ignore[arg-type]

    base_sigs = baseline_signals(table, pairs)
    baselines: dict[VariantExit, Row] = {}

    def baseline_for(cfg: StrategyConfig) -> VariantExit:
        ex = exit_key(cfg)
        if ex not in baselines:
            baselines[ex] = split(None, ex, run_portfolio(table, series, base_sigs, cfg, end, None, order))
        return ex

    def row(label: str, cfg: StrategyConfig) -> Row:
        ex = baseline_for(cfg)
        return split(VariantEntry(label), ex, run_portfolio(table, series, signals(cfg), cfg, end,
                                                            max_open, order))

    vname = name or f"{base.strategy_id} v{base.version}"
    current = row(vname, base)
    rows = [row(label, cfg) for label, cfg in variants]
    top = rank(rows) if variants else [current]
    trades_all = current.first + current.second + [t for r in rows for t in r.first + r.second]
    spread_pct = None
    if SPREAD in DecisionEngine.needed_features(base):
        total = sum(1 for iid in pairs for i in range(table.n) if table.ref_close[iid][i] is not None)
        assumed = sum(1 for iid in pairs for i in range(table.n)
                      if table.ref_close[iid][i] is not None and table.spread_assumed[iid][i])
        spread_pct = None if not total else f"{D(assumed) / total * HUNDRED:.1f}"
    cooldown_h = (base.cooldown_seconds or 0) / 3600
    return GenericReport(
        start=start, cut=cut, end=end, pairs=len(pairs),
        evaluable_minutes=sum(1 for iid in pairs for c in table.ref_close[iid] if c is not None),
        variants_tested=len(rows) or 1, current=current, current_baseline=baselines[current.exit],
        top=top, baselines=baselines,
        title=f"étude générique de {vname} hors production (09-recherche/08, lecture seule)",
        current_name=vname, baseline_name="une entrée par paire et par heure, mêmes sorties",
        cooldown_hours=int(cooldown_h) if cooldown_h == int(cooldown_h) else cooldown_h,  # type: ignore[arg-type]
        min_trades=min_trades, not_judgeable=not_judgeable, adr="ADR-025",
        spread_note=("lu dans les instantanés de carnet (supposé vrai sans instantané)"
                     if spread_pct is not None else "non utilisé"),
        config_sha256=sha256_hex(stored_strategy_config(base)), grid_sha256=grid_sha256,
        minutes_read=sum(len(pd.minutes) for pd in series.values()),
        pass_rates=pass_rates(table, pairs, base.entry), spread_assumed_pct=spread_pct,
        open_at_end=sum(1 for t in trades_all if t.reason == "OPEN_AT_END"),
        max_open_positions=max_open, order_by=order_by)


# ---------------------------------------------------------------- database

async def _policy_max_open(db: AsyncSession, policy_id: str) -> int | None:
    rows = (await db.scalars(select(RiskPolicyVersion).where(RiskPolicyVersion.policy_id == policy_id)
                             .order_by(RiskPolicyVersion.created_at.desc()))).all()
    chosen = next((r for r in rows if r.status == PolicyVersionStatus.PUBLISHED), rows[0] if rows else None)
    return None if chosen is None else chosen.config.get("max_open_positions")


async def _spreads(db: AsyncSession, pairs: list[str], start: datetime,
                   end: datetime) -> dict[tuple[str, datetime], Decimal]:
    rows = await db.execute(select(BookSnapshot.instrument_id, BookSnapshot.minute_start,
                                   BookSnapshot.spread_bps).where(
        BookSnapshot.instrument_id.in_(pairs), BookSnapshot.minute_start >= start,
        BookSnapshot.minute_start <= end))
    return {(iid, m): s for iid, m, s in rows}


async def load_inputs(db: AsyncSession, *, strategy: str | None, file_config: dict[str, Any] | None,
                      grid: dict[str, list[Any]] | None) -> tuple[StrategyConfig, list[tuple[str, StrategyConfig]]]:
    """The version (database or file) and its variants, each validated as at save time (schema and
    coherence); the first error stops the study with its field paths."""
    if strategy is not None:
        sid, _, ver = strategy.partition("@")
        sv = await get_strategy_version(db, sid, ver)
        if sv is None:
            raise StudyError(f"version {strategy} introuvable")
        raw = {**sv.config, "status": getattr(sv.status, "value", sv.status)}   # status is not stored in the config
    else:
        raw = dict(file_config or {})
    base = parse_config(raw)
    errors = await strategy_coherence_errors(db, base)
    if errors:
        raise StudyError(f"version incohérente : {errors}")
    variants: list[tuple[str, StrategyConfig]] = []
    for label, cfg in expand_grid(base.model_dump(exclude_none=True), grid):
        parsed = parse_config(cfg, f"variante « {label} »")
        errs = await strategy_coherence_errors(db, parsed)
        if errs:
            raise StudyError(f"variante « {label} » incohérente : {errs}")
        variants.append((label, parsed))
    return base, variants


async def build_study(sessions: async_sessionmaker[AsyncSession], *, strategy: str | None = None,
                      file_config: dict[str, Any] | None = None, grid: dict[str, list[Any]] | None = None,
                      days: float = 30.0, min_days: float | None = None, min_trades: int = 20,
                      order_by: str | None = None, now: datetime | None = None) -> GenericReport:
    end = (now or utcnow()).replace(second=0, microsecond=0)
    since = end - timedelta(days=days)
    async with sessions() as db:
        base, variants = await load_inputs(db, strategy=strategy, file_config=file_config, grid=grid)
        configs = [base] + [c for _, c in variants]
        instruments = [tuple(r) for r in (await db.execute(
            select(Instrument.id, Instrument.volume_24h_usd, Instrument.base, Instrument.quote)
            .where(Instrument.in_universe))).all()]
        pairs = sorted(DecisionEngine.bot_universe(base, instruments))  # type: ignore[arg-type]
        max_open = await _policy_max_open(db, base.risk_policy_id)
        series = await load_series(db, since, end)
        needed = needed_features(configs, order_by)
        spreads = await _spreads(db, pairs, since, end) if SPREAD in needed else {}
        await db.rollback()
    pairs = [p for p in pairs if p in series]
    first = min((m for pd in series.values() for m in pd.minutes), default=since)
    origin = max(since, first)
    warmup = DAY_MINUTES if needed & (DAY_FEATURES | MARKET_FEATURES) else HISTORY_MINUTES
    start = origin + MINUTE * (warmup + 1)
    days_of_data = (end - origin).total_seconds() / 86400
    not_judgeable = (None if min_days is None or days_of_data >= min_days
                     else f"{days_of_data:.1f} jours de données, {min_days:g} requis")
    return run_study(series, pairs, base, variants, spreads, start, end, max_open=max_open,
                     order_by=order_by, min_trades=min_trades, not_judgeable=not_judgeable,
                     grid_sha256=None if grid is None else sha256_hex(grid))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="0-hl — étude générique d'une version (lecture seule)")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--strategy", help="ID@VERSION d'une version en base")
    src.add_argument("--file", help="fichier JSON d'une version")
    parser.add_argument("--grid", help="fichier JSON : chemin de champ -> liste de valeurs")
    parser.add_argument("--days", type=float, default=30.0)
    parser.add_argument("--min-days", type=float, default=None)
    parser.add_argument("--min-trades", type=int, default=20)
    parser.add_argument("--order-by", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    from app.core.database import SessionLocal, engine
    from app.domain.trading.agents.features import FEATURE_CATALOGUE

    if args.order_by is not None and args.order_by not in FEATURE_CATALOGUE:
        print(f"--order-by : feature inconnue {args.order_by}", file=sys.stderr)
        return 2
    file_config = None if args.file is None else json.load(open(args.file, encoding="utf-8"))
    grid = None if args.grid is None else json.load(open(args.grid, encoding="utf-8"))

    async def _run() -> GenericReport:
        try:
            return await build_study(SessionLocal, strategy=args.strategy, file_config=file_config,
                                     grid=grid, days=args.days, min_days=args.min_days,
                                     min_trades=args.min_trades, order_by=args.order_by)
        finally:
            await engine.dispose()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    try:
        report = asyncio.run(_run())
    except StudyError as exc:
        print(f"étude refusée : {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report.as_json(), indent=2, ensure_ascii=False, default=str)
          if args.json else report.as_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
