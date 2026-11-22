"""REST routes /api/v1 (spec 10-backend/04 « Routes /api/v1 »). Read-only except bot commands."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.models import User
from app.domain.trading.agents.commands import active_session, create_command
from app.domain.trading.api.common import ALL_ROLES, DomainRoute, event_json, ok, parse_uuid
from app.domain.trading.api.snapshot import (
    OPEN,
    bot_json,
    build_snapshot,
    feed_summary,
    position_json,
    session_json,
)
from app.domain.trading.audit import record_audit
from app.domain.trading.enums import CommandType, PositionStatus, TradingRole
from app.domain.trading.errors import NotFound, PermissionDenied, StateConflict
from app.domain.trading.models import (
    Bot,
    Command,
    DomainEvent,
    EquitySnapshot,
    FeedStatus,
    Fill,
    Instrument,
    Order,
    OrderIntent,
    Position,
    RiskPolicyVersion,
    StrategyVersion,
)
from app.domain.trading.rbac import TradingPrincipal, require_trading_roles
from app.domain.trading.types import utcnow

router = APIRouter(prefix="/api/v1", tags=["0-hl"], route_class=DomainRoute)

Reader = Annotated[TradingPrincipal, Depends(require_trading_roles(*ALL_ROLES))]
Commander = Annotated[TradingPrincipal, Depends(require_trading_roles(
    TradingRole.OPERATOR, TradingRole.RISK_OFFICER, audit_action="BOT_COMMAND"))]
DB = Annotated[AsyncSession, Depends(get_db)]


async def _bot(db: AsyncSession, bot_id: str) -> Bot:
    bot = await db.get(Bot, parse_uuid(bot_id, "bot"))
    if bot is None:
        raise NotFound(f"unknown bot {bot_id}")
    return bot


@router.get("/me")
async def me(p: Reader, db: DB):
    user = await db.get(User, p.user_id)
    return ok({"user_id": p.user_id, "email": user.email if user else None,
               "roles": sorted(r.value for r in p.roles)})


@router.get("/market/radar")
async def market_radar(p: Reader, db: DB, window: str = Query("1h", pattern="^(1h|4h|24h)$")):
    """Market radar of the Terminal (02-design/05): 1h, 4h or 24h window."""
    from app.domain.trading.market.radar import WINDOWS, cached_radar
    return ok(await cached_radar(db, utcnow(), WINDOWS[window]))


@router.get("/health")
async def health(p: Reader, db: DB):
    now = utcnow()
    bots = (await db.scalars(select(Bot).order_by(Bot.name))).all()
    scan = await db.scalar(select(DomainEvent).where(
        DomainEvent.event_type == "SCAN_CYCLE_COMPLETED").order_by(DomainEvent.position.desc()).limit(1))
    feed = await feed_summary(db, now)
    return ok({"status": "ok" if feed["worker_alive"] else "degraded", "checked_at": now,
               "feed": feed,
               "bots": [{"id": b.id, "name": b.name, "status": b.status.value,
                         "environment": b.environment.value} for b in bots],
               "last_scan": None if scan is None else scan.payload | {"occurred_at": scan.occurred_at}})


@router.get("/bots")
async def list_bots(p: Reader, db: DB):
    from app.domain.trading.lifecycle import initial_capital
    bots = (await db.scalars(select(Bot).order_by(Bot.name))).all()
    capital = await initial_capital(db)
    sv = {v.id: v for v in (await db.scalars(select(StrategyVersion))).all()}
    pv = {v.id: v for v in (await db.scalars(select(RiskPolicyVersion))).all()}
    return ok({"items": [bot_json(b) | {
        "capital_usd": capital.get(b.id),
        "strategy": {"strategy_id": sv[b.strategy_version_id].strategy_id, "version": sv[b.strategy_version_id].version},
        "risk_policy": {"policy_id": pv[b.risk_policy_version_id].policy_id, "version": pv[b.risk_policy_version_id].version},
    } for b in bots]})


@router.get("/bots/{bot_id}")
async def get_bot(bot_id: str, p: Reader, db: DB):
    bot = await _bot(db, bot_id)
    sv = await db.get(StrategyVersion, bot.strategy_version_id)
    pv = await db.get(RiskPolicyVersion, bot.risk_policy_version_id)
    body = bot_json(bot) | {
        "session": session_json(await active_session(db, bot.id), utcnow()),
        "strategy": {"strategy_id": sv.strategy_id, "version": sv.version,
                     "config_sha256": sv.config_sha256},
        "risk_policy": {"policy_id": pv.policy_id, "version": pv.version,
                        "config_sha256": pv.config_sha256},
    }
    return ok(body, headers={"ETag": f'"{bot.row_version}"'})


class CommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: CommandType
    reason: str | None = Field(default=None, max_length=500)


def command_json(c: Command) -> dict:
    return {"command_id": c.id, "bot_id": c.bot_id, "command": c.command.value,
            "status": c.status.value, "rejection_code": c.rejection_code, "actor": c.actor,
            "reason": c.reason, "requested_at": c.requested_at, "processed_at": c.processed_at,
            "result": c.result}


@router.post("/bots/{bot_id}/commands")
async def send_command(
    bot_id: str, body: CommandRequest, p: Commander, db: DB,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
):
    bot = await _bot(db, bot_id)
    allowed = p.has_any(TradingRole.OPERATOR) or (
        body.command is CommandType.EMERGENCY_STOP and p.has_any(TradingRole.RISK_OFFICER))
    if not allowed:
        await record_audit(db, actor=p.actor, action="BOT_COMMAND_DENIED", entity_type="bot",
                           entity_id=bot.id, diff={"command": body.command.value})
        await db.commit()
        raise PermissionDenied(f"{body.command.value} requires OPERATOR")
    key = f"api:{p.user_id}:{idempotency_key}"
    existing = await db.scalar(select(Command).where(Command.idempotency_key == key))
    if existing is not None:
        if existing.bot_id != bot.id or existing.command is not body.command:
            raise StateConflict("Idempotency-Key already used for a different command")
        return ok(command_json(existing), status=202)
    cmd = await create_command(db, bot_id=bot.id, command=body.command, actor=p.actor,
                               idempotency_key=key, reason=body.reason)
    await record_audit(db, actor=p.actor, action="BOT_COMMAND_REQUESTED", entity_type="bot",
                       entity_id=bot.id, reason=body.reason,
                       diff={"command": body.command.value, "command_id": cmd.id})
    await db.commit()
    return ok(command_json(cmd), status=202)


@router.get("/commands/{command_id}")
async def get_command(command_id: str, p: Reader, db: DB):
    cmd = await db.get(Command, parse_uuid(command_id, "command"))
    if cmd is None:
        raise NotFound(f"unknown command {command_id}")
    return ok(command_json(cmd))


@router.get("/strategies")
async def strategies(p: Reader, db: DB):
    rows = (await db.scalars(select(StrategyVersion).order_by(
        StrategyVersion.strategy_id, StrategyVersion.created_at))).all()
    from app.domain.trading.api.versions import strategy_json
    from app.domain.trading.lifecycle import bots_bound_to
    bound = await bots_bound_to(db)
    return ok({"items": [strategy_json(v, bound) for v in rows]})


@router.get("/risk-policies")
async def risk_policies(p: Reader, db: DB):
    rows = (await db.scalars(select(RiskPolicyVersion).order_by(
        RiskPolicyVersion.policy_id, RiskPolicyVersion.created_at))).all()
    from app.domain.trading.api.versions import policy_json
    from app.domain.trading.lifecycle import bots_bound_to
    bound = await bots_bound_to(db)
    return ok({"items": [policy_json(v, bound) for v in rows]})


@router.get("/terminal/snapshot")
async def terminal_snapshot(p: Reader, db: DB, bot_id: str = Query(...)):
    return ok(await build_snapshot(db, parse_uuid(bot_id, "bot"), utcnow()))


@router.get("/positions")
async def positions(p: Reader, db: DB, bot_id: str = Query(...),
                    status: str | None = Query(default=None, pattern="^(OPEN|CLOSED|ALL)$")):
    bot = await _bot(db, bot_id)
    stmt = select(Position).where(Position.bot_id == bot.id)
    if status == "OPEN" or status is None:
        stmt = stmt.where(Position.status.in_(OPEN))
    elif status == "CLOSED":
        stmt = stmt.where(Position.status == PositionStatus.CLOSED)
    rows = (await db.scalars(stmt.order_by(Position.opened_at.desc()).limit(500))).all()
    return ok({"items": [position_json(x) for x in rows]})


class TradeFilters:
    """Query filters shared by /trades, /trades.csv and /stats (M2.5, 03-ecrans/M2-trades-et-statistiques)."""

    def __init__(self, bot_id: str | None = Query(None), since: datetime | None = Query(None),
                 until: datetime | None = Query(None), instrument_id: str | None = Query(None),
                 exit_reason: str | None = Query(None), strategy_version_id: str | None = Query(None)):
        self.bot_id, self.since, self.until = bot_id, since, until
        self.instrument_id, self.exit_reason, self.strategy_version_id = instrument_id, exit_reason, strategy_version_id

    async def rows(self, db: AsyncSession):
        from app.domain.trading.analytics import trade_rows
        bot = await _bot(db, self.bot_id) if self.bot_id else None
        svid = parse_uuid(self.strategy_version_id, "strategy version") if self.strategy_version_id else None
        return await trade_rows(db, bot_id=bot.id if bot else None, since=self.since, until=self.until,
                                instrument_id=self.instrument_id, exit_reason=self.exit_reason,
                                strategy_version_id=svid)


Filters = Annotated[TradeFilters, Depends()]


@router.get("/trades")
async def trades(p: Reader, db: DB, f: Filters, limit: int = Query(50, ge=1, le=500),
                 offset: int = Query(0, ge=0)):
    rows = await f.rows(db)
    page = rows[offset:offset + limit]
    return ok({"items": [position_json(r.position) | r.extras() for r in page], "total": len(rows),
               "offset": offset, "limit": limit})


@router.get("/trades.csv")
async def trades_csv_route(p: Reader, db: DB, f: Filters):
    from fastapi.responses import Response

    from app.domain.trading.analytics import trades_csv
    body = trades_csv(await f.rows(db))
    return Response(content=body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="0-hl-trades.csv"'})


@router.get("/stats")
async def stats(p: Reader, db: DB, f: Filters):
    from app.domain.trading.analytics import stats_report
    return ok(stats_report(await f.rows(db)))


@router.get("/stats/curve")
async def stats_curve(p: Reader, db: DB, bot_id: str = Query(...), since: datetime | None = Query(None),
                      until: datetime | None = Query(None)):
    from app.domain.trading.analytics import performance_curve
    bot = await _bot(db, bot_id)
    return ok(await performance_curve(db, bot.id, since=since, until=until))


@router.get("/orders")
async def orders(p: Reader, db: DB, bot_id: str = Query(...), limit: int = Query(50, ge=1, le=500)):
    bot = await _bot(db, bot_id)
    rows = (await db.execute(select(Order, OrderIntent).join(OrderIntent, OrderIntent.id == Order.intent_id)
                             .where(OrderIntent.bot_id == bot.id)
                             .order_by(Order.submitted_at.desc()).limit(limit))).all()
    items = []
    for order, intent in rows:
        fills = (await db.scalars(select(Fill).where(Fill.order_id == order.id))).all()
        items.append({
            "id": order.id, "client_order_id": order.client_order_id, "side": order.side.value,
            "purpose": intent.purpose.value, "instrument_id": intent.instrument_id,
            "status": order.status.value, "requested_qty": order.requested_qty,
            "filled_qty": order.filled_qty, "avg_fill_price": order.avg_fill_price,
            "limit_price": order.limit_price, "submitted_at": order.submitted_at,
            "position_id": intent.position_id, "candidate_id": intent.candidate_id,
            "fills": [{"qty": f.qty, "price": f.price, "fee": f.fee, "occurred_at": f.occurred_at}
                      for f in fills],
        })
    return ok({"items": items})


@router.get("/equity")
async def equity(p: Reader, db: DB, bot_id: str = Query(...), since: datetime | None = Query(None),
                 limit: int = Query(1440, ge=1, le=10080)):
    bot = await _bot(db, bot_id)
    stmt = select(EquitySnapshot).where(EquitySnapshot.bot_id == bot.id)
    if since is not None:
        stmt = stmt.where(EquitySnapshot.at >= since)
    rows = (await db.scalars(stmt.order_by(EquitySnapshot.at.desc()).limit(limit))).all()
    return ok({"items": [{"at": s.at, "equity": s.equity, "cash": s.cash,
                          "positions_value": s.positions_mark_value,
                          "realized_pnl_net": s.realized_pnl_net, "unrealized_pnl": s.unrealized_pnl,
                          "quality": s.quality.value} for s in reversed(rows)]})


@router.get("/events")
async def events(p: Reader, db: DB, bot_id: str | None = Query(None), after: int = Query(0, ge=0),
                 types: str | None = Query(None), limit: int = Query(100, ge=1, le=500)):
    stmt = select(DomainEvent).where(DomainEvent.position > after)
    if bot_id is not None:
        bot = await _bot(db, bot_id)
        stmt = stmt.where(or_(DomainEvent.bot_id == bot.id, DomainEvent.aggregate_type == "market_feed"))
    if types:
        stmt = stmt.where(DomainEvent.event_type.in_([t for t in types.split(",") if t]))
    rows = (await db.scalars(stmt.order_by(DomainEvent.position).limit(limit))).all()
    return ok({"items": [event_json(e) for e in rows],
               "next_after": rows[-1].position if rows else after})


@router.get("/events/trace/{correlation_id}")
async def trace(correlation_id: str, p: Reader, db: DB):
    corr = parse_uuid(correlation_id, "correlation_id")
    rows = (await db.scalars(select(DomainEvent).where(DomainEvent.correlation_id == corr)
                             .order_by(DomainEvent.position))).all()
    if not rows:
        raise NotFound(f"no event for correlation {correlation_id}")
    return ok({"correlation_id": corr, "items": [event_json(e) for e in rows]})


@router.get("/market/feed")
async def market_feed(p: Reader, db: DB):
    rows = (await db.scalars(select(FeedStatus).order_by(FeedStatus.instrument_id))).all()
    instruments = (await db.scalars(select(Instrument).where(Instrument.in_universe)
                                    .order_by(Instrument.volume_24h_usd.desc()))).all()
    return ok({
        "summary": await feed_summary(db, utcnow()),
        "feeds": [{"instrument_id": f.instrument_id, "state": f.state, "reason": f.reason,
                   "last_message_at": f.last_message_at, "last_trade_at": f.last_trade_at,
                   "last_checksum_ok_at": f.last_checksum_ok_at, "resync_count": f.resync_count,
                   "updated_at": f.updated_at} for f in rows],
        "universe": [{"instrument_id": i.id, "symbol": i.symbol, "status": i.status,
                      "volume_24h_usd": i.volume_24h_usd} for i in instruments],
        "count": await db.scalar(select(func.count()).select_from(Instrument).where(Instrument.in_universe)),
    })

