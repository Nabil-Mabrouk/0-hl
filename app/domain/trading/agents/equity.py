"""EQUITY agent: conservative valuation, snapshots, continuous limits, reconciliation
(spec 06-agents/03 « EQUITY, contrôles continus et réconciliation »)."""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.trading.agents.sim import EXIT_FLOOR_SLIPPAGE, PAPER_TAKER_FEE, sweep
from app.domain.trading.enums import CashMovementKind, LedgerKind, PositionStatus, SnapshotQuality
from app.domain.trading.ledger import (
    ACCOUNT_CASH,
    ACCOUNT_CASH_RESERVED,
    INVENTORY_PREFIX,
    USD,
    account_balances,
)
from app.domain.trading.market.state import MarketState
from app.domain.trading.models import (
    CashMovement,
    EquitySnapshot,
    Fill,
    LedgerTransaction,
    Order,
    OrderIntent,
    Position,
)

ZERO = Decimal(0)
OPEN_STATUSES = (PositionStatus.OPEN, PositionStatus.REDUCING)


def symbol_of(instrument_id: str) -> str:
    return instrument_id.split(":", 1)[1]


def liquidation_breakdown(qty: Decimal, state: MarketState, instrument_id: str,
                          fee: Decimal = PAPER_TAKER_FEE) -> tuple[Decimal, Decimal] | None:
    """(value, estimated part) of selling `qty`, net of taker fee (ADR-022). Visible bids are
    swept down to the exit floor best_bid × (1 − EXIT_FLOOR_SLIPPAGE); the quantity left is
    counted at that floor (never zero). None if the book is not LIVE."""
    symbol = symbol_of(instrument_id)
    book = state.book(symbol)
    if book is None or not state.is_fresh(symbol) or book.best_bid is None:
        return None
    floor = book.best_bid[0] * (1 - EXIT_FLOOR_SLIPPAGE)
    res = sweep(book.top_bids(book.depth), qty, limit=floor, side="sell")
    estimated = (qty - res.filled_qty) * floor
    return (res.notional + estimated) * (1 - fee), estimated * (1 - fee)


def liquidation_value(qty: Decimal, state: MarketState, instrument_id: str,
                      fee: Decimal = PAPER_TAKER_FEE) -> Decimal | None:
    """Proceeds of selling `qty` (see `liquidation_breakdown`); None if book not LIVE."""
    out = liquidation_breakdown(qty, state, instrument_id, fee)
    return None if out is None else out[0]


@dataclass
class EquityView:
    cash: Decimal
    cash_reserved: Decimal
    positions_value: Decimal | None
    equity: Decimal | None
    owner_capital: Decimal
    unrealized: Decimal | None
    open_positions: list[Position] = field(default_factory=list)
    estimated: Decimal = ZERO          # part of positions_value counted at the exit floor (ADR-022)

    @property
    def quality(self) -> SnapshotQuality:
        return SnapshotQuality.OK if self.equity is not None else SnapshotQuality.STALE


async def open_positions(db: AsyncSession, bot_id: uuid.UUID) -> list[Position]:
    return list((await db.scalars(
        select(Position).where(Position.bot_id == bot_id, Position.status.in_(OPEN_STATUSES))
    )).all())


async def compute_equity(db: AsyncSession, bot_id: uuid.UUID, state: MarketState) -> EquityView:
    bal = await account_balances(db, bot_id)
    cash = bal.get((ACCOUNT_CASH, USD), ZERO)
    reserved = bal.get((ACCOUNT_CASH_RESERVED, USD), ZERO)
    owner = -bal.get(("owner_capital", USD), ZERO)
    positions = await open_positions(db, bot_id)
    value: Decimal | None = ZERO
    cost = estimated = ZERO
    for p in positions:
        out = liquidation_breakdown(p.quantity, state, p.instrument_id)
        cost += p.cost_basis
        if out is None:
            value = None
        elif value is not None:
            value += out[0]
            estimated += out[1]
    equity = None if value is None else cash + reserved + value
    unrealized = None if value is None else value - cost
    return EquityView(cash, reserved, value, equity, owner, unrealized, positions, estimated)


async def write_snapshot(db: AsyncSession, bot_id: uuid.UUID, view: EquityView, now: datetime,
                         realized_net: Decimal) -> EquitySnapshot:
    snap = EquitySnapshot(
        bot_id=bot_id, at=now, cash=view.cash, cash_reserved=view.cash_reserved,
        positions_mark_value=view.positions_value, equity=view.equity,
        realized_pnl_net=realized_net, unrealized_pnl=view.unrealized, quality=view.quality,
    )
    db.add(snap)
    await db.flush()
    return snap


async def realized_pnl_net(db: AsyncSession, bot_id: uuid.UUID) -> Decimal:
    rows = await db.scalars(select(Position.realized_pnl_net).where(
        Position.bot_id == bot_id, Position.status == PositionStatus.CLOSED))
    return sum((v for v in rows.all() if v is not None), ZERO)


async def daily_loss(db: AsyncSession, bot_id: uuid.UUID, equity_now: Decimal | None,
                     now: datetime) -> Decimal | None:
    """Loss fraction since the first OK snapshot of the UTC day, net of capital flows."""
    if equity_now is None:
        return None
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    first = await db.scalar(
        select(EquitySnapshot).where(
            EquitySnapshot.bot_id == bot_id, EquitySnapshot.at >= day_start,
            EquitySnapshot.quality == SnapshotQuality.OK,
        ).order_by(EquitySnapshot.at).limit(1)
    )
    if first is None or not first.equity:
        return ZERO
    flows = ZERO
    for kind, amount in (await db.execute(
        select(CashMovement.kind, CashMovement.amount).where(
            CashMovement.bot_id == bot_id, CashMovement.occurred_at > first.at)
    )).all():
        flows += amount if kind is CashMovementKind.DEPOSIT else -amount
    return max((first.equity - (equity_now - flows)) / first.equity, ZERO)


async def drawdown(db: AsyncSession, bot_id: uuid.UUID, view: EquityView) -> Decimal | None:
    """On performance P = equity − owner_capital: (max P − P) / (owner_capital + max P)."""
    if view.equity is None:
        return None
    rows = await db.scalars(select(EquitySnapshot.equity).where(
        EquitySnapshot.bot_id == bot_id, EquitySnapshot.quality == SnapshotQuality.OK))
    perf_now = view.equity - view.owner_capital
    peak = max([e - view.owner_capital for e in rows.all() if e is not None] + [perf_now])
    denom = view.owner_capital + peak
    return ZERO if denom <= ZERO else max((peak - perf_now) / denom, ZERO)


@dataclass
class ReconciliationResult:
    ok: bool
    issues: list[str] = field(default_factory=list)
    expired_intents: int = 0


async def reconcile(db: AsyncSession, bot_id: uuid.UUID) -> ReconciliationResult:
    issues: list[str] = []
    bal = await account_balances(db, bot_id)
    # (a) inventory at cost per instrument == open positions cost basis
    inv: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for (account, asset), amount in bal.items():
        if account.startswith(INVENTORY_PREFIX) and asset == USD:
            inv[account[len(INVENTORY_PREFIX):]] += amount
    pos: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for p in await open_positions(db, bot_id):
        pos[p.instrument_id] += p.cost_basis
    for iid in set(inv) | set(pos):
        if inv[iid] != pos[iid]:
            issues.append(f"INVENTORY_MISMATCH {iid}: ledger={inv[iid]} positions={pos[iid]}")
    # (b) cash
    if bal.get((ACCOUNT_CASH, USD), ZERO) < ZERO:
        issues.append("NEGATIVE_CASH")
    if bal.get((ACCOUNT_CASH_RESERVED, USD), ZERO) != ZERO:
        issues.append(f"RESIDUAL_RESERVATION {bal.get((ACCOUNT_CASH_RESERVED, USD))}")
    # (c) every fill has its ledger transaction (one BUY/SELL_FILL per order with fills)
    filled_orders = await db.scalar(
        select(func.count(func.distinct(Fill.order_id))).join(Order, Order.id == Fill.order_id)
        .join(OrderIntent, OrderIntent.id == Order.intent_id).where(OrderIntent.bot_id == bot_id))
    fill_txs = await db.scalar(select(func.count()).select_from(LedgerTransaction).where(
        LedgerTransaction.bot_id == bot_id,
        LedgerTransaction.kind.in_([LedgerKind.BUY_FILL, LedgerKind.SELL_FILL])))
    if (filled_orders or 0) != (fill_txs or 0):
        issues.append(f"FILL_LEDGER_MISMATCH orders={filled_orders} ledger={fill_txs}")
    # (d) orphan intents
    expired = 0
    for intent in (await db.scalars(select(OrderIntent).where(
            OrderIntent.bot_id == bot_id, OrderIntent.status == "CREATED"))).all():
        has_order = await db.scalar(select(Order.id).where(Order.intent_id == intent.id).limit(1))
        if has_order is None:
            intent.status = "EXPIRED"
            expired += 1
    await db.flush()
    return ReconciliationResult(ok=not issues, issues=issues, expired_intents=expired)

