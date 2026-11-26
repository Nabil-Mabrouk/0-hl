"""Idempotent bootstrap data — spec 10-backend/03 « Amorçage des données ».

1. risk policy `paper-default` v1, published by `system:seed`;
2. strategy `volume-spike` v0.1.0, published by `system:seed` for PAPER only
   (audited `self_approved=true`, reason « amorçage M1 »);
3. bot `paper-kraken-01`, PAPER, READY;
4. initial paper deposit of 10 000 USD (ADR-010);
5–7. ADR-027 observation bots: policy `rotation` v1, strategies `breakout-1h`, `rebound`,
   `rotation`, `pullback` v0.1.0 and bots `paper-kraken-02..05` (READY, never started here).
   Anything that already exists is left untouched; a failure is logged and skipped.

The JSON files in `seed_data/` are copies of the spec examples; a test fails if
they drift from `docs/.../examples/` (the documentation is the source of truth).
"""

from __future__ import annotations

import json
import logging
import uuid
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
from app.domain.trading.audit import record_audit
from app.domain.trading.models import (
    AuditEvent,
    Bot,
    BotSession,
    CashMovement,
    RiskPolicyVersion,
    Strategy,
    StrategyVersion,
)
from app.domain.trading.types import utcnow
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

OBSERVATION_POLICIES = (("risk-policy-rotation.json", "Rotation"),)
# (strategy file, display name, bot name)
OBSERVATION_BOTS = (
    ("strategy-breakout-1h.json", "Breakout 1h (STRAT-02)", "paper-kraken-02"),
    ("strategy-rebound.json", "Rebound after excess (STRAT-06)", "paper-kraken-03"),
    ("strategy-rotation.json", "Rotation of the strongest (STRAT-11)", "paper-kraken-04"),
    ("strategy-pullback.json", "Pullback in an uptrend (STRAT-12)", "paper-kraken-05"),
)
# ADR-028 (site in English): display names given by the first ADR-027 seed, renamed once
RENAMED = {
    "Rebond après excès (STRAT-06)": "Rebound after excess (STRAT-06)",
    "Rotation des plus fortes (STRAT-11)": "Rotation of the strongest (STRAT-11)",
    "Repli dans une tendance haussière (STRAT-12)": "Pullback in an uptrend (STRAT-12)",
}
OBSERVATION_REASON = "ADR-027 observation paper"

log = logging.getLogger("hl.seed")

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


async def seed_bot(db: AsyncSession, name: str) -> Bot | None:
    """The bot the seed created under `name`, whatever it is called now (ADR-032 point 4).

    Identity = the `BOT_CREATED` audit of `system:seed` carrying that starting name, so a
    renamed bot is not recreated and a deleted one does not come back. Bots that predate the
    audit trail (none in production) fall back to the name."""
    rows = (await db.execute(select(AuditEvent.entity_id, AuditEvent.diff).where(
        AuditEvent.actor == SEED_ACTOR, AuditEvent.action == "BOT_CREATED",
        AuditEvent.entity_type == "bot").order_by(AuditEvent.occurred_at))).all()
    for entity_id, diff in rows:
        if (diff or {}).get("name") == name:
            bot = await db.get(Bot, uuid.UUID(str(entity_id)))
            if bot is not None:
                return bot
    return await db.scalar(select(Bot).where(Bot.name == name, Bot.deleted_at.is_(None)))


async def _ensure_bot(
    db: AsyncSession, sv: StrategyVersion, pv: RiskPolicyVersion
) -> Bot:
    bot = await seed_bot(db, SEED_BOT_NAME)
    if bot is None:
        bot = await create_bot(
            db, name=SEED_BOT_NAME, environment=Environment.PAPER, strategy_version=sv,
            risk_policy_version=pv, actor=SEED_ACTOR,
        )
    if bot.status is BotStatus.CREATED:
        await transition_bot(db, bot, BotStatus.READY, actor=SEED_ACTOR, reason=SEED_REASON)
    # ADR-014: a seed bot that never had a session follows the current seed versions
    never_started = await db.scalar(
        select(BotSession.id).where(BotSession.bot_id == bot.id).limit(1)) is None
    # M2.3: a binding chosen by the operator (BOT_REBOUND) is never undone by the seed
    rebound = await db.scalar(select(AuditEvent.id).where(
        AuditEvent.entity_type == "bot", AuditEvent.entity_id == str(bot.id),
        AuditEvent.action == "BOT_REBOUND").limit(1)) is not None
    if never_started and not rebound and bot.deleted_at is None and (bot.strategy_version_id, bot.risk_policy_version_id) != (sv.id, pv.id):
        before = {"strategy_version_id": bot.strategy_version_id,
                  "risk_policy_version_id": bot.risk_policy_version_id}
        bot.strategy_version_id, bot.risk_policy_version_id = sv.id, pv.id
        bot.row_version += 1
        bot.updated_at = utcnow()
        await db.flush()
        await record_audit(db, actor=SEED_ACTOR, action="SEED_REBIND", entity_type="bot",
                           entity_id=bot.id, reason="ADR-014 bot jamais démarré",
                           diff={"from": before, "to": {"strategy_version_id": sv.id,
                                                        "risk_policy_version_id": pv.id}})
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


async def _observation_step(db: AsyncSession, label: str, step) -> None:
    """One ADR-027 element in its own savepoint: a failure is logged, never raised."""
    try:
        async with db.begin_nested():
            await step()
    except Exception as exc:  # noqa: BLE001 — the worker must start anyway
        log.warning("seed: observation %s skipped: %s: %s", label, type(exc).__name__, exc)


async def _ensure_observation(db: AsyncSession) -> None:
    """ADR-027 (10-backend/03 « Bots d'observation »): create what is missing, touch nothing else."""
    from app.domain.trading import lifecycle as lc

    for file, name in OBSERVATION_POLICIES:
        config = _load(SEED_DIR / file)

        async def policy(config=config, name=name) -> None:
            if await get_policy_version(db, config["policy_id"], config["version"]) is None:
                pv = await create_policy_version(db, config=config, name=name, actor=SEED_ACTOR)
                await publish_policy_version(db, pv, actor=SEED_ACTOR, reason=OBSERVATION_REASON,
                                             self_approved=True)
                log.info("seed: policy %s@%s published", config["policy_id"], config["version"])

        await _observation_step(db, f"policy {file}", policy)

    async def rename() -> None:
        for s in (await db.scalars(select(Strategy).where(Strategy.name.in_(list(RENAMED))))).all():
            before, s.name = s.name, RENAMED[s.name]
            await db.flush()
            await record_audit(db, actor=SEED_ACTOR, action="STRATEGY_RENAMED", entity_type="strategy",
                               entity_id=s.id, reason="ADR-028 site in English",
                               diff={"name": {"before": before, "after": s.name}})
            log.info("seed: strategy %s renamed to %s", s.id, s.name)

    await _observation_step(db, "rename", rename)

    for file, name, bot_name in OBSERVATION_BOTS:
        config = _load(SEED_DIR / file)

        async def strategy(config=config, name=name) -> None:
            if await get_strategy_version(db, config["strategy_id"], config["version"]) is None:
                sv = await create_strategy_version(db, config=config, name=name, actor=SEED_ACTOR)
                await publish_strategy_version(db, sv, actor=SEED_ACTOR, reason=OBSERVATION_REASON,
                                               self_approved=True)
                log.info("seed: strategy %s@%s published", config["strategy_id"], config["version"])

        async def bot(config=config, bot_name=bot_name) -> None:
            if await seed_bot(db, bot_name) is not None:   # renamed or deleted: never recreated
                return
            sv = await get_strategy_version(db, config["strategy_id"], config["version"])
            pv = await db.scalar(
                select(RiskPolicyVersion)
                .where(RiskPolicyVersion.policy_id == config["risk_policy_id"],
                       RiskPolicyVersion.status == PolicyVersionStatus.PUBLISHED)
                .order_by(RiskPolicyVersion.created_at.desc()).limit(1))
            if sv is None or pv is None:
                raise LookupError(f"{config['strategy_id']} or policy {config['risk_policy_id']} missing")
            await lc.create_paper_bot(db, name=bot_name, strategy_version=sv, risk_policy_version=pv,
                                      capital=PAPER_INITIAL_CAPITAL_USD, actor=SEED_ACTOR,
                                      reason=OBSERVATION_REASON)
            log.info("seed: bot %s created (READY)", bot_name)

        await _observation_step(db, f"strategy {file}", strategy)
        await _observation_step(db, f"bot {bot_name}", bot)


async def run_seed(db: AsyncSession, *, observation: bool = False) -> SeedResult:
    """Create/complete the bootstrap data; safe to run any number of times.
    `observation` (the worker passes True) adds the ADR-027 steps 5–7."""
    pv = await _ensure_policy(db)
    sv = await _ensure_strategy(db)
    bot = await _ensure_bot(db, sv, pv)
    await _ensure_initial_deposit(db, bot)
    await db.commit()
    if observation:
        await _ensure_observation(db)
        await db.commit()
    return SeedResult(strategy_version=sv, policy_version=pv, bot=bot)
