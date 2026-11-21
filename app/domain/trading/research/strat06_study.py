"""Offline STRAT-06 « Rebond après excès » study — read-only (spec 09-recherche/06-etude-strat06.md,
ADR-024).

Usage (production): docker exec 0-hl_worker python -m app.domain.trading.research.strat06_study
[--days 30] [--json]. Reads `hl_trade_minute` and `hl_instrument` only; writes nothing.

Same costs, split, ranking, report and launch criteria as the STRAT-02 study (09-recherche/05).
Entry signals and the « back to the mean » exit are computed by the SCANNER code, reading only
minutes before the decision minute.
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
from typing import Any, Callable, Iterable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.trading.agents.features import (
    BTC_IID,
    DAY_MINUTES,
    MINUTE,
    OK,
    MinuteData,
    change_feature,
    gap_to_mean_feature,
    scanner_features,
)
from app.domain.trading.research.strat01_study import (
    SLIPPAGE,
    Bar,
    PairData,
    Trade,
    load_series,
    split_at,
)
from app.domain.trading.research.strat02_study import Row, StudyReport, rank
from app.domain.trading.types import utcnow

D = Decimal
ZERO = D(0)
ONE = D(1)
HUNDRED = D(100)
COOLDOWN = timedelta(hours=6)

# fixed filters (05-strategies/02 « STRAT-06 — Entrée v0.1.0 »)
MIN_NOTIONAL = D(25000)
MIN_TREND_24H = D("-0.10")
BTC_FLOOR = D("-0.015")

# grid (09-recherche/06 « Grille », 324 variants)
DROPS = (D("0.03"), D("0.04"), D("0.06"))
SPIKES = (D("1.5"), D(2), D(3))
BTC_FILTER = (True, False)
STOPS = (D(3), D(4), D(6))
MODES: tuple[Decimal | None, ...] = (None, D("1.5"), D(2))   # None = back to the 4 h mean
HOLDS = (360, 720)


# ---------------------------------------------------------------- signals

@dataclass(frozen=True)
class Signal:
    at: datetime
    ref_close: Decimal
    spike: Decimal | None
    notional: Decimal
    change_5m: Decimal | None
    drop: Decimal
    trend_24h: Decimal | None
    btc_change_3h: Decimal | None


def btc_changes(series: dict[str, PairData], start: datetime, end: datetime) -> dict[datetime, Decimal]:
    """btc_change_3h at every minute (market feature, same code as the SCANNER)."""
    btc = series.get(BTC_IID)
    out: dict[datetime, Decimal] = {}
    if btc is None:
        return out
    m = start
    while m <= end:
        f = change_feature("btc_change_3h", btc.minutes, m, 180)
        if f.status == OK and f.value is not None:
            out[m] = f.value
        m += MINUTE
    return out


def compute_signals(minutes: dict[datetime, MinuteData], start: datetime, end: datetime,
                    btc: dict[datetime, Decimal]) -> list[Signal]:
    """Every evaluable minute m: complete live window and known drop from the 3 h high."""
    out: list[Signal] = []
    m = start
    while m <= end:
        f = scanner_features(minutes, m)
        dr, n = f["drop_from_high_3h"], f["window_notional_usd"]
        if dr.status == OK and n.status == OK:
            sp, ch, tr = f["volume_spike"], f["price_change_5m"], f["trend_24h"]
            out.append(Signal(
                at=m, ref_close=minutes[m - MINUTE].close,  # type: ignore[arg-type]
                spike=sp.value if sp.status == OK else None, notional=n.value,  # type: ignore[arg-type]
                change_5m=ch.value if ch.status == OK else None, drop=dr.value,  # type: ignore[arg-type]
                trend_24h=tr.value if tr.status == OK else None, btc_change_3h=btc.get(m)))
        m += MINUTE
    return out


@dataclass(frozen=True)
class EntryRule:
    drop: Decimal
    spike: Decimal
    btc_filter: bool

    def accepts(self, s: Signal) -> bool:
        if s.spike is None or s.spike < self.spike or s.notional < MIN_NOTIONAL:
            return False
        if s.drop > -self.drop or s.change_5m is None or s.change_5m <= ZERO:
            return False
        if s.trend_24h is None or s.trend_24h < MIN_TREND_24H:
            return False
        return not self.btc_filter or (s.btc_change_3h is not None and s.btc_change_3h >= BTC_FLOOR)

    @property
    def label(self) -> str:
        return f"chute≥{self.drop * HUNDRED}% v≥{self.spike} filtre BTC={'oui' if self.btc_filter else 'non'}"


# ---------------------------------------------------------------- exits

@dataclass(frozen=True)
class ExitRule:
    hold_minutes: int
    stop_pct: Decimal
    tp_r: Decimal | None          # None = back to the 4 h mean (exit condition)

    @property
    def label(self) -> str:
        tp = "retour à la moyenne" if self.tp_r is None else f"tp={self.tp_r}R"
        return f"H={self.hold_minutes // 60}h stop={self.stop_pct}% {tp}"


class GapCache:
    """gap_to_mean_4h per (pair, minute), computed by the SCANNER code once and shared by variants."""

    def __init__(self, series: dict[str, PairData]) -> None:
        self.series = series
        self._c: dict[tuple[str, datetime], Decimal | None] = {}

    def __call__(self, iid: str, at: datetime) -> Decimal | None:
        key = (iid, at)
        if key not in self._c:
            f = gap_to_mean_feature(self.series[iid].minutes, at)
            self._c[key] = f.value if f.status == OK else None
        return self._c[key]


def simulate_exit(iid: str, bars: dict[datetime, Bar], at: datetime, ref_close: Decimal,
                  rule: ExitRule, gap: Callable[[str, datetime], Decimal | None]) -> Trade:
    """Stop first; then target (fixed R) or « back to the mean » checked at m' = t + 1 on minutes
    before m' (exit at the close of t); then time."""
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
        if tp is None:
            g = gap(iid, t + MINUTE)
            if g is not None and g >= ZERO:
                return Trade(iid, at, entry, last, "EXIT_CONDITION", t + MINUTE)
    return Trade(iid, at, entry, last, "TIME", at + MINUTE * rule.hold_minutes)


def run_pair(iid: str, bars: dict[datetime, Bar], signals: Iterable[Signal], rule: ExitRule,
             end: datetime, gap: Callable[[str, datetime], Decimal | None]) -> list[Trade]:
    trades: list[Trade] = []
    free_at: datetime | None = None
    for s in signals:
        if free_at is not None and s.at < free_at:
            continue
        if s.at + MINUTE * rule.hold_minutes > end:
            break
        tr = simulate_exit(iid, bars, s.at, s.ref_close, rule, gap)
        trades.append(tr)
        free_at = tr.closed_at + COOLDOWN
    return trades


V010_ENTRY = EntryRule(D("0.04"), D(2), True)
V010_EXIT = ExitRule(720, D(4), None)


def run_study(series: dict[str, PairData], start: datetime, end: datetime) -> StudyReport:
    cut = split_at(start, end)
    btc = btc_changes(series, start, end)
    signals = {iid: compute_signals(pd.minutes, start, end, btc) for iid, pd in series.items()}
    gap = GapCache(series)
    entries = [EntryRule(d, v, b) for d, v, b in itertools.product(DROPS, SPIKES, BTC_FILTER)]
    exits = [ExitRule(h, x, k) for x, k, h in itertools.product(STOPS, MODES, HOLDS)]

    def row(entry: EntryRule | None, ex: ExitRule, sigs: dict[str, list[Signal]]) -> Row:
        trades = [t for iid, pd in series.items()
                  for t in run_pair(iid, pd.bars, sigs[iid], ex, end, gap)]
        return Row(entry, ex, [t for t in trades if t.at < cut], [t for t in trades if t.at >= cut])  # type: ignore[arg-type]

    base_sigs = {iid: [s for s in sigs if s.at.minute == 0] for iid, sigs in signals.items()}
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
    return StudyReport(
        start=start, cut=cut, end=end, pairs=len(series),
        evaluable_minutes=sum(len(s) for s in signals.values()), variants_tested=len(rows),
        current=current, current_baseline=baselines[V010_EXIT], top=rank(rows), baselines=baselines,
        title="étude STRAT-06 « Rebond après excès » hors production (09-recherche/06, lecture seule)",
        current_name="STRAT-06 v0.1.0", baseline_name="une entrée par paire et par heure, sorties v0.1.0",
        cooldown_hours=6, adr="ADR-024")


async def build_study(sessions: async_sessionmaker[AsyncSession], *, days: float = 30.0,
                      now: datetime | None = None) -> StudyReport:
    end = (now or utcnow()).replace(second=0, microsecond=0)
    since = end - timedelta(days=days)
    async with sessions() as db:
        series = await load_series(db, since, end)
        await db.rollback()
    first = min((m for pd in series.values() for m in pd.minutes), default=since)
    # the first 24 h only feed trend_24h (an unknown trend refuses the entry anyway)
    start = max(since, first) + MINUTE * (DAY_MINUTES + 1)
    return run_study(series, start, end)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="0-hl — étude STRAT-06 hors production (lecture seule)")
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
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    out: Any = report.as_json() if args.json else report.as_text()
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str) if args.json else out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
