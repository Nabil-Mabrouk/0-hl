"""T-M1.1-12 — idempotent seed; seed data must equal the spec examples (ADR-000)."""

import json
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.domain.trading.enums import BotStatus, Environment, StrategyVersionStatus
from app.domain.trading.ledger import equity_summary
from app.domain.trading.models import (
    AuditEvent,
    Bot,
    CashMovement,
    DomainEvent,
    LedgerEntry,
    RiskPolicyVersion,
    StrategyVersion,
)
from app.domain.trading.seed import POLICY_FILE, STRATEGY_FILE, run_seed

from .factories import SPEC_ROOT

pytestmark = pytest.mark.anyio

TABLES = (StrategyVersion, RiskPolicyVersion, Bot, CashMovement, LedgerEntry, AuditEvent, DomainEvent)


async def _counts(db) -> dict:
    return {t.__tablename__: await db.scalar(select(func.count()).select_from(t)) for t in TABLES}


async def test_seed_creates_expected_state(db):
    result = await run_seed(db)
    assert result.bot.environment is Environment.PAPER
    assert result.bot.status is BotStatus.READY
    assert result.strategy_version.status is StrategyVersionStatus.PUBLISHED
    summary = await equity_summary(db, result.bot.id, positions_mark_value=None)
    assert summary.equity == Decimal("10000")
    assert summary.realized_pnl_gross == Decimal(0)
    published = await db.scalar(
        select(AuditEvent).where(AuditEvent.action == "STRATEGY_PUBLISHED")
    )
    assert published.self_approved is True and published.actor == "system:seed"


async def test_seed_is_idempotent(session_factory):
    async with session_factory() as db:
        await run_seed(db)
        first = await _counts(db)
    async with session_factory() as db:
        await run_seed(db)
        second = await _counts(db)
    assert first == second


@pytest.mark.parametrize(
    "seed_file,spec_file",
    [
        (STRATEGY_FILE, "examples/strategy-volume-spike.json"),
        (POLICY_FILE, "examples/risk-policy-paper-default.json"),
    ],
)
def test_seed_data_matches_spec_examples(seed_file, spec_file):
    seed = json.loads(seed_file.read_text(encoding="utf-8"))
    spec = json.loads((SPEC_ROOT / spec_file).read_text(encoding="utf-8"))
    assert seed == spec, f"{seed_file.name} drifted from the spec ({spec_file})"
