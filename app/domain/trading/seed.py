"""Idempotent bootstrap data — spec 10-backend/03 « Amorçage des données ».

1. risk policy `paper-default` v1, published by `system:seed`;
2. strategy `volume-spike` v0.1.0, published by `system:seed` for PAPER only
   (audited `self_approved=true`, reason « amorçage M1 »);
3. bot `paper-kraken-01`, PAPER, READY;
4. initial paper deposit of 10 000 USD (ADR-010).

The JSON files in `seed_data/` are copies of the spec examples; a test fails if
they drift from `docs/.../examples/` (the documentation is the source of truth).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.trading.audit import system_actor
from app.domain.trading.bots import create_bot, transition_bot
from app.domain.trading.enums import (
    BotStatus,
    CashMovementKind,
    Environment,
    PolicyVersionStatus,
    StrategyVersionStatus,
)
from app.domain.trading.ledger import record_cash_movement
from app.domain.trading.models import Bot, CashMovement, RiskPolicyVersion, StrategyVersion
from app.domain.trading.versioning import (
    create_policy_version,
    create_strategy_version,
    get_policy_version,
    get_strategy_version,
    publish_policy_version,
    publish_strategy_version,
)

SEED_DIR = Path(__file__).parent / "seed_data"
STRATEGY_FILE = SEED_DIR / "strategy-volume-spike.json"
POLICY_FILE = SEED_DIR / "risk-policy-paper-default.json"

SEED_ACTOR = system_actor("seed")
SEED_BOT_NAME = "paper-kraken-01"
PAPER_INITIAL_CAPITAL_USD = Decimal("10000")  # ADR-010
SEED_REASON = "amorçage M1 (10-backend/03) — PAPER uniquement"


@dataclass(frozen=True)
class SeedResult:
    strategy_version: StrategyVersion
    policy_version: RiskPolicyVersion
    bot: Bot


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


async def _ensure_policy(db: AsyncSession) -> RiskPolicyVersion:
    config = _load(POLICY_FILE)
    pv = await get_policy_version(db, config["policy_id"], config["version"])
    if pv is None:
        pv = await create_policy_version(
            db, config=config, name="Paper default", actor=SEED_ACTOR
        )
    if pv.status is PolicyVersionStatus.DRAFT:
        await publish_policy_version(
            db, pv, actor=SEED_ACTOR, reason=SEED_REASON, self_approved=True
        )
    return pv


async def _ensure_strategy(db: AsyncSession) -> StrategyVersion:
    config = _load(STRATEGY_FILE)
    sv = await get_strategy_version(db, config["strategy_id"], config["version"])
    if sv is None:
        sv = await create_strategy_version(
            db, config=config, name="Volume Spike Momentum (Kraken Spot)", actor=SEED_ACTOR
        )
    if sv.status is StrategyVersionStatus.DRAFT:
        await publish_strategy_version(
            db, sv, actor=SEED_ACTOR, reason=SEED_REASON, self_approved=True
        )
    return sv


async def _ensure_bot(
    db: AsyncSession, sv: StrategyVersion, pv: RiskPolicyVersion
) -> Bot:
    bot = await db.scalar(select(Bot).where(Bot.name == SEED_BOT_NAME))
    if bot is None:
        bot = await create_bot(
            db, name=SEED_BOT_NAME, environment=Environment.PAPER, strategy_version=sv,
            risk_policy_version=pv, actor=SEED_ACTOR,
        )
    if bot.status is BotStatus.CREATED:
        await transition_bot(db, bot, BotStatus.READY, actor=SEED_ACTOR, reason=SEED_REASON)
    return bot


async def _ensure_initial_deposit(db: AsyncSession, bot: Bot) -> None:
    has_deposit = await db.scalar(
        select(CashMovement.id).where(
            CashMovement.bot_id == bot.id, CashMovement.kind == CashMovementKind.DEPOSIT
        ).limit(1)
    )
    if has_deposit is None:
        await record_cash_movement(
            db, bot_id=bot.id, kind=CashMovementKind.DEPOSIT, amount=PAPER_INITIAL_CAPITAL_USD,
            actor=SEED_ACTOR, reason="capital paper initial (ADR-010)",
        )


async def run_seed(db: AsyncSession) -> SeedResult:
    """Create/complete the bootstrap data; safe to run any number of times."""
    pv = await _ensure_policy(db)
    sv = await _ensure_strategy(db)
    bot = await _ensure_bot(db, sv, pv)
    await _ensure_initial_deposit(db, bot)
    await db.commit()
    return SeedResult(strategy_version=sv, policy_version=pv, bot=bot)
