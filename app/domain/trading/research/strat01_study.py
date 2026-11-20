"""Offline STRAT-01 study — read-only (spec 09-recherche/03-etude-strat01.md, ADR-020).

Usage (production): docker exec 0-hl_worker python -m app.domain.trading.research.strat01_study
[--days 7] [--json]. Reads `hl_trade_minute` and `hl_instrument` only; writes nothing.

Entry signals are computed by the SCANNER code (`window_features`) on the window [m−5 min, m);
exits only read minutes starting at or after m. Results are per-trade percentages (no sizing).
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.trading.agents.features import (
    MINUTE,
    OK,
    WINDOW_MINUTES,
    MinuteData,
    window_features,
)
from app.domain.trading.models import Instrument, TradeMinute
from app.domain.trading.types import utcnow

D = Decimal
ZERO = D(0)
ONE = D(1)
HUNDRED = D(100)
FEE = D("0.004")             # taker 0.40 % each side
SLIPPAGE = D("0.001")        # 0.10 % at entry and on a stop exit
COOLDOWN = timedelta(minutes=30)
SPLIT = D("0.6")
MIN_RANK_TRADES = 20
TOP = 10

# grid (09-recherche/03 « Grille »)
SPIKES = (D(3), D(4), D(6), D(8))
BUY_SHARES = (D("0.60"), D("0.70"))
ACTIVITY = ((D(25000), 25), (D(50000), 40), (D(100000), 60))
HOLDS = (5, 15, 30, 60)
STOPS = (D(1), D(2), D(3))
TPS: tuple[Decimal | None, ...] = (D(1), D(2), None)


# ---------------------------------------------------------------- data

@dataclass(frozen=True)
class Bar:
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None


@dataclass
class PairData:
    minutes: dict[datetime, MinuteData] = field(default_factory=dict)  # SCANNER view
    bars: dict[datetime, Bar] = field(default_factory=dict)            # exit view


async def load_series(db: AsyncSession, start: datetime, end: datetime) -> dict[str, PairData]:
    """Universe pairs; `live` wins over `backfill` for the same minute (as the SCANNER)."""
    iids = (await db.scalars(select(Instrument.id).where(Instrument.in_universe)
                             .order_by(Instrument.id))).all()
    out: dict[str, PairData] = {}
    for iid in iids:
        rows = await db.scalars(select(TradeMinute).where(
            TradeMinute.instrument_id == iid, TradeMinute.minute_start >= start,
            TradeMinute.minute_start < end))
        pd = PairData()
        for r in rows.all():
            prev = pd.minutes.get(r.minute_start)
            if prev is not None and prev.source == "live":
                continue
            pd.minutes[r.minute_start] = MinuteData.of(r)
            pd.bars[r.minute_start] = Bar(r.open, r.high, r.low, r.close)
        out[iid] = pd
    return out


# ---------------------------------------------------------------- signals

@dataclass(frozen=True)
class Signal:
    at: datetime                 # decision time m (end of the window)
    ref_close: Decimal           # last close of the window
    spike: Decimal | None
    share: Decimal | None
    change: Decimal
    notional: Decimal
    trades: int


def compute_signals(minutes: dict[datetime, MinuteData], start: datetime,
                    end: datetime) -> list[Signal]:
    """Every evaluable window end m in [start, end]: complete live window and known price change.
    Only minutes strictly before m are read (INV-03)."""
    out: list[Signal] = []
    m = start
    while m <= end:
        f = window_features(minutes, m)
        ch = f["price_change_5m"]
        if ch.status == OK and ch.value is not None:
            ref = next(md.close for i in range(1, WINDOW_MINUTES + 1)
                       if (md := minutes.get(m - MINUTE * i)) is not None and md.close is not None)
            sp, sh = f["volume_spike"], f["buy_notional_share"]
            out.append(Signal(
                at=m, ref_close=ref,
                spike=sp.value if sp.status == OK else None,
                share=sh.value if sh.status == OK else None,
                change=ch.value, notional=f["window_notional_usd"].value or ZERO,
                trades=int(f["window_trade_count"].value or 0)))
        m += MINUTE
    return out


@dataclass(frozen=True)
class EntryRule:
    spike: Decimal
    share: Decimal
    notional: Decimal
    trades: int

    def accepts(self, s: Signal) -> bool:
        return (s.spike is not None and s.share is not None and s.spike >= self.spike
                and s.share >= self.share and s.notional >= self.notional
                and s.trades >= self.trades and s.change > ZERO)

    @property
    def label(self) -> str:
        return f"s≥{self.spike} b≥{self.share} n≥{int(self.notional) // 1000}k t≥{self.trades}"


# ---------------------------------------------------------------- exits

@dataclass(frozen=True)
class ExitRule:
    hold_minutes: int
    stop_pct: Decimal
    tp_r: Decimal | None

    @property
    def label(self) -> str:
        tp = "—" if self.tp_r is None else f"{self.tp_r}R"
        return f"H={self.hold_minutes}m stop={self.stop_pct}% tp={tp}"


@dataclass(frozen=True)
class Trade:
    iid: str
    at: datetime
    entry: Decimal
    exit: Decimal | None         # None when excluded (data gap)
    reason: str                  # STOP | TAKE_PROFIT | TIME | EXCLUDED
    closed_at: datetime

    @property
    def excluded(self) -> bool:
        return self.exit is None

    @property
    def gross(self) -> Decimal:
        return self.exit / self.entry - ONE  # type: ignore[operator]

    @property
    def net(self) -> Decimal:
        return (self.exit * (ONE - FEE)) / (self.entry * (ONE + FEE)) - ONE  # type: ignore[operator]


def simulate_exit(iid: str, bars: dict[datetime, Bar], at: datetime, ref_close: Decimal,
                  rule: ExitRule) -> Trade:
    """Entry at ref_close × (1 + slippage) at time `at`; minutes [at, at + H) decide the exit.
    Stop wins over take-profit within the same minute (pessimistic)."""
    entry = ref_close * (ONE + SLIPPAGE)
    stop = entry * (ONE - rule.stop_pct / HUNDRED)
    tp = None if rule.tp_r is None else entry * (ONE + rule.tp_r * rule.stop_pct / HUNDRED)
    last = ref_close
    for j in range(rule.hold_minutes):
        t = at + MINUTE * j
        bar = bars.get(t)
        if bar is None:
            return Trade(iid, at, entry, None, "EXCLUDED", at + MINUTE * rule.hold_minutes)
        if bar.low is not None and bar.low <= stop:
            px = bar.open if bar.open is not None and bar.open < stop else stop
            return Trade(iid, at, entry, px * (ONE - SLIPPAGE), "STOP", t + MINUTE)
        if tp is not None and bar.high is not None and bar.high >= tp:
            px = bar.open if bar.open is not None and bar.open > tp else tp
            return Trade(iid, at, entry, px, "TAKE_PROFIT", t + MINUTE)
        if bar.close is not None:
            last = bar.close
    return Trade(iid, at, entry, last, "TIME", at + MINUTE * rule.hold_minutes)


def run_pair(iid: str, bars: dict[datetime, Bar], signals: Iterable[Signal], rule: ExitRule,
             end: datetime) -> list[Trade]:
    """One position at a time per pair, cooldown after exit; holds must end before `end`."""
    trades: list[Trade] = []
    free_at: datetime | None = None
    for s in signals:
        if free_at is not None and s.at < free_at:
            continue
        if s.at + MINUTE * rule.hold_minutes > end:
            break
        tr = simulate_exit(iid, bars, s.at, s.ref_close, rule)
        trades.append(tr)
        free_at = tr.closed_at + COOLDOWN
    return trades


# ---------------------------------------------------------------- stats

@dataclass
class Stats:
    trades: int = 0
    excluded: int = 0
    wins: int = 0
    gross_sum: Decimal = ZERO
    net_sum: Decimal = ZERO
    net_pos: Decimal = ZERO
    net_neg: Decimal = ZERO
    exits: dict[str, int] = field(default_factory=dict)

    @classmethod
    def of(cls, trades: Iterable[Trade]) -> Stats:
        st = cls()
        for t in trades:
            if t.excluded:
                st.excluded += 1
                continue
            st.trades += 1
            n = t.net
            st.gross_sum += t.gross
            st.net_sum += n
            if n > ZERO:
                st.wins += 1
                st.net_pos += n
            else:
                st.net_neg += n
            st.exits[t.reason] = st.exits.get(t.reason, 0) + 1
        return st

    @property
    def mean_net(self) -> Decimal | None:
        return self.net_sum / self.trades if self.trades else None

    @property
    def mean_gross(self) -> Decimal | None:
        return self.gross_sum / self.trades if self.trades else None

    @property
    def profit_factor(self) -> Decimal | None:
        return self.net_pos / -self.net_neg if self.net_neg < ZERO else None

    def as_json(self) -> dict[str, Any]:
        def pct(v: Decimal | None) -> str | None:
            return None if v is None else f"{v * HUNDRED:.3f}"
        return {"trades": self.trades, "excluded": self.excluded, "wins": self.wins,
                "win_rate_pct": None if not self.trades else f"{D(self.wins) / self.trades * HUNDRED:.1f}",
                "mean_gross_pct": pct(self.mean_gross), "mean_net_pct": pct(self.mean_net),
                "profit_factor": None if self.profit_factor is None else f"{self.profit_factor:.2f}",
                "exits": dict(sorted(self.exits.items()))}


def split_at(start: datetime, end: datetime) -> datetime:
    return start + (end - start) * 3 / 5      # SPLIT = 60 %


@dataclass
class Row:
    entry: EntryRule | None      # None = baseline (no entry filter)
    exit: ExitRule
    first: Stats
    second: Stats
    baseline_first: Stats | None = None
    baseline_second: Stats | None = None

    @property
    def label(self) -> str:
        return f"{'référence' if self.entry is None else self.entry.label} | {self.exit.label}"

    def as_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"variant": self.label, "first_part": self.first.as_json(),
                               "second_part": self.second.as_json()}
        if self.baseline_first is not None and self.baseline_second is not None:
            out["baseline_first_part"] = self.baseline_first.as_json()
            out["baseline_second_part"] = self.baseline_second.as_json()
        return out


def _split(trades: list[Trade], cut: datetime) -> tuple[Stats, Stats]:
    return Stats.of(t for t in trades if t.at < cut), Stats.of(t for t in trades if t.at >= cut)


def rank(rows: list[Row], top: int = TOP, min_trades: int = MIN_RANK_TRADES) -> list[Row]:
    """Ranked on the FIRST part only (mean net per trade); the second part is reported, not used."""
    eligible = [r for r in rows if r.first.trades >= min_trades and r.first.mean_net is not None]
    eligible.sort(key=lambda r: r.first.mean_net, reverse=True)  # type: ignore[arg-type, return-value]
    return eligible[:top]


V020_ENTRY = EntryRule(D(4), D("0.60"), D(50000), 40)
V020_EXIT = ExitRule(60, D(3), D(2))


@dataclass
class StudyReport:
    start: datetime
    cut: datetime
    end: datetime
    pairs: int
    evaluable_windows: int
    variants_tested: int
    current: Row
    top: list[Row]
    baseline: Row

    def as_json(self) -> dict[str, Any]:
        return {"period": {"start": self.start.isoformat(), "split": self.cut.isoformat(),
                           "end": self.end.isoformat()},
                "pairs": self.pairs, "evaluable_windows": self.evaluable_windows,
                "variants_tested": self.variants_tested,
                "assumptions": {"fee_pct_each_side": "0.40", "slippage_pct": "0.10",
                                "cooldown_minutes": 30, "spread": "not replayed (assumed pass)"},
                "v0_2_0": self.current.as_json(), "baseline_v0_2_0_exits": self.baseline.as_json(),
                "top": [r.as_json() for r in self.top]}

    def as_text(self) -> str:
        def line(name: str, s: Stats) -> str:
            j = s.as_json()
            return (f"    {name:<10} trades {j['trades']:>5}  exclus {j['excluded']:>4}  "
                    f"gain {j['win_rate_pct'] or '—':>5}%  brut {j['mean_gross_pct'] or '—':>7}%  "
                    f"net {j['mean_net_pct'] or '—':>7}%  PF {j['profit_factor'] or '—':>5}  "
                    f"{j['exits']}")

        def block(r: Row) -> list[str]:
            out = [f"  {r.label}", line("partie 1", r.first), line("partie 2", r.second)]
            if r.baseline_second is not None:
                out.append(line("réf. p2", r.baseline_second))
            return out

        lines = ["0-hl — étude STRAT-01 hors production (09-recherche/03, lecture seule)",
                 f"période {self.start:%Y-%m-%d %H:%M} → {self.end:%Y-%m-%d %H:%M} UTC, "
                 f"coupure {self.cut:%Y-%m-%d %H:%M} (60 % / 40 %)",
                 f"{self.pairs} paires, {self.evaluable_windows} fenêtres évaluables, "
                 f"{self.variants_tested} variantes testées",
                 "hypothèses : frais 0,40 % par côté, glissement 0,10 % (entrée et stop), "
                 "spread non rejoué, résultats en % par trade",
                 "", "STRAT-01 v0.2.0 :", *block(self.current),
                 "", "Référence (aucun filtre d'entrée, sorties v0.2.0) :", *block(self.baseline),
                 "", f"Top {len(self.top)} (classées sur la partie 1, ≥ {MIN_RANK_TRADES} trades ; "
                 "juger sur la partie 2) :"]
        for i, r in enumerate(self.top, 1):
            lines.append(f" #{i}")
            lines += block(r)
        if not self.top:
            lines.append("  aucune variante n'a assez de trades sur la partie 1")
        lines += ["", "Limites : quelques jours, un seul régime de marché, pas de carnet historique, "
                  "nombreuses variantes (risque de faux positif). Aucune publication sans "
                  "validation de l'opérateur (ADR-000, ADR-020)."]
        return "\n".join(lines)


def run_study(series: dict[str, PairData], start: datetime, end: datetime) -> StudyReport:
    cut = split_at(start, end)
    signals = {iid: compute_signals(pd.minutes, start, end) for iid, pd in series.items()}
    entries = [EntryRule(s, b, n, t) for s, b, (n, t) in itertools.product(SPIKES, BUY_SHARES, ACTIVITY)]
    exits = [ExitRule(h, x, k) for h, x, k in itertools.product(HOLDS, STOPS, TPS)]

    baselines: dict[ExitRule, tuple[Stats, Stats]] = {}
    for ex in exits:
        trades = [t for iid, pd in series.items() for t in run_pair(iid, pd.bars, signals[iid], ex, end)]
        baselines[ex] = _split(trades, cut)

    rows: list[Row] = []
    current: Row | None = None
    for en in entries:
        filtered = {iid: [s for s in sigs if en.accepts(s)] for iid, sigs in signals.items()}
        for ex in exits:
            trades = [t for iid, pd in series.items()
                      for t in run_pair(iid, pd.bars, filtered[iid], ex, end)]
            b1, b2 = baselines[ex]
            row = Row(en, ex, *_split(trades, cut), baseline_first=b1, baseline_second=b2)
            rows.append(row)
            if en == V020_ENTRY and ex == V020_EXIT:
                current = row
    assert current is not None
    b1, b2 = baselines[V020_EXIT]
    return StudyReport(start=start, cut=cut, end=end, pairs=len(series),
                       evaluable_windows=sum(len(s) for s in signals.values()),
                       variants_tested=len(rows), current=current, top=rank(rows),
                       baseline=Row(None, V020_EXIT, b1, b2))


async def build_study(sessions: async_sessionmaker[AsyncSession], *, days: float = 7.0,
                      now: datetime | None = None) -> StudyReport:
    end = (now or utcnow()).replace(second=0, microsecond=0)
    since = end - timedelta(days=days)
    async with sessions() as db:
        series = await load_series(db, since, end)
        await db.rollback()
    first = min((m for pd in series.values() for m in pd.minutes), default=since)
    # the first hour only feeds the baseline windows of the volume spike
    start = max(since, first) + MINUTE * WINDOW_MINUTES * 13
    return run_study(series, start, end)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="0-hl — étude STRAT-01 hors production (lecture seule)")
    parser.add_argument("--days", type=float, default=7.0)
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
    print(json.dumps(report.as_json(), indent=2, ensure_ascii=False) if args.json else report.as_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
