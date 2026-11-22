"""Market radar — spec 02-design/05 « Calcul (serveur) » and « API » (ADR-028).

x = pair return over the window minus BTC's (percentage points); y = log2(realised volatility of
the pair / BTC's) on one-minute log returns; plus, per active bot, what it concluded for each pair
at its last cycle. Read-only; shared by `/api/v1/public/radar` and `/api/v1/market/radar`.
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.trading.agents.features import BTC_IID
from app.domain.trading.enums import BotStatus, PositionStatus
from app.domain.trading.lifecycle import initial_capital
from app.domain.trading.models import (
    Bot, DomainEvent, EquitySnapshot, Instrument, Position, Strategy, StrategyVersion, TradeMinute,
)

MINUTE = timedelta(minutes=1)
WINDOWS = {"1h": 60, "4h": 240, "24h": 1440}
CACHE_SECONDS = {60: 15.0, 240: 15.0, 1440: 60.0}
SETTLE = timedelta(seconds=30)          # same settling delay as the engine (06-agents/03)
START_LOOKBACK = timedelta(hours=24)    # how far back the start close may be looked for
CYCLE_MAX_AGE = timedelta(minutes=5)
_cache: dict[tuple[int, bool], tuple[float, dict[str, Any]]] = {}


def window_end_for(now: datetime) -> datetime:
    """Exclusive end of the window: the start of the minute after the last settled one."""
    return (now - SETTLE).replace(second=0, microsecond=0)


def _q2(x: Decimal) -> str:
    return format(x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


def pair_metrics(closes: dict[datetime, Decimal], traded: set[datetime], start_close: Decimal | None,
                 start: datetime, minutes: int) -> dict[str, Any] | None:
    """Return, per-minute volatility and traded minutes on the carried-forward grid; None ⇒ no data."""
    if start_close is None or start_close <= 0:
        return None
    grid, last = [start_close], start_close
    for i in range(minutes):
        c = closes.get(start + MINUTE * i)
        if c is not None and c > 0:
            last = c
        grid.append(last)
    rets = [math.log(float(grid[i + 1] / grid[i])) for i in range(minutes)]
    mean = sum(rets) / len(rets)
    vol = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)) if len(rets) > 1 else 0.0
    return {"ret": grid[-1] / start_close - 1, "vol": vol,
            "minutes_traded": sum(1 for i in range(minutes) if start + MINUTE * i in traded)}


async def _minutes(db: AsyncSession, ids: list[str], start: datetime, end: datetime):
    """{iid: ({minute: close}, {traded minutes}, start close)}; live wins over backfill."""
    rows = (await db.execute(select(TradeMinute.instrument_id, TradeMinute.minute_start, TradeMinute.source,
                                    TradeMinute.close, TradeMinute.trade_count)
                             .where(TradeMinute.instrument_id.in_(ids),
                                    TradeMinute.minute_start >= start - timedelta(hours=1),
                                    TradeMinute.minute_start < end))).all()
    picked: dict[tuple[str, datetime], tuple[str, Decimal | None, int]] = {}
    for iid, m, src, close, n in rows:
        if (iid, m) in picked and picked[(iid, m)][0] == "live":
            continue
        picked[(iid, m)] = (src, close, n)
    out: dict[str, tuple[dict[datetime, Decimal], set[datetime], Decimal | None]] = {
        i: ({}, set(), None) for i in ids}
    before: dict[str, tuple[datetime, Decimal]] = {}
    for (iid, m), (_, close, n) in picked.items():
        closes, traded, _ = out[iid]
        if m >= start:
            if close is not None:
                closes[m] = close
            if n and close is not None:
                traded.add(m)
        elif close is not None and close > 0 and (iid not in before or m > before[iid][0]):
            before[iid] = (m, close)
    for iid in ids:
        start_close = before.get(iid, (None, None))[1]
        if start_close is None:  # look further back, at most 24 h
            start_close = await db.scalar(
                select(TradeMinute.close).where(
                    TradeMinute.instrument_id == iid, TradeMinute.close.is_not(None), TradeMinute.close > 0,
                    TradeMinute.minute_start < start - timedelta(hours=1),
                    TradeMinute.minute_start >= start - START_LOOKBACK)
                .order_by(TradeMinute.minute_start.desc()).limit(1))
        closes, traded, _ = out[iid]
        out[iid] = (closes, traded, start_close)
    return out


async def _bots(db: AsyncSession, now: datetime) -> list[dict[str, Any]]:
    bots = (await db.scalars(select(Bot).where(Bot.status.in_((BotStatus.RUNNING, BotStatus.PAUSED)))
                             .order_by(Bot.name))).all()
    deposits = await initial_capital(db)
    out = []
    for b in bots:
        sv = await db.get(StrategyVersion, b.strategy_version_id)
        strat = await db.get(Strategy, sv.strategy_id) if sv else None
        equity = await db.scalar(select(EquitySnapshot.equity).where(
            EquitySnapshot.bot_id == b.id, EquitySnapshot.equity.is_not(None))
            .order_by(EquitySnapshot.at.desc()).limit(1))
        dep = deposits.get(b.id)
        net = None if equity is None or not dep else _q2((equity - dep) / dep * 100)
        ev = await db.scalar(select(DomainEvent).where(
            DomainEvent.bot_id == b.id, DomainEvent.event_type == "SCAN_CYCLE_COMPLETED",
            DomainEvent.occurred_at >= now - CYCLE_MAX_AGE).order_by(DomainEvent.position.desc()).limit(1))
        cycle = None
        if ev is not None:
            p = ev.payload
            cycle = {"window_end": p.get("window_end"), "evaluated": p.get("evaluated"),
                     "signals": p.get("candidates"), "outcomes": p.get("outcomes") or {}}
        held = (await db.scalars(select(Position.instrument_id).where(
            Position.bot_id == b.id, Position.status.in_((PositionStatus.OPEN, PositionStatus.REDUCING))))).all()
        out.append({"name": b.name, "status": b.status.value,
                    "strategy": {"id": sv.strategy_id if sv else None, "version": sv.version if sv else None,
                                 "name": strat.name if strat else None},
                    "net_pct": net, "last_cycle": cycle,
                    "open_positions": sorted({i.split(":", 1)[1] for i in held})})
    return out


async def compute_radar(db: AsyncSession, now: datetime, window_minutes: int) -> dict[str, Any]:
    end = window_end_for(now)
    start = end - MINUTE * window_minutes
    universe = (await db.scalars(select(Instrument.id).where(Instrument.in_universe))).all()
    ids = sorted(set(universe) | {BTC_IID})
    data = await _minutes(db, ids, start, end)
    metrics = {iid: pair_metrics(*data[iid][:2], data[iid][2], start, window_minutes) for iid in ids}
    btc = metrics.get(BTC_IID)
    need = window_minutes / 3
    btc_ok = btc is not None and btc["vol"] > 0 and btc["minutes_traded"] >= need
    pairs, without = [], 0
    for iid in ids:
        if iid == BTC_IID:
            continue
        m = metrics[iid]
        if m is None:
            without += 1
            continue
        judgeable = btc_ok and m["minutes_traded"] >= need and m["vol"] > 0
        ratio = m["vol"] / btc["vol"] if btc_ok and btc is not None and m["vol"] > 0 else None
        pairs.append({
            "symbol": iid.split(":", 1)[1], "return_pct": _q2(m["ret"] * 100),
            "excess_pp": _q2((m["ret"] - btc["ret"]) * 100) if btc is not None else None,
            "vol_ratio": None if ratio is None else f"{ratio:.2f}",
            "y": f"{math.log2(ratio):.4f}" if judgeable and ratio else "0",  # string: the domain JSON refuses floats
            "minutes_traded": m["minutes_traded"], "judgeable": judgeable})
    return {
        "generated_at": now, "window_minutes": window_minutes, "window_end": end,
        "stale": not btc_ok,
        "reference": {"symbol": BTC_IID.split(":", 1)[1],
                      "return_pct": None if btc is None else _q2(btc["ret"] * 100),
                      "volatility_pct": None if btc is None else f"{btc['vol'] * 100:.4f}"},
        "pairs": pairs, "pairs_without_data": without,
        "bots": await _bots(db, now),
    }


async def cached_radar(db: AsyncSession, now: datetime, window_minutes: int) -> dict[str, Any]:
    """Per-process cache (15 s for 1h/4h, 60 s for 24h) so public traffic cannot load the database."""
    key = (window_minutes, True)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS[window_minutes]:
        return hit[1]
    data = await compute_radar(db, now, window_minutes)
    _cache[key] = (time.monotonic(), data)
    return data
