"""Terminal snapshot built from the database only (spec 10-backend/04 « Instantané terminal »)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.trading.agents.commands import active_session
from app.domain.trading.agents.equity import EquityView, daily_loss, drawdown
from app.domain.trading.config_schemas import validate_risk_policy_config
from app.domain.trading.enums import BotStatus, PositionStatus
from app.domain.trading.errors import NotFound
from app.domain.trading.ledger import ACCOUNT_CASH, ACCOUNT_CASH_RESERVED, USD, account_balances
from app.domain.trading.market.service import CONNECTION_FEED_ID
from app.domain.trading.enums import IntentPurpose
from app.domain.trading.models import (
    Bot,
    BotSession,
    Candidate,
    DomainEvent,
    FeedStatus,
    Order,
    OrderIntent,
    Position,
    RiskPolicyVersion,
    StrategyVersion,
)

ZERO = Decimal(0)
MARK_MAX_AGE = timedelta(seconds=30)
WORKER_MAX_SILENCE = timedelta(seconds=30)
AGENT_ACTIVE_WINDOW = timedelta(minutes=2)
OPEN = (PositionStatus.OPEN, PositionStatus.REDUCING)

AGENT_EVENTS: dict[str, tuple[str, ...]] = {
    "SCANNER": ("SCAN_CYCLE_COMPLETED", "CANDIDATE_DETECTED"),
    "RUG_CHECK": ("RISK_PASSED", "RISK_REJECTED:RUG_CHECK"),
    "SIZER": ("POSITION_SIZED", "RISK_REJECTED:SIZER"),
    "EXECUTOR": ("ORDER_INTENT_CREATED", "ORDER_SUBMITTED", "ORDER_FILLED", "ORDER_PARTIAL_FILL",
                 "ORDER_EXPIRED", "ORDER_REJECTED", "POSITION_OPENED", "POSITION_REDUCED",
                 "POSITION_CLOSED"),
    "EQUITY": ("EQUITY_UPDATED", "RECONCILIATION_OK", "RECONCILIATION_FAILED"),
}


def position_json(p: Position) -> dict[str, Any]:
    return {
        "id": p.id, "instrument_id": p.instrument_id, "symbol": p.instrument_id.split(":", 1)[1],
        "status": p.status.value, "quantity": p.quantity, "cost_basis": p.cost_basis,
        "avg_entry_price": p.avg_entry_price, "initial_stop_price": p.initial_stop_price,
        "take_profit_price": p.take_profit_price, "r0_usd": p.r0_usd, "opened_at": p.opened_at,
        "closed_at": p.closed_at, "exit_reason": p.exit_reason, "fees_paid": p.fees_paid,
        "realized_pnl_net": p.realized_pnl_net,
        "r_multiple": (p.realized_pnl_net / p.r0_usd) if (p.realized_pnl_net is not None and p.r0_usd) else None,
        "mark_price": p.mark_price, "mark_value": p.mark_value, "marked_at": p.marked_at,
        "unrealized_pnl": (p.mark_value - p.cost_basis) if p.mark_value is not None else None,
    }


async def worker_alive(db: AsyncSession, now: datetime) -> bool:
    row = await db.get(FeedStatus, CONNECTION_FEED_ID)
    return bool(row and row.reason != "WORKER_STOPPED" and now - row.updated_at <= WORKER_MAX_SILENCE)


async def feed_summary(db: AsyncSession, now: datetime) -> dict[str, Any]:
    conn = await db.get(FeedStatus, CONNECTION_FEED_ID)
    rows = (await db.scalars(select(FeedStatus).where(FeedStatus.instrument_id != CONNECTION_FEED_ID))).all()
    subscribed = [r for r in rows if r.state != "UNKNOWN" or r.reason != "NOT_SUBSCRIBED"]
    return {
        "connection": None if conn is None else conn.state,
        "connection_reason": None if conn is None else conn.reason,
        "updated_at": None if conn is None else conn.updated_at,
        "worker_alive": await worker_alive(db, now),
        "pairs_live": sum(1 for r in subscribed if r.state == "LIVE"),
        "pairs_subscribed": len(subscribed),
    }


async def equity_json(db: AsyncSession, bot_id: uuid.UUID, now: datetime) -> dict[str, Any]:
    bal = await account_balances(db, bot_id)
    cash = bal.get((ACCOUNT_CASH, USD), ZERO)
    reserved = bal.get((ACCOUNT_CASH_RESERVED, USD), ZERO)
    open_pos = (await db.scalars(select(Position).where(
        Position.bot_id == bot_id, Position.status.in_(OPEN)))).all()
    fresh = all(p.mark_value is not None and p.marked_at is not None
                and now - p.marked_at <= MARK_MAX_AGE for p in open_pos)
    positions_value = sum((p.mark_value for p in open_pos), ZERO) if fresh else None
    cost = sum((p.cost_basis for p in open_pos), ZERO)
    closed = (await db.scalars(select(Position.realized_pnl_net).where(
        Position.bot_id == bot_id, Position.status == PositionStatus.CLOSED))).all()
    as_of = min((p.marked_at for p in open_pos if p.marked_at), default=now)
    return {
        "cash": cash, "cash_reserved": reserved, "positions_value": positions_value,
        "equity": None if positions_value is None else cash + reserved + positions_value,
        "realized_pnl_net": sum((v for v in closed if v is not None), ZERO),
        "unrealized_pnl": None if positions_value is None else positions_value - cost,
        "quality": "OK" if fresh else "STALE", "as_of": as_of if open_pos else now,
    }


async def stats_json(db: AsyncSession, bot_id: uuid.UUID) -> dict[str, int]:
    pnl = (await db.scalars(select(Position.realized_pnl_net).where(
        Position.bot_id == bot_id, Position.status == PositionStatus.CLOSED))).all()
    return {"trades": len(pnl), "wins": sum(1 for v in pnl if v is not None and v > 0),
            "losses": sum(1 for v in pnl if v is not None and v < 0),
            "breakeven": sum(1 for v in pnl if v is not None and v == 0)}


def _agent_key(e: DomainEvent) -> str:
    if e.event_type == "RISK_REJECTED":
        return f"RISK_REJECTED:{(e.payload or {}).get('stage', '')}"
    return e.event_type


async def agents_json(db: AsyncSession, bot_id: uuid.UUID, now: datetime, alive: bool) -> dict[str, Any]:
    recent = (await db.scalars(select(DomainEvent).where(DomainEvent.bot_id == bot_id)
                               .order_by(DomainEvent.position.desc()).limit(500))).all()
    out: dict[str, Any] = {}
    for agent, keys in AGENT_EVENTS.items():
        last = next((e for e in recent if _agent_key(e) in keys), None)
        out[agent] = _agent_state(last, now, alive)
    last_any = recent[0] if recent else None
    out["LOGGER"] = _agent_state(last_any, now, alive) | {
        "position": await db.scalar(select(func.max(DomainEvent.position)))}
    return out


def _agent_state(e: DomainEvent | None, now: datetime, alive: bool) -> dict[str, Any]:
    if not alive:
        status = "DOWN"
    elif e is not None and now - e.occurred_at <= AGENT_ACTIVE_WINDOW:
        status = "ACTIVE"
    else:
        status = "IDLE"
    return {"status": status, "last_event_type": None if e is None else e.event_type,
            "last_event_at": None if e is None else e.occurred_at}


def bot_json(bot: Bot) -> dict[str, Any]:
    return {"id": bot.id, "name": bot.name, "environment": bot.environment.value,
            "status": bot.status.value, "row_version": bot.row_version,
            "strategy_version_id": bot.strategy_version_id,
            "risk_policy_version_id": bot.risk_policy_version_id,
            "created_at": bot.created_at, "updated_at": bot.updated_at,
            "deleted_at": bot.deleted_at}


def session_json(s: BotSession | None, now: datetime) -> dict[str, Any] | None:
    if s is None:
        return None
    end = s.ended_at or now
    return {"id": s.id, "status": s.status.value, "started_at": s.started_at, "ended_at": s.ended_at,
            "uptime_seconds": int((end - s.started_at).total_seconds()),
            "strategy_sha256": s.strategy_sha256, "risk_policy_sha256": s.risk_policy_sha256}


ENTRY_FEATURES = ("volume_spike", "buy_notional_share", "window_notional_usd", "window_trade_count",
                  "price_change_5m", "spread_bps")


async def entry_features(db: AsyncSession, positions: list[Position]) -> dict[uuid.UUID, dict[str, Any]]:
    """Key features of the candidate behind each position (M1.5 « raison » of the trade)."""
    if not positions:
        return {}
    rows = await db.execute(
        select(OrderIntent.position_id, Candidate.features)
        .join(Candidate, Candidate.id == OrderIntent.candidate_id)
        .where(OrderIntent.position_id.in_([p.id for p in positions]),
               OrderIntent.purpose == IntentPurpose.ENTRY))
    out: dict[uuid.UUID, dict[str, Any]] = {}
    for position_id, features in rows:
        out[position_id] = {k: (features.get(k) or {}).get("value") for k in ENTRY_FEATURES}
    return out


async def entry_amounts(db: AsyncSession, positions: list[Position]) -> dict[uuid.UUID, tuple[Decimal, Decimal]]:
    """Quantity and notional actually executed at entry (fills of the entry order)."""
    if not positions:
        return {}
    rows = await db.execute(
        select(OrderIntent.position_id, Order.filled_qty, Order.avg_fill_price)
        .join(Order, Order.intent_id == OrderIntent.id)
        .where(OrderIntent.position_id.in_([p.id for p in positions]),
               OrderIntent.purpose == IntentPurpose.ENTRY))
    out: dict[uuid.UUID, tuple[Decimal, Decimal]] = {}
    for position_id, qty, avg in rows:
        if qty and avg is not None:
            prev_q, prev_n = out.get(position_id, (ZERO, ZERO))
            out[position_id] = (prev_q + qty, prev_n + qty * avg)
    return out


def _next_utc_midnight(now: datetime) -> datetime:
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


async def entry_blocks(db: AsyncSession, bot: Bot, equity: dict[str, Any], open_count: int,
                       now: datetime) -> list[dict[str, Any]]:
    """Entry blocks in force, computed with the SIZER functions (10-backend/04)."""
    blocks: list[dict[str, Any]] = []
    if bot.status is not BotStatus.RUNNING:
        blocks.append({"code": "BOT_NOT_RUNNING", "value": bot.status.value, "limit": "RUNNING", "until": None})
    pv = await db.get(RiskPolicyVersion, bot.risk_policy_version_id)
    if pv is None:
        return blocks
    policy = validate_risk_policy_config(pv.config)
    if policy.kill_switch:
        blocks.append({"code": "KILL_SWITCH", "value": "on", "limit": "off", "until": None})
    if policy.max_open_positions is not None and open_count >= policy.max_open_positions:
        blocks.append({"code": "MAX_OPEN_POSITIONS", "value": str(open_count),
                       "limit": str(policy.max_open_positions), "until": None})
    eq = equity["equity"]
    max_daily = policy.dec("max_daily_loss_pct")
    if eq is not None and max_daily is not None:
        loss = await daily_loss(db, bot.id, eq, now)
        if loss is not None and loss >= max_daily / 100:
            blocks.append({"code": "MAX_DAILY_LOSS", "value": loss, "limit": max_daily / 100,
                           "until": _next_utc_midnight(now)})
    max_dd = policy.dec("max_drawdown_pct")
    if eq is not None and max_dd is not None:
        view = EquityView(equity["cash"], equity["cash_reserved"], equity["positions_value"], eq,
                          equity["owner_capital"], equity["unrealized_pnl"])
        dd = await drawdown(db, bot.id, view)
        if dd is not None and dd >= max_dd / 100:
            blocks.append({"code": "MAX_DRAWDOWN", "value": dd, "limit": max_dd / 100, "until": None})
    return blocks


async def config_json(db: AsyncSession, bot: Bot) -> dict[str, Any]:
    sv = await db.get(StrategyVersion, bot.strategy_version_id)
    pv = await db.get(RiskPolicyVersion, bot.risk_policy_version_id)
    return {"strategy_id": sv.strategy_id if sv else None,
            "strategy_version": sv.version if sv else None,
            "max_trade_risk_pct": (pv.config or {}).get("max_trade_risk_pct") if pv else None,
            "max_slippage_pct": (pv.config or {}).get("max_slippage_pct") if pv else None}


async def build_snapshot(db: AsyncSession, bot_id: uuid.UUID, now: datetime) -> dict[str, Any]:
    from app.domain.trading.api.common import event_json

    bot = await db.get(Bot, bot_id)
    if bot is None:
        raise NotFound(f"unknown bot {bot_id}")
    session = await active_session(db, bot.id) or await db.scalar(
        select(BotSession).where(BotSession.bot_id == bot.id)
        .order_by(BotSession.started_at.desc()).limit(1))
    alive = await worker_alive(db, now)
    open_pos = (await db.scalars(select(Position).where(
        Position.bot_id == bot.id, Position.status.in_(OPEN)).order_by(Position.opened_at))).all()
    trades = (await db.scalars(select(Position).where(
        Position.bot_id == bot.id, Position.status == PositionStatus.CLOSED)
        .order_by(Position.closed_at.desc()).limit(20))).all()
    scan = await db.scalar(select(DomainEvent).where(
        DomainEvent.bot_id == bot.id, DomainEvent.event_type == "SCAN_CYCLE_COMPLETED")
        .order_by(DomainEvent.position.desc()).limit(1))
    events = (await db.scalars(select(DomainEvent).where(
        or_(DomainEvent.bot_id == bot.id, DomainEvent.aggregate_type == "market_feed"))
        .order_by(DomainEvent.position.desc()).limit(50))).all()
    features = await entry_features(db, [*open_pos, *trades])
    amounts = await entry_amounts(db, [*open_pos, *trades])
    owner = -(await account_balances(db, bot.id)).get(("owner_capital", USD), ZERO)
    equity = await equity_json(db, bot.id, now)

    def with_features(p: Position) -> dict[str, Any]:
        qty, notional = amounts.get(p.id, (None, None))
        return position_json(p) | {"entry_features": features.get(p.id),
                                   "entry_qty": qty, "entry_notional": notional}

    return {
        "generated_at": now,
        "bot": bot_json(bot),
        "config": await config_json(db, bot),
        "owner_capital": owner,
        "session": session_json(session, now),
        "equity": equity,
        "entry_blocks": await entry_blocks(db, bot, equity | {"owner_capital": owner},
                                           len(open_pos), now),
        "stats": await stats_json(db, bot.id),
        "open_positions": [with_features(p) for p in open_pos],
        "recent_trades": [with_features(p) for p in trades],
        "agents": await agents_json(db, bot.id, now, alive),
        "scan": None if scan is None else scan.payload | {"occurred_at": scan.occurred_at},
        "feed": await feed_summary(db, now),
        "recent_events": [event_json(e) for e in reversed(events)],
        "position": await db.scalar(select(func.max(DomainEvent.position))) or 0,
    }
