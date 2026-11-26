"""PostgreSQL-specific checks (10-backend/03): NUMERIC(38,18), advisory journal lock,
CHECK constraints and the full seed on the production dialect.

Run with: TEST_POSTGRES_URL=postgresql+asyncpg://user:pass@host:port/db pytest -m postgres
"""

import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.trading.enums import IntentPurpose, SessionStatus, Side
from app.domain.trading.events import append_event, read_events_after
from app.domain.trading.ledger import equity_summary
from app.domain.trading.models import BotSession, CashMovement, Instrument, OrderIntent
from app.domain.trading.seed import run_seed

pytestmark = [pytest.mark.anyio, pytest.mark.postgres]


@pytest.fixture
def pg_sessions(pg_engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(pg_engine, class_=AsyncSession, expire_on_commit=False)


async def test_seed_and_numeric_precision_on_postgres(pg_sessions):
    async with pg_sessions() as db:
        result = await run_seed(db)
        await run_seed(db)  # idempotent
        summary = await equity_summary(db, result.bot.id, positions_mark_value=None)
        assert summary.equity == Decimal("10000")
        mv = await db.scalar(select(CashMovement))
        assert isinstance(mv.amount, Decimal)


async def test_concurrent_appends_keep_commit_order(pg_sessions):
    """The advisory lock makes `position` order == commit order across sessions."""

    async def writer(i: int) -> None:
        async with pg_sessions() as db:
            await append_event(
                db, event_type="T", aggregate_type="agg", aggregate_id="shared",
                payload={"i": str(i)}, source="test",
            )
            await asyncio.sleep(0.01)
            await db.commit()

    await asyncio.gather(*(writer(i) for i in range(20)))
    async with pg_sessions() as db:
        events = await read_events_after(db, 0)
    assert [e.aggregate_sequence for e in events] == list(range(1, 21))
    assert [e.position for e in events] == sorted(e.position for e in events)


async def test_inv01_check_constraint_on_postgres(pg_sessions):
    async with pg_sessions() as db:
        result = await run_seed(db)
        bot = result.bot
        session = BotSession(
            bot_id=bot.id, environment=bot.environment,
            strategy_version_id=bot.strategy_version_id, strategy_sha256="0" * 64,
            risk_policy_version_id=bot.risk_policy_version_id, risk_policy_sha256="0" * 64,
            status=SessionStatus.RUNNING,
        )
        db.add(session)
        db.add(Instrument(id="kraken_spot:P/USD", venue="kraken_spot", symbol="P/USD", base="P",
                          quote="USD", pair_decimals=4, lot_decimals=8, status="online"))
        await db.flush()
        db.add(OrderIntent(
            id=uuid.uuid4(), bot_id=bot.id, session_id=session.id,
            instrument_id="kraken_spot:P/USD", side=Side.BUY, purpose=IntentPurpose.ENTRY,
            quantity=Decimal("1"), idempotency_key="pg-k1", status="CREATED",
        ))
        with pytest.raises(IntegrityError):
            await db.flush()


async def test_bot_name_unique_only_among_non_deleted_bots(pg_sessions):
    """ADR-032: partial unique index `uq_hl_bot_name_live`."""
    from app.domain.trading.models import Bot
    from app.domain.trading.types import utcnow

    async with pg_sessions() as db:
        bot = (await run_seed(db)).bot

        def twin():
            return Bot(name=bot.name, environment=bot.environment, status=bot.status,
                       strategy_version_id=bot.strategy_version_id,
                       risk_policy_version_id=bot.risk_policy_version_id)

        dup = twin()
        db.add(dup)
        with pytest.raises(IntegrityError):
            await db.flush()
        await db.rollback()
        bot = (await run_seed(db)).bot
        bot.deleted_at = utcnow()
        await db.flush()
        db.add(twin())
        await db.flush()                     # the name of a deleted bot is free again
