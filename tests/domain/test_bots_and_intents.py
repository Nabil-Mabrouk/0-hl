"""T-M1.1-09/10 — INV-01 on order intents; bot transitions and LIVE lock."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.domain.trading.bots import ALLOWED_TRANSITIONS, transition_bot
from app.domain.trading.enums import (
    BotStatus,
    Environment,
    IntentPurpose,
    SessionStatus,
    Side,
)
from app.domain.trading.errors import LiveNotApproved, StateConflict, StrategyNotPublished
from app.domain.trading.models import AuditEvent, BotSession, DomainEvent, Instrument, OrderIntent
from app.domain.trading.versioning import create_strategy_version

from .factories import ACTOR, make_bot, strategy_config

pytestmark = pytest.mark.anyio


async def _session(db, bot):
    s = BotSession(
        bot_id=bot.id, environment=bot.environment, strategy_version_id=bot.strategy_version_id,
        strategy_sha256="0" * 64, risk_policy_version_id=bot.risk_policy_version_id,
        risk_policy_sha256="0" * 64, status=SessionStatus.RUNNING,
    )
    db.add(s)
    db.add(
        Instrument(
            id="kraken_spot:T/USD", venue="kraken_spot", symbol="T/USD", base="T", quote="USD",
            pair_decimals=4, lot_decimals=8, status="online",
        )
    )
    await db.flush()
    return s


async def test_entry_intent_without_risk_approval_is_refused(db):
    """INV-01 / T-M1.1-09."""
    bot = await make_bot(db)
    session = await _session(db, bot)
    db.add(
        OrderIntent(
            id=uuid.uuid4(), bot_id=bot.id, session_id=session.id,
            instrument_id="kraken_spot:T/USD", side=Side.BUY, purpose=IntentPurpose.ENTRY,
            quantity=Decimal("1"), risk_approval_id=None, idempotency_key="k1", status="CREATED",
        )
    )
    with pytest.raises(IntegrityError):
        await db.flush()


async def test_intent_quantity_must_be_positive(db):
    bot = await make_bot(db)
    session = await _session(db, bot)
    db.add(
        OrderIntent(
            id=uuid.uuid4(), bot_id=bot.id, session_id=session.id,
            instrument_id="kraken_spot:T/USD", side=Side.SELL, purpose=IntentPurpose.EXIT,
            quantity=Decimal("-1"), position_id=None, idempotency_key="k2", status="CREATED",
        )
    )
    with pytest.raises(IntegrityError):
        await db.flush()


async def test_allowed_path_and_events(db):
    bot = await make_bot(db)
    for target in (BotStatus.READY, BotStatus.RUNNING, BotStatus.PAUSED, BotStatus.RUNNING,
                   BotStatus.STOPPING, BotStatus.STOPPED, BotStatus.READY):
        await transition_bot(db, bot, target, actor=ACTOR, reason="test")
    assert bot.row_version == 8
    types = list(
        (await db.scalars(
            select(DomainEvent.event_type).where(DomainEvent.bot_id == bot.id)
            .order_by(DomainEvent.position)
        )).all()
    )
    assert "BOT_PAUSED" in types and "BOT_RESUMED" in types
    seqs = list((await db.scalars(
        select(DomainEvent.aggregate_sequence).where(DomainEvent.bot_id == bot.id)
        .order_by(DomainEvent.position))).all())
    assert seqs == list(range(1, len(seqs) + 1))


@pytest.mark.parametrize(
    "source,target",
    [(s, t) for s in BotStatus for t in BotStatus if t not in ALLOWED_TRANSITIONS[s]],
)
async def test_forbidden_transitions_raise_state_conflict(db, source, target):
    bot = await make_bot(db)
    bot.status = source
    await db.flush()
    with pytest.raises(StateConflict):
        await transition_bot(db, bot, target, actor=ACTOR, reason="test")


async def test_stopped_cannot_go_running_directly(db):
    assert BotStatus.RUNNING not in ALLOWED_TRANSITIONS[BotStatus.STOPPED]


async def test_concurrent_modification_detected(db):
    bot = await make_bot(db)
    with pytest.raises(StateConflict):
        await transition_bot(
            db, bot, BotStatus.READY, actor=ACTOR, reason="t", expected_row_version=99
        )


async def test_live_start_is_locked_and_audited(db):
    """ADR-009 / QA-19."""
    bot = await make_bot(db, name="live-bot", environment=Environment.LIVE)
    await transition_bot(db, bot, BotStatus.READY, actor=ACTOR, reason="t")
    with pytest.raises(LiveNotApproved):
        await transition_bot(db, bot, BotStatus.RUNNING, actor=ACTOR, reason="t")
    assert bot.status is BotStatus.READY
    refused = await db.scalar(
        select(AuditEvent).where(AuditEvent.action == "LIVE_START_REFUSED")
    )
    assert refused is not None


async def test_bot_requires_published_versions(db):
    from app.domain.trading.bots import create_bot
    from app.domain.trading.versioning import create_policy_version

    from .factories import policy_config

    sv = await create_strategy_version(db, config=strategy_config(), name="vs", actor=ACTOR)
    pv = await create_policy_version(db, config=policy_config(), name="pd", actor=ACTOR)
    with pytest.raises(StrategyNotPublished):
        await create_bot(db, name="x", environment=Environment.PAPER, strategy_version=sv,
                         risk_policy_version=pv, actor=ACTOR)
