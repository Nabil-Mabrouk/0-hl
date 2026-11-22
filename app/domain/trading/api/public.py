"""Public landing statistics — UI-00 (spec 03-ecrans/UI-00-accueil-public.md « API publique »).

No authentication. `/stats`: aggregates only, cached 30 s per process. `/radar` (ADR-028,
02-design/05): per active bot its name, strategy, last-cycle outcomes, pairs held and net result
in % — never an absolute amount, internal id or user data; cached 15 s.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.domain.trading.api.common import DomainRoute, ok
from app.domain.trading.api.snapshot import worker_alive
from app.domain.trading import learn
from app.domain.trading.errors import NotFound
from app.domain.trading.market.radar import cached_radar
from app.domain.trading.enums import BotStatus, PositionStatus
from app.domain.trading.market.service import CONNECTION_FEED_ID
from app.domain.trading.models import Bot, DomainEvent, FeedStatus, Position, TradeMinute
from app.domain.trading.types import utcnow

router = APIRouter(prefix="/api/v1/public", tags=["0-hl public"], route_class=DomainRoute)
DB = Annotated[AsyncSession, Depends(get_db)]
CACHE_SECONDS = 30.0
_cache: dict[str, Any] = {"at": 0.0, "data": None}


async def public_stats(db: AsyncSession, now: datetime) -> dict[str, Any]:
    day = now - timedelta(hours=24)
    count = lambda stmt: db.scalar(select(func.count()).select_from(stmt.subquery()))  # noqa: E731
    bots_total = await db.scalar(select(func.count()).select_from(Bot)) or 0
    bots_running = await db.scalar(select(func.count()).select_from(Bot).where(Bot.status == BotStatus.RUNNING)) or 0
    closed = select(Position.id).where(Position.status == PositionStatus.CLOSED)
    trades_total = await count(closed) or 0
    trades_24h = await count(closed.where(Position.closed_at >= day)) or 0
    open_positions = await count(select(Position.id).where(Position.status == PositionStatus.OPEN)) or 0
    feeds = (await db.scalars(select(FeedStatus).where(FeedStatus.instrument_id != CONNECTION_FEED_ID))).all()
    subscribed = [f for f in feeds if not (f.state == "UNKNOWN" and f.reason == "NOT_SUBSCRIBED")]
    evaluations = (await db.scalars(select(DomainEvent.payload).where(
        DomainEvent.event_type == "SCAN_CYCLE_COMPLETED", DomainEvent.occurred_at >= day))).all()
    first_minute = await db.scalar(select(func.min(TradeMinute.minute_start)))
    last_trade = await db.scalar(select(func.max(Position.closed_at)).where(Position.status == PositionStatus.CLOSED))
    return {
        "bots_total": bots_total, "bots_running": bots_running,
        "trades_total": trades_total, "trades_24h": trades_24h, "open_positions": open_positions,
        "pairs_live": sum(1 for f in subscribed if f.state == "LIVE"), "pairs_subscribed": len(subscribed),
        "worker_alive": await worker_alive(db, now),
        "evaluations_24h": sum(int(p.get("evaluated") or 0) for p in evaluations),
        "market_data_days": None if first_minute is None else
        (Decimal(int((now - first_minute).total_seconds())) / Decimal(86400)).quantize(Decimal("0.1")),
        "last_trade_at": last_trade, "generated_at": now,
    }


@router.get("/stats")
async def stats(db: DB):
    if _cache["data"] is None or time.monotonic() - _cache["at"] > CACHE_SECONDS:
        _cache["data"] = await public_stats(db, utcnow())
        _cache["at"] = time.monotonic()
    return ok(_cache["data"], headers={"Cache-Control": f"public, max-age={int(CACHE_SECONDS)}"})


@router.get("/radar")
async def radar(db: DB):
    """Market radar of the landing page (02-design/05): 1h window only, no authentication."""
    return ok(await cached_radar(db, utcnow(), 60), headers={"Cache-Control": "public, max-age=15"})


@router.get("/strategies")
async def strategies(db: DB):
    """Learn (UI-17): strategies with a published version and their paper bot, in % only."""
    items = await learn.cached("list", lambda: learn.list_strategies(db))
    return ok({"items": items}, headers={"Cache-Control": "public, max-age=60"})


@router.get("/strategies/{strategy_id}")
async def strategy(strategy_id: str, db: DB):
    """Learn (UI-17): live rules, results, latest trades and versions of one strategy."""
    data = await learn.cached(f"one:{strategy_id}", lambda: learn.strategy_detail(db, strategy_id, utcnow()))
    if data is None:
        raise NotFound(f"unknown strategy {strategy_id}")
    return ok(data, headers={"Cache-Control": "public, max-age=60"})
