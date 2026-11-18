"""T-M1.3-06..12 — DecisionEngine end to end (network-free, controlled clock)."""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.domain.trading.agents import executor as executor_mod
from app.domain.trading.agents.commands import create_command
from app.domain.trading.agents.executor import execute_entry
from app.domain.trading.bots import create_bot
from app.domain.trading.enums import (
    BotStatus,
    CandidateStatus,
    CommandStatus,
    CommandType,
    Environment,
    PositionStatus,
)
from app.domain.trading.ledger import ACCOUNT_CASH_RESERVED, account_balances
from app.domain.trading.models import (
    Bot,
    BotSession,
    Candidate,
    Command,
    DomainEvent,
    EquitySnapshot,
    LedgerEntry,
    Order,
    OrderIntent,
    Position,
    SizingDecision,
)
from app.domain.trading.seed import run_seed

from .agent_fakes import (
    IID,
    T_WINDOW_END,
    Clock,
    add_instrument,
    add_minutes,
    engine,
    market_state,
    set_book,
)

pytestmark = pytest.mark.anyio
D = Decimal
NOW = T_WINDOW_END + timedelta(seconds=40)


async def _setup(session_factory, **minute_kw):
    async with session_factory() as db:
        seed = await run_seed(db)
        await add_instrument(db)
        await add_minutes(db, **minute_kw)
        await db.commit()
    clock = Clock(NOW)
    st = market_state()
    return seed.bot.id, st, clock, engine(session_factory, st, clock)


async def _one(session_factory, model, *where):
    async with session_factory() as db:
        return await db.scalar(select(model).where(*where))


async def _all(session_factory, model, *where):
    async with session_factory() as db:
        return list((await db.scalars(select(model).where(*where))).all())


async def _ledger_sum(session_factory, bot_id) -> Decimal:
    entries = await _all(session_factory, LedgerEntry, LedgerEntry.bot_id == bot_id)
    return sum((e.amount for e in entries), D(0))


async def test_full_chain_entry_then_take_profit(session_factory):
    """T-M1.3-12: candidate → assessment → sizing → order → position → TP exit."""
    bot_id, st, clock, eng = await _setup(session_factory)
    await eng.tick()

    bot = await _one(session_factory, Bot, Bot.id == bot_id)
    assert bot.status is BotStatus.RUNNING                         # ADR-013 auto-start
    session = await _one(session_factory, BotSession, BotSession.bot_id == bot_id)
    assert session.strategy_sha256 and session.risk_policy_sha256
    pos = await _one(session_factory, Position, Position.bot_id == bot_id)
    assert pos.status is PositionStatus.OPEN and pos.avg_entry_price == D(101)
    assert pos.initial_stop_price == D(101) * D("0.97")
    assert pos.take_profit_price == D(101) + 2 * (D(101) - pos.initial_stop_price)
    assert (await account_balances_for(session_factory, bot_id))[(ACCOUNT_CASH_RESERVED, "USD")] == 0
    assert await _ledger_sum(session_factory, bot_id) == 0

    # price rallies above TP -> exit
    set_book(st, asks=((108.2, 50),), bids=((108, 50),))
    clock.advance(seconds=10)
    await eng.tick()
    pos = await _one(session_factory, Position, Position.id == pos.id)
    assert pos.status is PositionStatus.CLOSED and pos.exit_reason == "TAKE_PROFIT"
    gross = (D(108) - D(101)) * pos.fees_paid / pos.fees_paid * (await _filled_qty(session_factory, pos))
    assert pos.realized_pnl_net == gross - pos.fees_paid
    assert await _ledger_sum(session_factory, bot_id) == 0

    cand = await _one(session_factory, Candidate, Candidate.bot_id == bot_id)
    chain = await _all(session_factory, DomainEvent, DomainEvent.correlation_id == cand.id)
    types = [e.event_type for e in sorted(chain, key=lambda e: e.position)]
    for expected in ("CANDIDATE_DETECTED", "RISK_PASSED", "POSITION_SIZED", "ORDER_INTENT_CREATED",
                     "ORDER_SUBMITTED", "ORDER_FILLED", "POSITION_OPENED", "LEDGER_POSTED",
                     "POSITION_CLOSED"):
        assert expected in types, expected
    closed = next(e for e in chain if e.event_type == "POSITION_CLOSED")
    assert D(closed.payload["r_multiple"]) > D(1)


async def account_balances_for(session_factory, bot_id):
    async with session_factory() as db:
        bal = await account_balances(db, bot_id)
    return {k: v for k, v in bal.items()} | {(ACCOUNT_CASH_RESERVED, "USD"): bal.get((ACCOUNT_CASH_RESERVED, "USD"), D(0))}


async def _filled_qty(session_factory, pos) -> Decimal:
    async with session_factory() as db:
        orders = (await db.scalars(select(Order).join(OrderIntent, OrderIntent.id == Order.intent_id)
                                   .where(OrderIntent.position_id == pos.id))).all()
    return next(o.filled_qty for o in orders if o.side.value == "BUY")


async def test_window_evaluated_once_and_unknown_skips(session_factory):
    bot_id, st, clock, eng = await _setup(session_factory, current_per_min=D(6000))  # spike 1.5
    await eng.tick()
    await eng.tick()
    assert await _one(session_factory, Candidate, Candidate.bot_id == bot_id) is None
    rt = eng._runtime[bot_id]  # noqa: SLF001
    assert rt.counters.evaluated == 1
    # stale book -> counted UNKNOWN on the next window, never a candidate (QA-01)
    st.feeds["TEST/USD"].state = "STALE"
    clock.advance(minutes=1)
    await eng.tick()
    assert rt.counters.unknown == 1


async def test_stale_data_blocks_entry(session_factory):
    bot_id, st, clock, eng = await _setup(session_factory)
    st.feeds["TEST/USD"].state = "STALE"
    await eng.tick()
    assert await _one(session_factory, Position, Position.bot_id == bot_id) is None


async def test_rug_check_rejects_cancel_only_pair(session_factory):
    bot_id, st, clock, eng = await _setup(session_factory)
    st.rules["TEST/USD"].status = "cancel_only"
    await eng.tick()
    cand = await _one(session_factory, Candidate, Candidate.bot_id == bot_id)
    assert cand.status is CandidateStatus.REJECTED
    assert await _one(session_factory, Position, Position.bot_id == bot_id) is None
    rejected = await _one(session_factory, DomainEvent, DomainEvent.event_type == "RISK_REJECTED")
    assert "pair_status" in rejected.payload["failed"]


async def test_entry_idempotent_and_atomic(session_factory, monkeypatch):
    """QA-05: the same authorisation never produces two orders; a crash leaves nothing."""
    bot_id, st, clock, eng = await _setup(session_factory)
    real = executor_mod.post_transaction
    calls = {"n": 0}

    async def crash_on_fill(db, **kw):
        if kw["kind"].value == "BUY_FILL":
            calls["n"] += 1
            raise RuntimeError("simulated crash after submit")
        return await real(db, **kw)

    monkeypatch.setattr(executor_mod, "post_transaction", crash_on_fill)
    await eng.tick()
    assert calls["n"] == 1
    assert await _one(session_factory, Order, Order.id.is_not(None)) is None       # rolled back
    assert await _one(session_factory, OrderIntent, OrderIntent.id.is_not(None)) is None
    assert await _ledger_sum(session_factory, bot_id) == 0

    monkeypatch.setattr(executor_mod, "post_transaction", real)
    sizing = await _one(session_factory, SizingDecision, SizingDecision.approved.is_(True))
    async with session_factory() as db:
        cand = await db.get(Candidate, sizing.candidate_id)
        sz = await db.get(SizingDecision, sizing.id)
        first = await execute_entry(db, sizing=sz, candidate=cand, book=st.book("TEST/USD"),
                                    book_fresh=True, max_slippage_pct=D("0.5"),
                                    initial_stop_pct=D(3), take_profit_r=D(2), now=NOW)
        await db.commit()
        second = await execute_entry(db, sizing=sz, candidate=cand, book=st.book("TEST/USD"),
                                     book_fresh=True, max_slippage_pct=D("0.5"),
                                     initial_stop_pct=D(3), take_profit_r=D(2), now=NOW)
    assert first.status == "FILLED" and second.status == "DUPLICATE"
    async with session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(Order)) == 1


async def test_partial_entry_and_partial_exit(session_factory):
    """QA-06 / QA-18: thin book → partial fill; exit in two steps."""
    bot_id, st, clock, eng = await _setup(session_factory)
    # sizing sees enough depth, but the book thins out before execution (latency)
    original_sleep = eng._sleep  # noqa: SLF001

    async def thin_then_sleep(s):
        set_book(st, asks=((101, 5), (103, 100)), bids=((100.9, 50),))
        await original_sleep(s)

    eng._sleep = thin_then_sleep  # noqa: SLF001
    await eng.tick()
    pos = await _one(session_factory, Position, Position.bot_id == bot_id)
    assert pos.quantity == D(5)                                    # remainder never counted
    assert await _one(session_factory, Order, Order.status == "PARTIALLY_FILLED") is not None
    assert await _ledger_sum(session_factory, bot_id) == 0
    bal = await account_balances_for(session_factory, bot_id)
    assert bal[(ACCOUNT_CASH_RESERVED, "USD")] == 0               # reservation fully released

    set_book(st, asks=((90.2, 50),), bids=((90, 2), (80, 100)))   # stop hit, thin bids
    clock.advance(seconds=10)
    await eng.tick()
    pos = await _one(session_factory, Position, Position.id == pos.id)
    assert pos.status is PositionStatus.REDUCING and pos.quantity == D(3)
    set_book(st, asks=((90.2, 50),), bids=((90, 50),))
    clock.advance(seconds=10)
    await eng.tick()
    pos = await _one(session_factory, Position, Position.id == pos.id)
    assert pos.status is PositionStatus.CLOSED and pos.exit_reason == "STOP"
    assert pos.realized_pnl_net < 0
    assert await _ledger_sum(session_factory, bot_id) == 0


async def test_time_exit_and_deferred_exit(session_factory):
    bot_id, st, clock, eng = await _setup(session_factory)
    await eng.tick()
    pos = await _one(session_factory, Position, Position.bot_id == bot_id)
    clock.advance(minutes=61)
    st.feeds["TEST/USD"].state = "STALE"                           # no reliable price
    await eng.tick()
    assert (await _one(session_factory, Position, Position.id == pos.id)).status is PositionStatus.OPEN
    st.feeds["TEST/USD"].state = "LIVE"
    st.rules["TEST/USD"].status = "cancel_only"                     # cannot sell
    clock.advance(seconds=10)
    await eng.tick()
    assert (await _one(session_factory, Position, Position.id == pos.id)).status is PositionStatus.OPEN
    st.rules["TEST/USD"].status = "reduce_only"                     # sell allowed -> STATUS exit
    clock.advance(seconds=10)
    await eng.tick()
    pos = await _one(session_factory, Position, Position.id == pos.id)
    assert pos.status is PositionStatus.CLOSED and pos.exit_reason == "STATUS"


async def _command(session_factory, bot_id, command, key):
    async with session_factory() as db:
        await create_command(db, bot_id=bot_id, command=command, actor="user:1", idempotency_key=key)
        await db.commit()


async def test_pause_blocks_entries_not_exits_and_stop_closes(session_factory):
    bot_id, st, clock, eng = await _setup(session_factory)
    await eng.tick()
    pos = await _one(session_factory, Position, Position.bot_id == bot_id)
    await _command(session_factory, bot_id, CommandType.PAUSE_ENTRIES, "p1")
    set_book(st, asks=((90.2, 50),), bids=((90, 50),))
    clock.advance(seconds=10)
    await eng.tick()
    assert (await _one(session_factory, Bot, Bot.id == bot_id)).status is BotStatus.PAUSED
    assert (await _one(session_factory, Position, Position.id == pos.id)).exit_reason == "STOP"
    await _command(session_factory, bot_id, CommandType.STOP, "s1")
    clock.advance(seconds=10)
    await eng.tick()
    bot = await _one(session_factory, Bot, Bot.id == bot_id)
    assert bot.status is BotStatus.STOPPED
    session = await _one(session_factory, BotSession, BotSession.bot_id == bot_id)
    assert session.ended_at is not None


async def test_emergency_stop_liquidates(session_factory):
    """QA-09."""
    bot_id, st, clock, eng = await _setup(session_factory)
    await eng.tick()
    await _command(session_factory, bot_id, CommandType.EMERGENCY_STOP, "e1")
    clock.advance(seconds=10)
    await eng.tick()           # command -> STOPPING, exits liquidate in the same tick
    clock.advance(seconds=10)
    await eng.tick()           # equity step -> STOPPED
    pos = await _one(session_factory, Position, Position.bot_id == bot_id)
    assert pos.status is PositionStatus.CLOSED and pos.exit_reason == "EMERGENCY"
    assert (await _one(session_factory, Bot, Bot.id == bot_id)).status is BotStatus.STOPPED


async def test_autostart_once_never_live(session_factory):
    """ADR-013 / QA-19."""
    bot_id, st, clock, eng = await _setup(session_factory)
    async with session_factory() as db:
        seed_bot = await db.get(Bot, bot_id)
        from app.domain.trading.models import RiskPolicyVersion, StrategyVersion
        sv = await db.get(StrategyVersion, seed_bot.strategy_version_id)
        pv = await db.get(RiskPolicyVersion, seed_bot.risk_policy_version_id)
        live = await create_bot(db, name="live-1", environment=Environment.LIVE, strategy_version=sv,
                                risk_policy_version=pv, actor="user:1")
        from app.domain.trading.bots import transition_bot
        await transition_bot(db, live, BotStatus.READY, actor="user:1", reason="t")
        await db.commit()
    await eng.tick()
    starts = await _all(session_factory, Command, Command.command == CommandType.START)
    assert len(starts) == 1 and starts[0].bot_id == bot_id and starts[0].actor == "system:worker"
    # manual START of the LIVE bot is rejected
    await _command(session_factory, live.id, CommandType.START, "live-start")
    clock.advance(seconds=10)
    await eng.tick()
    rejected = await _one(session_factory, Command, Command.idempotency_key == "live-start")
    assert rejected.status is CommandStatus.REJECTED and rejected.rejection_code == "LIVE_NOT_APPROVED"
    # a seed bot put back to READY after having had a session is NOT auto-started again
    async with session_factory() as db:
        b = await db.get(Bot, bot_id)
        b.status = BotStatus.READY
        await db.commit()
    clock.advance(seconds=10)
    await eng.tick()
    assert len(await _all(session_factory, Command, Command.command == CommandType.START,
                          Command.actor == "system:worker")) == 1


async def test_equity_snapshot_and_daily_loss_block(session_factory):
    bot_id, st, clock, eng = await _setup(session_factory)
    async with session_factory() as db:
        db.add(EquitySnapshot(bot_id=bot_id, at=NOW.replace(hour=0, minute=1), cash=D(11000),
                              cash_reserved=D(0), positions_mark_value=D(0), equity=D(11000),
                              realized_pnl_net=D(0), unrealized_pnl=D(0), quality="OK"))
        await db.commit()
    await eng.tick()           # equity 10000 vs 11000 at day start: -9 % > 3 % -> blocked
    assert await _one(session_factory, Position, Position.bot_id == bot_id) is None
    sizing = await _one(session_factory, SizingDecision, SizingDecision.approved.is_(False))
    assert "MAX_DAILY_LOSS" in sizing.rejection_reasons
    snaps = await _all(session_factory, EquitySnapshot, EquitySnapshot.bot_id == bot_id)
    assert any(s.equity == D(10000) for s in snaps)


async def test_reconciliation_failure_degrades_bot(session_factory):
    bot_id, st, clock, eng = await _setup(session_factory)
    await eng.tick()
    async with session_factory() as db:   # tamper: position cost no longer matches ledger
        pos = await db.scalar(select(Position).where(Position.bot_id == bot_id))
        pos.cost_basis = pos.cost_basis + 1
        await db.commit()
    clock.advance(minutes=6)
    await eng.tick()
    assert (await _one(session_factory, Bot, Bot.id == bot_id)).status is BotStatus.DEGRADED
    failed = await _one(session_factory, DomainEvent, DomainEvent.event_type == "RECONCILIATION_FAILED")
    assert "INVENTORY_MISMATCH" in failed.payload["issues"][0]


async def test_thin_market_noise_is_not_a_candidate(session_factory):
    """ADR-014: a huge spike on a thin window (live-run SUI/USD case) is filtered out."""
    bot_id, st, clock, eng = await _setup(session_factory, baseline_per_min=D(10),
                                          current_per_min=D(425), current_trades=2)
    await eng.tick()
    assert await _one(session_factory, Candidate, Candidate.bot_id == bot_id) is None
