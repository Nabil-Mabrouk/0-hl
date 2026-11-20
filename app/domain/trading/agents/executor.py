"""EXECUTOR paper — entries and exits against the live order book
(spec 06-agents/03 « EXECUTOR paper » and « Sorties », 08-risque-execution/02-03).

Each execution is ONE transaction owned by the caller: intent, order, fills,
ledger postings and position change commit together or not at all (INV-04).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.trading.agents.sim import EXIT_FLOOR_SLIPPAGE, PAPER_TAKER_FEE, SweepResult, sweep
from app.domain.trading.enums import (
    CandidateStatus,
    IntentPurpose,
    LedgerKind,
    OrderStatus,
    PositionStatus,
    Side,
)
from app.domain.trading.events import append_event
from app.domain.trading.ledger import (
    buy_fill_postings,
    post_transaction,
    release_postings,
    reserve_postings,
    sell_fill_postings,
)
from app.domain.trading.market.book import OrderBook
from app.domain.trading.models import (
    Candidate,
    Fill,
    Order,
    OrderIntent,
    Position,
    SizingDecision,
)

ZERO = Decimal(0)
SOURCE = "worker:executor"
INTENT_NAMESPACE = uuid.UUID("6c1e9b0e-0d1a-4c55-9a43-0a0e17000001")
SELLABLE_STATUSES = frozenset({"online", "reduce_only", "limit_only"})


def entry_intent_id(sizing_id: uuid.UUID) -> uuid.UUID:
    return uuid.uuid5(INTENT_NAMESPACE, f"entry:{sizing_id}")


async def _emit(db, event_type, aggregate_type, aggregate_id, payload, *, bot_id, correlation_id,
                instrument_id):
    # every order/position/ledger event carries instrument_id (06-agents/03, 2026-11-20)
    await append_event(db, event_type=event_type, aggregate_type=aggregate_type,
                       aggregate_id=aggregate_id, payload={"instrument_id": instrument_id, **payload},
                       source=SOURCE, bot_id=bot_id, correlation_id=correlation_id)


async def _record_fills(db, order: Order, res: SweepResult, now: datetime, fee_rate: Decimal) -> Decimal:
    fee_total = ZERO
    for i, (price, qty) in enumerate(res.levels):
        fee = price * qty * fee_rate
        fee_total += fee
        db.add(Fill(order_id=order.id, venue_fill_id=f"paper-{i}", qty=qty, price=price, fee=fee,
                    fee_asset="USD", liquidity="taker", occurred_at=now,
                    book_snapshot={"level": i, "price": format(price, "f"),
                                   "qty": format(qty, "f")}))
    return fee_total


@dataclass
class EntryOutcome:
    status: str               # FILLED | PARTIALLY_FILLED | EXPIRED | DUPLICATE | REFUSED
    position: Position | None = None
    order: Order | None = None
    reason: str | None = None
    latency_ms: int | None = None


async def execute_entry(
    db: AsyncSession,
    *,
    sizing: SizingDecision,
    candidate: Candidate,
    book: OrderBook,
    book_fresh: bool,
    max_slippage_pct: Decimal,
    initial_stop_pct: Decimal,
    take_profit_r: Decimal | None,
    now: datetime,
    fee_rate: Decimal = PAPER_TAKER_FEE,
) -> EntryOutcome:
    intent_id = entry_intent_id(sizing.id)
    if await db.get(OrderIntent, intent_id) is not None:
        return EntryOutcome("DUPLICATE", reason="INTENT_ALREADY_EXECUTED")
    if not sizing.approved or sizing.quantity is None:
        return EntryOutcome("REFUSED", reason="NOT_APPROVED")
    if sizing.expires_at is not None and now > sizing.expires_at:
        candidate.status = CandidateStatus.EXPIRED
        return EntryOutcome("REFUSED", reason="AUTHORIZATION_EXPIRED")
    if not book_fresh or book.best_ask is None:
        candidate.status = CandidateStatus.EXPIRED
        return EntryOutcome("REFUSED", reason="DATA_STALE")

    corr, bot_id = candidate.id, candidate.bot_id
    qty = sizing.quantity
    ref = sizing.reference_price or book.best_ask[0]
    limit = ref * (1 + max_slippage_pct / 100)
    reserve = qty * limit * (1 + fee_rate)

    intent = OrderIntent(
        id=intent_id, bot_id=bot_id, session_id=candidate.session_id, candidate_id=candidate.id,
        instrument_id=candidate.instrument_id, side=Side.BUY, purpose=IntentPurpose.ENTRY,
        quantity=qty, limit_price=limit, max_notional=qty * limit, risk_approval_id=sizing.id,
        idempotency_key=f"entry:{sizing.id}", status="CREATED", expires_at=sizing.expires_at,
    )
    db.add(intent)
    await db.flush()
    await _emit(db, "ORDER_INTENT_CREATED", "order_intent", intent.id,
                {"side": "BUY", "quantity": qty, "limit_price": limit,
                 "instrument_id": candidate.instrument_id}, bot_id=bot_id, correlation_id=corr,
                instrument_id=candidate.instrument_id)
    await post_transaction(db, bot_id=bot_id, kind=LedgerKind.RESERVE,
                           postings=reserve_postings(reserve), occurred_at=now,
                           description=f"reserve {intent.id}")

    order = Order(intent_id=intent.id, client_order_id=f"hl-{intent.id}", side=Side.BUY,
                  status=OrderStatus.SUBMITTING, requested_qty=qty, limit_price=limit,
                  submitted_at=now)
    db.add(order)
    await db.flush()
    await _emit(db, "ORDER_SUBMITTED", "order", order.id,
                {"client_order_id": order.client_order_id, "quantity": qty, "limit_price": limit},
                bot_id=bot_id, correlation_id=corr,
                instrument_id=candidate.instrument_id)

    res = sweep(book.top_asks(book.depth), qty, limit=limit, side="buy")
    if res.filled_qty <= ZERO:
        order.status, order.closed_at = OrderStatus.EXPIRED, now
        intent.status = "EXPIRED"
        candidate.status = CandidateStatus.EXPIRED
        await post_transaction(db, bot_id=bot_id, kind=LedgerKind.RELEASE,
                               postings=release_postings(reserve), occurred_at=now,
                               description=f"release {intent.id} (no fill)")
        await _emit(db, "ORDER_EXPIRED", "order", order.id, {"reason": "NO_LIQUIDITY_WITHIN_LIMIT"},
                    bot_id=bot_id, correlation_id=corr,
                instrument_id=candidate.instrument_id)
        return EntryOutcome("EXPIRED", order=order, reason="NO_LIQUIDITY_WITHIN_LIMIT")

    fee = await _record_fills(db, order, res, now, fee_rate)
    avg = res.avg_price
    order.filled_qty, order.avg_fill_price, order.acked_at, order.closed_at = res.filled_qty, avg, now, now
    partial = res.filled_qty < qty
    order.status = OrderStatus.PARTIALLY_FILLED if partial else OrderStatus.FILLED
    intent.status = "EXECUTED"
    await post_transaction(db, bot_id=bot_id, kind=LedgerKind.BUY_FILL,
                           postings=buy_fill_postings(candidate.instrument_id, res.notional, fee,
                                                      from_reserved=True),
                           occurred_at=now, description=f"buy fill {order.client_order_id}")
    leftover = reserve - res.notional - fee
    if leftover > ZERO:
        await post_transaction(db, bot_id=bot_id, kind=LedgerKind.RELEASE,
                               postings=release_postings(leftover), occurred_at=now,
                               description=f"release remainder {intent.id}")

    stop_price = avg * (1 - initial_stop_pct / 100)  # type: ignore[operator]
    tp = None if take_profit_r is None else avg + take_profit_r * (avg - stop_price)  # type: ignore[operator]
    est_exit_fee = stop_price * res.filled_qty * fee_rate
    position = Position(
        bot_id=bot_id, session_id=candidate.session_id, instrument_id=candidate.instrument_id,
        status=PositionStatus.OPEN, quantity=res.filled_qty, cost_basis=res.notional,
        avg_entry_price=avg, initial_stop_price=stop_price, take_profit_price=tp,
        r0_usd=(avg - stop_price) * res.filled_qty + fee + est_exit_fee,  # type: ignore[operator]
        opened_at=now, fees_paid=fee,
    )
    db.add(position)
    await db.flush()
    intent.position_id = position.id
    candidate.status = CandidateStatus.APPROVED
    latency_ms = int((now - sizing.created_at).total_seconds() * 1000) if sizing.created_at else None
    await _emit(db, "ORDER_PARTIAL_FILL" if partial else "ORDER_FILLED", "order", order.id,
                {"filled_qty": res.filled_qty, "requested_qty": qty, "avg_price": avg, "fee": fee,
                 "position_id": position.id, "decision_to_fill_ms": latency_ms},
                bot_id=bot_id, correlation_id=corr,
                instrument_id=candidate.instrument_id)
    await _emit(db, "POSITION_OPENED", "position", position.id,
                {"instrument_id": candidate.instrument_id, "quantity": res.filled_qty,
                 "avg_entry_price": avg, "stop_price": stop_price, "take_profit_price": tp,
                 "r0_usd": position.r0_usd, "environment": "PAPER"},
                bot_id=bot_id, correlation_id=corr,
                instrument_id=candidate.instrument_id)
    await _emit(db, "LEDGER_POSTED", "position", position.id,
                {"kind": "BUY_FILL", "notional": res.notional, "fee": fee},
                bot_id=bot_id, correlation_id=corr,
                instrument_id=candidate.instrument_id)
    return EntryOutcome("PARTIALLY_FILLED" if partial else "FILLED", position, order,
                        latency_ms=latency_ms)


async def position_correlation(db: AsyncSession, position: Position) -> uuid.UUID:
    cand = await db.scalar(select(OrderIntent.candidate_id).where(
        OrderIntent.position_id == position.id, OrderIntent.purpose == IntentPurpose.ENTRY).limit(1))
    return cand or position.id


@dataclass
class ExitOutcome:
    status: str               # CLOSED | REDUCED | DEFERRED
    reason: str | None = None
    sold_qty: Decimal = ZERO
    realized_pnl_net: Decimal | None = None
    r_multiple: Decimal | None = None


async def execute_exit(
    db: AsyncSession,
    *,
    position: Position,
    exit_reason: str,
    book: OrderBook | None,
    book_fresh: bool,
    pair_status: str | None,
    now: datetime,
    fee_rate: Decimal = PAPER_TAKER_FEE,
) -> ExitOutcome:
    if book is None or not book_fresh or book.best_bid is None:
        return ExitOutcome("DEFERRED", reason="BOOK_NOT_LIVE")
    if pair_status not in SELLABLE_STATUSES:
        return ExitOutcome("DEFERRED", reason=f"PAIR_STATUS_{pair_status}")

    bot_id = position.bot_id
    corr = await position_correlation(db, position)
    qty = position.quantity
    floor = book.best_bid[0] * (1 - EXIT_FLOOR_SLIPPAGE)
    attempt = 1 + len((await db.scalars(select(OrderIntent.id).where(
        OrderIntent.position_id == position.id, OrderIntent.purpose == IntentPurpose.EXIT))).all())
    intent = OrderIntent(
        id=uuid.uuid4(), bot_id=bot_id, session_id=position.session_id, candidate_id=None,
        position_id=position.id, instrument_id=position.instrument_id, side=Side.SELL,
        purpose=IntentPurpose.EXIT, quantity=qty, limit_price=floor,
        idempotency_key=f"exit:{position.id}:{attempt}", status="CREATED",
    )
    db.add(intent)
    await db.flush()  # intent row must exist before its order (FK, no ORM relationship)
    order = Order(intent_id=intent.id, client_order_id=f"hl-{intent.id}", side=Side.SELL,
                  status=OrderStatus.SUBMITTING, requested_qty=qty, limit_price=floor,
                  submitted_at=now)
    db.add(order)
    await db.flush()
    await _emit(db, "ORDER_SUBMITTED", "order", order.id,
                {"side": "SELL", "quantity": qty, "limit_price": floor, "reason": exit_reason},
                bot_id=bot_id, correlation_id=corr,
                    instrument_id=position.instrument_id)

    res = sweep(book.top_bids(book.depth), qty, limit=floor, side="sell")
    if res.filled_qty <= ZERO:
        order.status, order.closed_at, intent.status = OrderStatus.EXPIRED, now, "EXPIRED"
        await _emit(db, "ORDER_EXPIRED", "order", order.id, {"reason": "NO_BID_ABOVE_FLOOR"},
                    bot_id=bot_id, correlation_id=corr,
                    instrument_id=position.instrument_id)
        return ExitOutcome("DEFERRED", reason="NO_BID_ABOVE_FLOOR")

    fee = await _record_fills(db, order, res, now, fee_rate)
    sold = res.filled_qty
    cost_released = position.cost_basis * sold / qty
    order.filled_qty, order.avg_fill_price, order.acked_at, order.closed_at = sold, res.avg_price, now, now
    order.status = OrderStatus.FILLED if sold == qty else OrderStatus.PARTIALLY_FILLED
    intent.status = "EXECUTED"
    await post_transaction(db, bot_id=bot_id, kind=LedgerKind.SELL_FILL,
                           postings=sell_fill_postings(position.instrument_id, res.notional, fee,
                                                       cost_released),
                           occurred_at=now, description=f"sell fill {order.client_order_id}")
    gross = res.notional - cost_released
    position.quantity = qty - sold
    position.cost_basis = position.cost_basis - cost_released
    position.fees_paid = position.fees_paid + fee
    position.realized_pnl_net = (position.realized_pnl_net or ZERO) + gross - fee
    position.row_version += 1
    await _emit(db, "ORDER_FILLED" if sold == qty else "ORDER_PARTIAL_FILL", "order", order.id,
                {"filled_qty": sold, "avg_price": res.avg_price, "fee": fee},
                bot_id=bot_id, correlation_id=corr,
                    instrument_id=position.instrument_id)
    await _emit(db, "LEDGER_POSTED", "position", position.id,
                {"kind": "SELL_FILL", "proceeds": res.notional, "fee": fee,
                 "cost_released": cost_released}, bot_id=bot_id, correlation_id=corr,
                    instrument_id=position.instrument_id)

    if position.quantity > ZERO:
        position.status = PositionStatus.REDUCING
        await _emit(db, "POSITION_REDUCED", "position", position.id,
                    {"sold_qty": sold, "remaining_qty": position.quantity, "reason": exit_reason},
                    bot_id=bot_id, correlation_id=corr,
                    instrument_id=position.instrument_id)
        await db.flush()
        return ExitOutcome("REDUCED", exit_reason, sold)

    # realized_pnl_net accumulated Σ(gross − exit fee); the entry fee is deducted once, at close
    position.realized_pnl_net = position.realized_pnl_net - await entry_fees(db, position)
    position.status = PositionStatus.CLOSED
    position.closed_at = now
    position.exit_reason = exit_reason
    r_mult = (position.realized_pnl_net / position.r0_usd) if position.r0_usd else None
    await db.flush()
    await _emit(db, "POSITION_CLOSED", "position", position.id,
                {"reason": exit_reason, "realized_pnl_net": position.realized_pnl_net,
                 "fees_paid": position.fees_paid, "r_multiple": r_mult},
                bot_id=bot_id, correlation_id=corr,
                    instrument_id=position.instrument_id)
    return ExitOutcome("CLOSED", exit_reason, sold, position.realized_pnl_net, r_mult)


async def entry_fees(db: AsyncSession, position: Position) -> Decimal:
    rows = await db.scalars(
        select(Fill.fee).join(Order, Order.id == Fill.order_id)
        .join(OrderIntent, OrderIntent.id == Order.intent_id)
        .where(OrderIntent.position_id == position.id, OrderIntent.purpose == IntentPurpose.ENTRY)
    )
    return sum(rows.all(), ZERO)
