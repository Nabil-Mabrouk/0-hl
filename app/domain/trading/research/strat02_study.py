"""Offline STRAT-02 « Breakout 1h » study — read-only (spec 09-recherche/05-etude-strat02.md, ADR-023).

Usage (production): docker exec 0-hl_worker python -m app.domain.trading.research.strat02_study
[--days 30] [--json]. Reads `hl_trade_minute` and `hl_instrument` only; writes nothing.

Same method as the STRAT-01 study (09-recherche/03), whose exit simulation, costs and statistics
are reused: entry signals come from the SCANNER code (`scanner_features`) at each closed minute m,
reading only minutes before m; exits only read minutes from m on. Launch criteria are fixed in the
spec before any result is seen.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.trading.agents.features import (
    HISTORY_MINUTES,
    MINUTE,
    OK,
    MinuteData,
    scanner_features,
)
from app.domain.trading.research.strat01_study import (
    Bar,
    ExitRule,
    PairData,
    Stats,
    Trade,
    load_series,
    simulate_exit,
    split_at,
)
from app.domain.trading.types import utcnow

D = Decimal
ZERO = D(0)
HUNDRED = D(100)
COOLDOWN = timedelta(hours=4)
MIN_RANK_TRADES = 10
TOP = 10

# fixed filters (05-strategies/02 « Entrée v0.1.0 »)
MIN_NOTIONAL = D(25000)
RANGE_MIN = D("0.008")
RANGE_MAX = D("0.05")

# grid (09-recherche/05 « Grille », 324 variants)
SPIKES = (D("1.5"), D(2), D(3))
TREND = (True, False)
CHASE_CAPS = (D("0.005"), D("0.01"))
STOPS = (D("1.5"), D("2.5"), D(4))
TPS: tuple[Decimal | None, ...] = (D("1.5"), D(2), D(3))
HOLDS = (120, 360, 720)

# launch criteria (09-recherche/05 « Critères de lancement paper »)
CRIT_MIN_TRADES = 20
CRIT_MIN_PF = D("1.2")
CRIT_EDGE_VS_BASELINE = D("0.003")


# ---------------------------------------------------------------- signals

@dataclass(frozen=True)
class Signal:
    at: datetime                 # decision time m
    ref_close: Decimal           # close of minute m − 1
    spike: Decimal | None
    notional: Decimal
    breakout: Decimal
    range_1h: Decimal
    trend: Decimal | None


def compute_signals(minutes: dict[datetime, MinuteData], start: datetime,
                    end: datetime) -> list[Signal]:
    """Every evaluable minute m in [start, end]: complete live window, known breakout and range.
    Only minutes strictly before m are read (INV-03)."""
    out: list[Signal] = []
    m = start
    while m <= end:
        f = scanner_features(minutes, m)
        bo, rg, n = f["breakout_1h"], f["range_1h"], f["window_notional_usd"]
        if bo.status == OK and rg.status == OK and n.status == OK:
            last = minutes[m - MINUTE]
            sp, tr = f["volume_spike"], f["trend_4h"]
            out.append(Signal(
                at=m, ref_close=last.close,  # type: ignore[arg-type]
                spike=sp.value if sp.status == OK else None, notional=n.value,  # type: ignore[arg-type]
                breakout=bo.value, range_1h=rg.value,  # type: ignore[arg-type]
                trend=tr.value if tr.status == OK else None))
        m += MINUTE
    return out


@dataclass(frozen=True)
class EntryRule:
    spike: Decimal
    trend: bool
    chase_cap: Decimal

    def accepts(self, s: Signal) -> bool:
        if s.spike is None or s.spike < self.spike or s.notional < MIN_NOTIONAL:
            return False
        if not (ZERO < s.breakout <= self.chase_cap) or not (RANGE_MIN <= s.range_1h <= RANGE_MAX):
            return False
        return not self.trend or (s.trend is not None and s.trend > ZERO)

    @property
    def label(self) -> str:
        return f"v≥{self.spike} tendance={'oui' if self.trend else 'non'} c≤{self.chase_cap * HUNDRED}%"


def baseline_signals(signals: Iterable[Signal]) -> list[Signal]:
    """Reference: one entry per pair and per full hour, no filter."""
    return [s for s in signals if s.at.minute == 0]


def run_pair(iid: str, bars: dict[datetime, Bar], signals: Iterable[Signal], rule: ExitRule,
             end: datetime, cooldown: timedelta = COOLDOWN) -> list[Trade]:
    """One position at a time per pair, 4 h cooldown after exit; holds must end before `end`."""
    trades: list[Trade] = []
    free_at: datetime | None = None
    for s in signals:
        if free_at is not None and s.at < free_at:
            continue
        if s.at + MINUTE * rule.hold_minutes > end:
            break
        tr = simulate_exit(iid, bars, s.at, s.ref_close, rule)
        trades.append(tr)
        free_at = tr.closed_at + cooldown
    return trades


# ---------------------------------------------------------------- report

def extras(trades: Iterable[Trade]) -> dict[str, Any]:
    """Mean holding time and the share of the best pair in the net result."""
    kept = [t for t in trades if not t.excluded]
    if not kept:
        return {"mean_hold_minutes": None, "best_pair": None, "best_pair_share_pct": None}
    hold = sum((t.closed_at - t.at).total_seconds() for t in kept) / 60 / len(kept)
    by_pair: dict[str, Decimal] = {}
    for t in kept:
        by_pair[t.iid] = by_pair.get(t.iid, ZERO) + t.net
    total = sum(by_pair.values(), ZERO)
    best = max(by_pair, key=lambda k: by_pair[k])
    share = None if total <= ZERO else f"{by_pair[best] / total * HUNDRED:.0f}"
    return {"mean_hold_minutes": round(hold), "best_pair": best, "best_pair_share_pct": share}


@dataclass
class Row:
    entry: EntryRule | None      # None = baseline
    exit: ExitRule
    first: list[Trade]
    second: list[Trade]

    @property
    def label(self) -> str:
        return f"{'référence' if self.entry is None else self.entry.label} | {self.exit.label}"

    @property
    def s1(self) -> Stats:
        return Stats.of(self.first)

    @property
    def s2(self) -> Stats:
        return Stats.of(self.second)

    def as_json(self) -> dict[str, Any]:
        return {"variant": self.label,
                "first_part": {**self.s1.as_json(), **extras(self.first)},
                "second_part": {**self.s2.as_json(), **extras(self.second)}}


def rank(rows: list[Row], top: int = TOP, min_trades: int = MIN_RANK_TRADES) -> list[Row]:
    """Ranked on the FIRST part only (mean net per trade)."""
    scored = [(r, r.s1) for r in rows]
    eligible = [(r, s) for r, s in scored if s.trades >= min_trades and s.mean_net is not None]
    eligible.sort(key=lambda rs: rs[1].mean_net, reverse=True)  # type: ignore[arg-type, return-value]
    return [r for r, _ in eligible[:top]]


def verdict(chosen: Row | None, baseline: Row | None) -> dict[str, Any]:
    """The five launch criteria, judged on the second part (09-recherche/05)."""
    if chosen is None or baseline is None:
        return {"launch": False, "reason": "aucune variante n'a assez de trades sur la partie 1",
                "criteria": []}
    s1, s2, b2 = chosen.s1, chosen.s2, baseline.s2
    pf = s2.profit_factor
    edge = None if s2.mean_gross is None or b2.mean_gross is None else s2.mean_gross - b2.mean_gross
    crit = [
        ("≥ 20 trades sur la partie 2", s2.trades >= CRIT_MIN_TRADES, str(s2.trades)),
        ("net moyen > 0 sur la partie 2", s2.mean_net is not None and s2.mean_net > ZERO,
         None if s2.mean_net is None else f"{s2.mean_net * HUNDRED:.3f} %"),
        ("profit factor net ≥ 1,2 sur la partie 2",
         (pf is not None and pf >= CRIT_MIN_PF) or (pf is None and s2.trades > 0 and s2.net_neg == ZERO),
         "—" if pf is None else f"{pf:.2f}"),
        ("brut ≥ référence + 0,3 point sur la partie 2",
         edge is not None and edge >= CRIT_EDGE_VS_BASELINE,
         None if edge is None else f"{edge * HUNDRED:+.3f} point"),
        ("net moyen > 0 sur la partie 1", s1.mean_net is not None and s1.mean_net > ZERO,
         None if s1.mean_net is None else f"{s1.mean_net * HUNDRED:.3f} %"),
    ]
    return {"launch": all(ok for _, ok, _ in crit), "variant": chosen.label,
            "criteria": [{"criterion": c, "ok": ok, "value": v} for c, ok, v in crit]}


V010_ENTRY = EntryRule(D(2), True, D("0.01"))
V010_EXIT = ExitRule(360, D("2.5"), D(2))


@dataclass
class StudyReport:
    start: datetime
    cut: datetime
    end: datetime
    pairs: int
    evaluable_minutes: int
    variants_tested: int
    current: Row
    current_baseline: Row
    top: list[Row]
    baselines: dict[ExitRule, Row]

    @property
    def verdict(self) -> dict[str, Any]:
        chosen = self.top[0] if self.top else None
        return verdict(chosen, None if chosen is None else self.baselines[chosen.exit])

    def as_json(self) -> dict[str, Any]:
        return {"period": {"start": self.start.isoformat(), "split": self.cut.isoformat(),
                           "end": self.end.isoformat()},
                "pairs": self.pairs, "evaluable_minutes": self.evaluable_minutes,
                "variants_tested": self.variants_tested,
                "assumptions": {"fee_pct_each_side": "0.40", "slippage_pct": "0.10",
                                "cooldown_hours": 4, "spread": "not replayed (assumed pass)"},
                "v0_1_0": self.current.as_json(), "baseline_v0_1_0_exits": self.current_baseline.as_json(),
                "top": [{**r.as_json(), "baseline": self.baselines[r.exit].as_json()} for r in self.top],
                "verdict": self.verdict}

    def as_text(self) -> str:
        def line(name: str, trades: list[Trade]) -> str:
            j, x = Stats.of(trades).as_json(), extras(trades)
            share = "" if x["best_pair"] is None else (
                f"  durée {x['mean_hold_minutes']} min  meilleure paire {x['best_pair']}"
                f" {x['best_pair_share_pct'] or '—'}%")
            return (f"    {name:<10} trades {j['trades']:>4}  exclus {j['excluded']:>3}  "
                    f"gain {j['win_rate_pct'] or '—':>5}%  brut {j['mean_gross_pct'] or '—':>7}%  "
                    f"net {j['mean_net_pct'] or '—':>7}%  PF {j['profit_factor'] or '—':>5}  "
                    f"{j['exits']}{share}")

        def block(r: Row, ref: Row | None = None) -> list[str]:
            out = [f"  {r.label}", line("partie 1", r.first), line("partie 2", r.second)]
            if ref is not None:
                out.append(line("réf. p2", ref.second))
            return out

        lines = ["0-hl — étude STRAT-02 « Breakout 1h » hors production (09-recherche/05, lecture seule)",
                 f"période {self.start:%Y-%m-%d %H:%M} → {self.end:%Y-%m-%d %H:%M} UTC, "
                 f"coupure {self.cut:%Y-%m-%d %H:%M} (60 % / 40 %)",
                 f"{self.pairs} paires, {self.evaluable_minutes} minutes évaluables, "
                 f"{self.variants_tested} variantes testées",
                 "hypothèses : frais 0,40 % par côté, glissement 0,10 % (entrée et stop), cooldown 4 h, "
                 "spread non rejoué, résultats en % par trade",
                 "", "STRAT-02 v0.1.0 :", *block(self.current, self.current_baseline),
                 "", "Référence (une entrée par paire et par heure, sorties v0.1.0) :",
                 *block(self.current_baseline),
                 "", f"Top {len(self.top)} (classées sur la partie 1, ≥ {MIN_RANK_TRADES} trades ; "
                 "juger sur la partie 2) :"]
        for i, r in enumerate(self.top, 1):
            lines.append(f" #{i}")
            lines += block(r, self.baselines[r.exit])
        if not self.top:
            lines.append("  aucune variante n'a assez de trades sur la partie 1")
        v = self.verdict
        lines += ["", "Verdict (critères de lancement, partie 2 de la variante #1) :"]
        for c in v["criteria"]:
            lines.append(f"  [{'OK' if c['ok'] else 'NON'}] {c['criterion']} : {c['value'] or '—'}")
        if not v["criteria"]:
            lines.append(f"  {v['reason']}")
        lines.append(f"  ⇒ {'LANCEMENT PAPER POSSIBLE (validation opérateur requise)' if v['launch'] else 'PAS DE LANCEMENT — relancer l’étude dans une semaine'}")
        lines += ["", "Limites : quelques jours, un seul régime de marché, pas de carnet historique, "
                  f"{self.variants_tested} variantes (risque de faux positif). Paper uniquement (ADR-009, ADR-023)."]
        return "\n".join(lines)


def run_study(series: dict[str, PairData], start: datetime, end: datetime) -> StudyReport:
    cut = split_at(start, end)
    signals = {iid: compute_signals(pd.minutes, start, end) for iid, pd in series.items()}
    entries = [EntryRule(v, t, c) for v, t, c in itertools.product(SPIKES, TREND, CHASE_CAPS)]
    exits = [ExitRule(h, x, k) for x, k, h in itertools.product(STOPS, TPS, HOLDS)]

    def row(entry: EntryRule | None, ex: ExitRule, sigs: dict[str, list[Signal]]) -> Row:
        trades = [t for iid, pd in series.items() for t in run_pair(iid, pd.bars, sigs[iid], ex, end)]
        return Row(entry, ex, [t for t in trades if t.at < cut], [t for t in trades if t.at >= cut])

    base_sigs = {iid: baseline_signals(s) for iid, s in signals.items()}
    baselines = {ex: row(None, ex, base_sigs) for ex in exits}
    rows: list[Row] = []
    current: Row | None = None
    for en in entries:
        filtered = {iid: [s for s in sigs if en.accepts(s)] for iid, sigs in signals.items()}
        for ex in exits:
            r = row(en, ex, filtered)
            rows.append(r)
            if en == V010_ENTRY and ex == V010_EXIT:
                current = r
    assert current is not None
    return StudyReport(start=start, cut=cut, end=end, pairs=len(series),
                       evaluable_minutes=sum(len(s) for s in signals.values()),
                       variants_tested=len(rows), current=current,
                       current_baseline=baselines[V010_EXIT], top=rank(rows), baselines=baselines)


async def build_study(sessions: async_sessionmaker[AsyncSession], *, days: float = 30.0,
                      now: datetime | None = None) -> StudyReport:
    end = (now or utcnow()).replace(second=0, microsecond=0)
    since = end - timedelta(days=days)
    async with sessions() as db:
        series = await load_series(db, since, end)
        await db.rollback()
    first = min((m for pd in series.values() for m in pd.minutes), default=since)
    # the first HISTORY_MINUTES only feed the 1 h range and the 4 h trend
    start = max(since, first) + MINUTE * (HISTORY_MINUTES + 1)
    return run_study(series, start, end)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="0-hl — étude STRAT-02 hors production (lecture seule)")
    parser.add_argument("--days", type=float, default=30.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    from app.core.database import SessionLocal, engine

    async def _run() -> StudyReport:
        try:
            return await build_study(SessionLocal, days=args.days)
        finally:
            await engine.dispose()

    report = asyncio.run(_run())
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(report.as_json(), indent=2, ensure_ascii=False, default=str)
          if args.json else report.as_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
