"""Bot lifecycle — spec 10-backend/03 « Transitions de bot autorisées ».

`STOPPED → RUNNING` is never direct (a new check goes through READY); starting a
LIVE bot raises `LIVE_NOT_APPROVED` until an ADR opens LIVE (ADR-009).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.trading.audit import record_audit
from app.domain.trading.enums import (
    BotStatus,
    Environment,
    PolicyVersionStatus,
    StrategyVersionStatus,
)
from app.domain.trading.errors import LiveNotApproved, StateConflict, StrategyNotPublished
from app.domain.trading.events import append_event
from app.domain.trading.models import Bot, RiskPolicyVersion, StrategyVersion
from app.domain.trading.types import utcnow

S = BotStatus
ALLOWED_TRANSITIONS: dict[BotStatus, frozenset[BotStatus]] = {
    S.CREATED: frozenset({S.READY, S.FAILED}),
    S.READY: frozenset({S.RUNNING, S.FAILED}),
    S.RUNNING: frozenset({S.PAUSED, S.STOPPING, S.DEGRADED, S.FAILED}),
    S.PAUSED: frozenset({S.RUNNING, S.STOPPING, S.DEGRADED, S.FAILED}),
    S.DEGRADED: frozenset({S.RUNNING, S.PAUSED, S.STOPPING, S.FAILED}),
    S.STOPPING: frozenset({S.STOPPED, S.FAILED}),
    S.STOPPED: frozenset({S.READY}),
    S.FAILED: frozenset({S.READY}),
}

# LIVE stays locked until an ADR explicitly opens phase P6 (ADR-009).
LIVE_TRADING_APPROVED = False


def _event_type(source: BotStatus, target: BotStatus) -> str:
    if (source, target) == (S.RUNNING, S.PAUSED):
        return "BOT_PAUSED"
    if (source, target) == (S.PAUSED, S.RUNNING):
        return "BOT_RESUMED"
    return "BOT_STATUS_CHANGED"


async def create_bot(
    db: AsyncSession,
    *,
    name: str,
    environment: Environment,
    strategy_version: StrategyVersion,
    risk_policy_version: RiskPolicyVersion,
    actor: str,
) -> Bot:
    if strategy_version.status is not StrategyVersionStatus.PUBLISHED:
        raise StrategyNotPublished(
            f"strategy version {strategy_version.id} is {strategy_version.status.value}"
        )
    if risk_policy_version.status is not PolicyVersionStatus.PUBLISHED:
        raise StrategyNotPublished(
            f"risk policy version {risk_policy_version.id} is {risk_policy_version.status.value}"
        )
    bot = Bot(
        name=name,
        environment=environment,
        status=BotStatus.CREATED,
        strategy_version_id=strategy_version.id,
        risk_policy_version_id=risk_policy_version.id,
    )
    db.add(bot)
    await db.flush()
    await record_audit(
        db, actor=actor, action="BOT_CREATED", entity_type="bot", entity_id=bot.id,
        diff={"name": name, "environment": environment.value,
              "strategy_version_id": strategy_version.id,
              "risk_policy_version_id": risk_policy_version.id},
    )
    await append_event(
        db, event_type="BOT_STATUS_CHANGED", aggregate_type="bot", aggregate_id=bot.id,
        bot_id=bot.id, source="api",
        payload={"from": None, "to": BotStatus.CREATED.value, "reason": "created"},
    )
    return bot


async def transition_bot(
    db: AsyncSession,
    bot: Bot,
    target: BotStatus,
    *,
    actor: str,
    reason: str,
    expected_row_version: int | None = None,
) -> Bot:
    if expected_row_version is not None and expected_row_version != bot.row_version:
        raise StateConflict(
            "bot was modified concurrently",
            details={"expected": expected_row_version, "actual": bot.row_version},
        )
    source = bot.status
    if target not in ALLOWED_TRANSITIONS.get(source, frozenset()):
        raise StateConflict(
            f"transition {source.value} -> {target.value} is not allowed",
            details={"from": source.value, "to": target.value},
        )
    if (
        target is S.RUNNING
        and source is S.READY
        and bot.environment is Environment.LIVE
        and not LIVE_TRADING_APPROVED
    ):
        await record_audit(
            db, actor=actor, action="LIVE_START_REFUSED", entity_type="bot", entity_id=bot.id,
            reason=reason,
        )
        raise LiveNotApproved("LIVE trading is not approved (ADR-009)")

    bot.status = target
    bot.row_version += 1
    bot.updated_at = utcnow()
    await db.flush()
    await record_audit(
        db, actor=actor, action="BOT_TRANSITION", entity_type="bot", entity_id=bot.id,
        reason=reason, diff={"status": {"from": source.value, "to": target.value}},
    )
    await append_event(
        db, event_type=_event_type(source, target), aggregate_type="bot", aggregate_id=bot.id,
        bot_id=bot.id, source="api",
        payload={"from": source.value, "to": target.value, "reason": reason},
    )
    return bot
