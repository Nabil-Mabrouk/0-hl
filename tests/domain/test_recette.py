"""M1.6 — acceptance script (12-tests/02-recette-m1) and restart test (QA-20, INV-04)."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.domain.trading.enums import LedgerKind, OrderStatus, PositionStatus, Side
from app.domain.trading.models import (
    Candidate,
    DomainEvent,
    Order,
    OrderIntent,
    Position,
    SizingDecision,
)
from app.domain.trading.recette import build_report
from app.domain.trading.seed import run_seed

from .agent_fakes import NOW, Clock, add_instrument, add_minutes, engine, market_state, set_book

pytestmark = pytest.mark.anyio
D = Decimal


async def _traded_world(session_factory):
    async with session_factory() as db:
        seed = await run_seed(db)
        await add_instrument(db)
        await add_minutes(db)
        await db.commit()
    clock, st = Clock(NOW), market_state()
    eng = engine(session_factory, st, clock)
    await eng.tick()                                        # auto-start + entry
    set_book(st, asks=((108.2, 50),), bids=((108, 50),))
    clock.advance(seconds=10)
    await eng.tick()                                        # take-profit exit
    return seed.bot, clock, st, eng


def _status(report, cid):
    return next(c for c in report.checks if c.id == cid)


async def test_clean_run_passes_blocking_criteria(session_factory):
    bot, clock, *_ = await _traded_world(session_factory)
    report = await build_report(session_factory, hours=0.001, now=clock.now)
    blocking = {c.id: c.status for c in report.checks if c.blocking}
    assert blocking == {f"R-0{i}": "OK" for i in range(1, 9)}, report.as_text()
    assert report.passed
    assert _status(report, "R-04").data["closed_positions"] == 1
    activity = _status(report, "R-11")
    assert activity.data["wins"] == 1 and activity.data["exits"] == {"TAKE_PROFIT": 1}
    assert "GATE M1 : FRANCHI" in report.as_text()
    assert report.as_json()["passed"] is True


async def test_session_too_short_blocks_the_gate(session_factory):
    _bot, clock, *_ = await _traded_world(session_factory)
    report = await build_report(session_factory, hours=24, now=clock.now)
    assert _status(report, "R-08").status == "KO" and not report.passed


async def test_unbalanced_ledger_is_detected(session_factory):
    bot, clock, *_ = await _traded_world(session_factory)
    async with session_factory() as db:   # bypass the service guard on purpose
        from app.domain.trading.models import LedgerEntry, LedgerTransaction
        tx = LedgerTransaction(bot_id=bot.id, kind=LedgerKind.COMPENSATION, occurred_at=clock.now)
        db.add(tx)
        await db.flush()
        db.add(LedgerEntry(transaction_id=tx.id, bot_id=bot.id, account_code="cash", asset="USD", amount=D(5)))
        await db.commit()
    report = await build_report(session_factory, hours=0.001, now=clock.now)
    assert _status(report, "R-01").status == "KO" and not report.passed


async def test_duplicate_order_and_missing_trace_are_detected(session_factory):
    bot, clock, *_ = await _traded_world(session_factory)
    async with session_factory() as db:
        intent = await db.scalar(select(OrderIntent).where(OrderIntent.side == Side.BUY))
        db.add(Order(intent_id=intent.id, client_order_id=f"dup-{uuid.uuid4()}", side=Side.BUY,
                     status=OrderStatus.EXPIRED, requested_qty=D(1)))
        pos = await db.scalar(select(Position))
        db.add(Position(bot_id=bot.id, session_id=pos.session_id, instrument_id=pos.instrument_id,
                        status=PositionStatus.CLOSED, quantity=D(0), cost_basis=D(0)))
        await db.commit()
    report = await build_report(session_factory, hours=0.001, now=clock.now)
    assert _status(report, "R-03").status == "KO"
    assert _status(report, "R-04").status == "KO"


async def test_tampered_event_breaks_journal_integrity(session_factory):
    _bot, clock, *_ = await _traded_world(session_factory)
    async with session_factory() as db:
        e = await db.scalar(select(DomainEvent).where(DomainEvent.event_type == "POSITION_CLOSED"))
        e.payload = {**e.payload, "realized_pnl_net": "999999"}
        await db.commit()
    report = await build_report(session_factory, hours=0.001, now=clock.now)
    assert _status(report, "R-05").status == "KO"


async def test_reconciliation_check_is_read_only(session_factory):
    bot, clock, *_ = await _traded_world(session_factory)
    async with session_factory() as db:   # orphan intent: would be expired by reconcile()
        pos = await db.scalar(select(Position))
        sizing = await db.scalar(select(SizingDecision))
        db.add(OrderIntent(id=uuid.uuid4(), bot_id=bot.id, session_id=pos.session_id,
                           instrument_id=pos.instrument_id, side=Side.BUY, purpose="ENTRY",
                           quantity=D(1), risk_approval_id=sizing.id,
                           idempotency_key=f"orphan-{uuid.uuid4()}", status="CREATED"))
        await db.commit()
    report = await build_report(session_factory, hours=0.001, now=clock.now)
    assert any("orpheline" in d for d in _status(report, "R-02").details)
    async with session_factory() as db:
        still = await db.scalar(select(func.count()).select_from(OrderIntent).where(OrderIntent.status == "CREATED"))
    assert still == 1   # nothing written by the report


async def test_restart_between_sizing_and_execution_creates_no_duplicate(session_factory):
    """QA-20 / INV-04: worker restarted after sizing, before execution."""
    async with session_factory() as db:
        await run_seed(db)
        await add_instrument(db)
        await add_minutes(db)
        await db.commit()
    clock, st = Clock(NOW), market_state()
    first = engine(session_factory, st, clock)

    class Crash(Exception):
        pass

    async def crash(_s):
        raise Crash("SIGKILL between sizing and execution")

    first._sleep = crash  # noqa: SLF001
    await first.tick()                      # candidate + sizing committed, then "crash"
    async with session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(SizingDecision)) == 1
        assert await db.scalar(select(func.count()).select_from(OrderIntent)) == 0

    clock.advance(seconds=15)               # restart: authorisation (10 s TTL) has expired
    second = engine(session_factory, st, clock)
    await second.tick()
    async with session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(Candidate)) == 1
        assert await db.scalar(select(func.count()).select_from(OrderIntent)) == 0
        assert await db.scalar(select(func.count()).select_from(Order)) == 0
    report = await build_report(session_factory, hours=0.001, now=clock.now)
    assert all(c.status == "OK" for c in report.checks if c.id in {"R-01", "R-02", "R-03", "R-04", "R-05"})
