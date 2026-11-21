"""Offline STRAT-11 « Rotation des plus fortes » study — read-only (spec 09-recherche/07-etude-strat11.md,
ADR-024).

Usage (production): docker exec 0-hl_worker python -m app.domain.trading.research.strat11_study
[--days 60] [--json]. Reads `hl_trade_minute` and `hl_instrument` only; writes nothing.

The signal is cross-sectional: at every closed minute m, trend_24h of every pair, then the market
view (rank, relative strength, BTC regime) by the SCANNER code, reading only minutes before m. The
simulation is a portfolio of at most N positions. Costs, split, ranking and report are those of the
STRAT-02 study; launch criteria adapted in 09-recherche/07 (28 days, 15 trades).
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
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.trading.agents.features import (
    BTC_IID,
    DAY_MINUTES,
    MINUTE,
    OK,
    change_feature,
    market_view,
    window_features,
)
from app.domain.trading.research.strat01_study import (
    SLIPPAGE,
    ExitRule as BaseExit,
    PairData,
    Trade,
    load_series,
    simulate_exit,
    split_at,
)
from app.domain.trading.research.strat02_study import Row, StudyReport, rank
from app.domain.trading.types import utcnow

D = Decimal
ZERO = D(0)
ONE = D(1)
HUNDRED = D(100)
COOLDOWN = timedelta(hours=24)
MIN_NOTIONAL = D(25000)
REGIME_FLOOR = D("-0.03")
MIN_DAYS = 28
MIN_TRADES = 15

# grid (09-recherche/07 « Grille », 48 variants)
SLOTS = (2, 3, 5)
REGIME = (True, False)
STOPS = (D(6), D(10))
HOLDS = (2 * DAY_MINUTES, 4 * DAY_MINUTES)


@dataclass(frozen=True)
class Snap:
    rank: Decimal | None
    rs: Decimal | None
    notional: Decimal | None
    ref_close: Decimal


@dataclass(frozen=True)
class Market:
    btc_trend: Decimal | None
    pairs: dict[str, Snap]


def market_series(series: dict[str, PairData], start: datetime, end: datetime) -> dict[datetime, Market]:
    """The market view at every minute, by the SCANNER code (06-agents/03 « features de marché »)."""
    out: dict[datetime, Market] = {}
    btc = series.get(BTC_IID)
    m = start
    while m <= end:
        trends = {iid: change_feature("trend_24h", pd.minutes, m, DAY_MINUTES) for iid, pd in series.items()}
        btc_ch = (change_feature("btc_change_3h", btc.minutes, m, 180) if btc is not None
                  else change_feature("btc_change_3h", {}, m, 180))
        view = market_view(trends, btc_ch)
        pairs: dict[str, Snap] = {}
        for iid, pd in series.items():
            last = pd.minutes.get(m - MINUTE)
            if last is None or last.close is None or trends[iid].status != OK:
                continue
            f = view.for_pair(iid)
            n = window_features(pd.minutes, m)["window_notional_usd"]
            pairs[iid] = Snap(
                rank=f["rs_rank_24h"].value if f["rs_rank_24h"].status == OK else None,
                rs=f["rs_24h"].value if f["rs_24h"].status == OK else None,
                notional=n.value if n.status == OK else None, ref_close=last.close)
        bt = view.common["btc_trend_24h"]
        out[m] = Market(bt.value if bt.status == OK else None, pairs)
        m += MINUTE
    return out


@dataclass(frozen=True)
class EntryRule:
    slots: int
    regime: bool

    def accepts(self, mk: Market, s: Snap) -> bool:
        if s.rank is None or s.rank > self.slots or s.rs is None or s.rs <= ZERO:
            return False
        if s.notional is None or s.notional < MIN_NOTIONAL:
            return False
        return not self.regime or (mk.btc_trend is not None and mk.btc_trend >= REGIME_FLOOR)

    @property
    def label(self) -> str:
        return f"top {self.slots} régime={'oui' if self.regime else 'non'}"


@dataclass(frozen=True)
class ExitRule:
    hold_minutes: int
    stop_pct: Decimal
    exit_rank: int

    @property
    def label(self) -> str:
        return f"H={self.hold_minutes // DAY_MINUTES}j stop={self.stop_pct}% sortie rang>{self.exit_rank}"


@dataclass
class _Open:
    at: datetime
    entry: Decimal
    stop: Decimal
    last: Decimal


def run_portfolio(series: dict[str, PairData], market: dict[datetime, Market], start: datetime,
                  end: datetime, en: EntryRule, ex: ExitRule) -> list[Trade]:
    """At most `en.slots` positions; candidates by increasing rank (then id); one per pair, 24 h
    cooldown. At decision minute m the bar m − 1 has closed: stop first, then time, then the exit
    condition rank > M (an unknown rank never sells); holds must end before `end`."""
    trades: list[Trade] = []
    open_: dict[str, _Open] = {}
    free_at: dict[str, datetime] = {}
    m = start
    while m <= end:
        mk = market.get(m)
        for iid in sorted(open_):
            p = open_[iid]
            if m - MINUTE < p.at:
                continue
            bar = series[iid].bars.get(m - MINUTE)
            closed: Trade | None = None
            if bar is None:
                closed = Trade(iid, p.at, p.entry, None, "EXCLUDED", m)
            elif bar.low is not None and bar.low <= p.stop:
                px = bar.open if bar.open is not None and bar.open < p.stop else p.stop
                closed = Trade(iid, p.at, p.entry, px * (ONE - SLIPPAGE), "STOP", m)
            else:
                if bar.close is not None:
                    p.last = bar.close
                snap = mk.pairs.get(iid) if mk else None
                if m - p.at >= MINUTE * ex.hold_minutes:
                    closed = Trade(iid, p.at, p.entry, p.last, "TIME", m)
                elif snap is not None and snap.rank is not None and snap.rank > ex.exit_rank:
                    closed = Trade(iid, p.at, p.entry, p.last, "EXIT_CONDITION", m)
            if closed is not None:
                trades.append(closed)
                del open_[iid]
                free_at[iid] = m + COOLDOWN
        if mk is not None and len(open_) < en.slots and m + MINUTE * ex.hold_minutes <= end:
            cands = sorted(((s.rank, iid) for iid, s in mk.pairs.items()
                            if iid not in open_ and not (iid in free_at and m < free_at[iid])
                            and en.accepts(mk, s)), key=lambda x: (x[0], x[1]))  # type: ignore[arg-type, return-value]
            for _, iid in cands[: en.slots - len(open_)]:
                entry = mk.pairs[iid].ref_close * (ONE + SLIPPAGE)
                open_[iid] = _Open(m, entry, entry * (ONE - ex.stop_pct / HUNDRED), mk.pairs[iid].ref_close)
        m += MINUTE
    return trades


def baseline(series: dict[str, PairData], market: dict[datetime, Market], start: datetime,
             end: datetime, stop: Decimal, hold: int) -> list[Trade]:
    """Every rankable pair bought each day at 00:00 UTC, same stop and hold, no exit condition."""
    rule = BaseExit(hold, stop, None)
    trades: list[Trade] = []
    for iid, pd in series.items():
        busy_until: datetime | None = None
        for m in sorted(t for t in market if t.hour == 0 and t.minute == 0):
            s = market[m].pairs.get(iid)
            if s is None or (busy_until is not None and m < busy_until) or m + MINUTE * hold > end:
                continue
            tr = simulate_exit(iid, pd.bars, m, s.ref_close, rule)
            trades.append(tr)
            busy_until = tr.closed_at
    return trades


V010_ENTRY = EntryRule(3, True)
V010_EXIT = ExitRule(4 * DAY_MINUTES, D(10), 8)


def run_study(series: dict[str, PairData], start: datetime, end: datetime,
              days_of_data: float) -> StudyReport:
    cut = split_at(start, end)
    market = market_series(series, start, end)
    entries = [EntryRule(n, r) for n, r in itertools.product(SLOTS, REGIME)]

    def split(entry: Any, ex: Any, trades: list[Trade]) -> Row:
        return Row(entry, ex, [t for t in trades if t.at < cut], [t for t in trades if t.at >= cut])

    base_cache: dict[tuple[Decimal, int], list[Trade]] = {}
    rows: list[Row] = []
    baselines: dict[ExitRule, Row] = {}
    current: Row | None = None
    for en in entries:
        for x, h, exit_rank in itertools.product(STOPS, HOLDS, sorted({en.slots + 3, 10})):
            ex = ExitRule(h, x, exit_rank)
            if (x, h) not in base_cache:
                base_cache[(x, h)] = baseline(series, market, start, end, x, h)
            baselines[ex] = split(None, ex, base_cache[(x, h)])
            r = split(en, ex, run_portfolio(series, market, start, end, en, ex))
            rows.append(r)
            if en == V010_ENTRY and ex == V010_EXIT:
                current = r
    if current is None:          # v0.1.0 exit rank 8 is not in the grid for N = 3 (N + 3 = 6)
        current = split(V010_ENTRY, V010_EXIT, run_portfolio(series, market, start, end, V010_ENTRY, V010_EXIT))
        baselines[V010_EXIT] = split(None, V010_EXIT, base_cache.get((V010_EXIT.stop_pct, V010_EXIT.hold_minutes))
                                     or baseline(series, market, start, end, V010_EXIT.stop_pct, V010_EXIT.hold_minutes))
    not_judgeable = (None if days_of_data >= MIN_DAYS
                     else f"{days_of_data:.1f} jours de données, {MIN_DAYS} requis (09-recherche/07)")
    return StudyReport(
        start=start, cut=cut, end=end, pairs=len(series),
        evaluable_minutes=sum(len(mk.pairs) for mk in market.values()), variants_tested=len(rows),
        current=current, current_baseline=baselines[V010_EXIT], top=rank(rows), baselines=baselines,
        title="étude STRAT-11 « Rotation des plus fortes » hors production (09-recherche/07, lecture seule)",
        current_name="STRAT-11 v0.1.0",
        baseline_name="toutes les paires classables achetées chaque jour à 00:00 UTC, mêmes stop et durée",
        cooldown_hours=24, min_trades=MIN_TRADES, not_judgeable=not_judgeable, adr="ADR-024")


async def build_study(sessions: async_sessionmaker[AsyncSession], *, days: float = 60.0,
                      now: datetime | None = None) -> StudyReport:
    end = (now or utcnow()).replace(second=0, microsecond=0)
    since = end - timedelta(days=days)
    async with sessions() as db:
        series = await load_series(db, since, end)
        await db.rollback()
    first = min((m for pd in series.values() for m in pd.minutes), default=since)
    origin = max(since, first)
    start = origin + MINUTE * (DAY_MINUTES + 1)   # the first 24 h only feed trend_24h
    return run_study(series, start, end, (end - origin).total_seconds() / 86400)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="0-hl — étude STRAT-11 hors production (lecture seule)")
    parser.add_argument("--days", type=float, default=60.0)
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
    print(json.dumps(report.as_json(), indent=2, ensure_ascii=False, default=str)
          if args.json else report.as_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
