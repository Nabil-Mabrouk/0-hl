"""Version lifecycle without code — M2.3 (spec 05-strategies/04-versions-sans-code-m2.md).

Duplicate, edit with If-Match, delete drafts, deprecate, diff, loosened-policy review delay,
and binding a PAPER bot to published versions. Shared by the API and the admin command, so
both apply exactly the same rules and audits.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.trading.audit import record_audit
from app.domain.trading.bots import create_bot, transition_bot
from app.domain.trading.config_schemas import validate_risk_policy_config, validate_strategy_config
from app.domain.trading.enums import (
    BotStatus,
    CashMovementKind,
    Environment,
    PolicyVersionStatus,
    PositionStatus,
    StrategyVersionStatus,
)
from app.domain.trading.errors import (
    BotNotRebindable,
    PreconditionFailed,
    ReviewDelayPending,
    StateConflict,
    StrategyNotPublished,
    ValidationFailed,
    VersionImmutable,
)
from app.domain.trading.events import append_event
from app.domain.trading.ledger import record_cash_movement
from app.domain.trading.models import Bot, Position, RiskPolicyVersion, StrategyVersion
from app.domain.trading.types import utcnow
from app.domain.trading.versioning import (
    create_policy_version,
    create_strategy_version,
    publish_policy_version,
    publish_strategy_version,
    strategy_coherence_errors,
    update_policy_draft,
    update_strategy_draft,
)

REVIEW_DELAY = timedelta(minutes=10)                   # ADR-006 solo rule
# M2.4 (06-agents/04, validated 2026-11-20)
MAX_ACTIVE_BOTS = 5
MIN_CAPITAL, MAX_CAPITAL, DEFAULT_CAPITAL = Decimal(100), Decimal(1_000_000), Decimal(10_000)
BOT_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")
REBINDABLE = (BotStatus.READY, BotStatus.STOPPED)
OPEN_POSITIONS = (PositionStatus.PENDING, PositionStatus.OPEN, PositionStatus.REDUCING,
                  PositionStatus.RECONCILIATION_REQUIRED)

# risk-policy fields: "lower" = a lower value is stricter, "higher" = a higher value is stricter
POLICY_DIRECTION: dict[str, str] = {
    "max_trade_risk_pct": "lower", "max_token_exposure_pct": "lower",
    "max_portfolio_exposure_pct": "lower", "max_daily_loss_pct": "lower",
    "max_drawdown_pct": "lower", "max_slippage_pct": "lower", "max_open_positions": "lower",
    "max_spread_bps": "lower", "max_abnormal_move_pct": "lower",
    "authorization_ttl_seconds": "lower",
    "min_volume_24h_usd": "higher", "min_liquidity_usd": "higher", "cooldown_seconds": "higher",
    "kill_switch": "flag",
}
# value used when an optional field is absent (engine behaviour)
_ABSENT = {"cooldown_seconds": 0, "authorization_ttl_seconds": 10, "kill_switch": False}


# ---------------------------------------------------------------- helpers

def _require_draft(v: StrategyVersion | RiskPolicyVersion) -> None:
    if v.status.value != "DRAFT":
        raise VersionImmutable(f"{type(v).__name__} {v.id} is {v.status.value}")


def _require_match(v: StrategyVersion | RiskPolicyVersion, if_match: str | None) -> None:
    if if_match is not None and if_match.strip('"') != v.config_sha256:
        raise PreconditionFailed("the draft was modified since it was read",
                                 details={"current": v.config_sha256})


def _flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else k))
        return out
    if isinstance(obj, list):
        out = {}
        for i, v in enumerate(obj):
            out.update(_flatten(v, f"{prefix}[{i}]"))
        return out or {prefix: []}
    return {prefix: obj}


def diff_configs(a: dict[str, Any], b: dict[str, Any]) -> list[dict[str, Any]]:
    fa, fb = _flatten(a), _flatten(b)
    return [{"path": k, "before": fa.get(k), "after": fb.get(k)}
            for k in sorted(set(fa) | set(fb)) if fa.get(k) != fb.get(k)]


def _num(field: str, value: Any) -> Decimal | bool | None:
    if value is None:
        value = _ABSENT.get(field)
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return Decimal(str(value))


def classify_policy_change(field: str, before: Any, after: Any) -> str:
    """`plus strict` / `plus souple` / `neutre` for one risk-policy field."""
    direction = POLICY_DIRECTION.get(field)
    b, a = _num(field, before), _num(field, after)
    if direction is None or b == a:
        return "neutre"
    if direction == "flag":
        return "plus strict" if a else "plus souple"
    if b is None:                       # a limit appears
        return "plus strict"
    if a is None:                       # a limit disappears
        return "plus souple"
    stricter = a < b if direction == "lower" else a > b
    return "plus strict" if stricter else "plus souple"


def policy_diff(a: dict[str, Any], b: dict[str, Any]) -> list[dict[str, Any]]:
    return [d | {"effect": classify_policy_change(d["path"], d["before"], d["after"])}
            for d in diff_configs(a, b) if d["path"] not in ("version",)]


# ---------------------------------------------------------------- strategies

async def duplicate_strategy_version(db: AsyncSession, source: StrategyVersion, *, version: str,
                                     actor: str) -> StrategyVersion:
    config = copy.deepcopy(source.config) | {"version": version, "status": "DRAFT"}
    sv = await create_strategy_version(db, config=config, name=source.strategy_id, actor=actor)
    await record_audit(db, actor=actor, action="STRATEGY_VERSION_DUPLICATED",
                       entity_type="strategy_version", entity_id=sv.id,
                       diff={"from_version_id": source.id, "version": version})
    return sv


async def edit_strategy_draft(db: AsyncSession, sv: StrategyVersion, *, config: dict[str, Any],
                              actor: str, if_match: str | None) -> StrategyVersion:
    _require_draft(sv)
    _require_match(sv, if_match)
    return await update_strategy_draft(db, sv, config=config, actor=actor)


async def delete_strategy_draft(db: AsyncSession, sv: StrategyVersion, *, actor: str) -> None:
    _require_draft(sv)
    await record_audit(db, actor=actor, action="STRATEGY_DRAFT_DELETED",
                       entity_type="strategy_version", entity_id=sv.id,
                       diff={"strategy_id": sv.strategy_id, "version": sv.version,
                             "config_sha256": sv.config_sha256})
    await db.delete(sv)
    await db.flush()


async def validate_strategy(db: AsyncSession, config: dict[str, Any]) -> list[dict[str, str]]:
    """Full validation without writing: schema errors, then coherence errors (by path)."""
    try:
        parsed = validate_strategy_config({**config, "status": config.get("status") or "DRAFT"})
    except ValidationFailed as exc:
        return [{"path": "", "message": str(exc.details.get("errors") or exc.message)}]
    return await strategy_coherence_errors(db, parsed)


async def publish_strategy(db: AsyncSession, sv: StrategyVersion, *, actor: str, reason: str,
                           backtest_ref: str | None = None) -> StrategyVersion:
    if not reason or not reason.strip():
        raise ValidationFailed("a reason is required to publish")
    return await publish_strategy_version(db, sv, actor=actor, reason=reason, self_approved=True,
                                          backtest_ref=backtest_ref)


async def deprecate_strategy(db: AsyncSession, sv: StrategyVersion, *, actor: str,
                             reason: str) -> StrategyVersion:
    if not reason or not reason.strip():
        raise ValidationFailed("a reason is required to deprecate")
    if sv.status is not StrategyVersionStatus.PUBLISHED:
        raise StateConflict(f"cannot deprecate a {sv.status.value} strategy version")
    sv.status = StrategyVersionStatus.DEPRECATED
    await db.flush()
    await record_audit(db, actor=actor, action="STRATEGY_DEPRECATED", entity_type="strategy_version",
                       entity_id=sv.id, reason=reason, self_approved=True,
                       diff={"status": {"from": "PUBLISHED", "to": "DEPRECATED"}})
    await append_event(db, event_type="STRATEGY_DEPRECATED", aggregate_type="strategy_version",
                       aggregate_id=sv.id, source="api",
                       payload={"strategy_id": sv.strategy_id, "version": sv.version})
    return sv


# ---------------------------------------------------------------- policies

async def duplicate_policy_version(db: AsyncSession, source: RiskPolicyVersion, *, version: str,
                                   actor: str) -> RiskPolicyVersion:
    config = copy.deepcopy(source.config) | {"version": version}
    pv = await create_policy_version(db, config=config, name=source.policy_id, actor=actor)
    await record_audit(db, actor=actor, action="POLICY_VERSION_DUPLICATED",
                       entity_type="risk_policy_version", entity_id=pv.id,
                       diff={"from_version_id": source.id, "version": version})
    return pv


async def edit_policy_draft(db: AsyncSession, pv: RiskPolicyVersion, *, config: dict[str, Any],
                            actor: str, if_match: str | None) -> RiskPolicyVersion:
    _require_draft(pv)
    _require_match(pv, if_match)
    pv = await update_policy_draft(db, pv, config=config, actor=actor)
    if pv.publish_requested_at is not None:   # an edited draft needs a new request
        pv.publish_requested_at = pv.publish_requested_by = pv.publish_request_reason = None
        await db.flush()
    return pv


async def delete_policy_draft(db: AsyncSession, pv: RiskPolicyVersion, *, actor: str) -> None:
    _require_draft(pv)
    await record_audit(db, actor=actor, action="POLICY_DRAFT_DELETED",
                       entity_type="risk_policy_version", entity_id=pv.id,
                       diff={"policy_id": pv.policy_id, "version": pv.version,
                             "config_sha256": pv.config_sha256})
    await db.delete(pv)
    await db.flush()


async def latest_published_policy(db: AsyncSession, policy_id: str,
                                  exclude_id: Any = None) -> RiskPolicyVersion | None:
    stmt = select(RiskPolicyVersion).where(
        RiskPolicyVersion.policy_id == policy_id,
        RiskPolicyVersion.status == PolicyVersionStatus.PUBLISHED)
    if exclude_id is not None:
        stmt = stmt.where(RiskPolicyVersion.id != exclude_id)
    return (await db.scalars(stmt.order_by(RiskPolicyVersion.published_at.desc()).limit(1))).first()


async def loosened_fields(db: AsyncSession, pv: RiskPolicyVersion) -> list[dict[str, Any]]:
    ref = await latest_published_policy(db, pv.policy_id, exclude_id=pv.id)
    if ref is None:
        return []
    return [d for d in policy_diff(ref.config, pv.config) if d["effect"] == "plus souple"]


@dataclass(frozen=True)
class PublishOutcome:
    status: str                       # PUBLISHED | PENDING
    version: RiskPolicyVersion
    loosened: list[dict[str, Any]]
    confirm_after: datetime | None = None


async def request_policy_publication(db: AsyncSession, pv: RiskPolicyVersion, *, actor: str,
                                     reason: str, now: datetime | None = None) -> PublishOutcome:
    """Stricter or equal ⇒ published now; loosened ⇒ pending, confirmable after 10 min."""
    if not reason or not reason.strip():
        raise ValidationFailed("a reason is required to publish")
    if pv.status is not PolicyVersionStatus.DRAFT:
        raise StateConflict(f"cannot publish a {pv.status.value} risk policy version")
    now = now or utcnow()
    loosened = await loosened_fields(db, pv)
    if not loosened:
        await publish_policy_version(db, pv, actor=actor, reason=reason, self_approved=True)
        return PublishOutcome("PUBLISHED", pv, [])
    if pv.publish_requested_at is None:
        pv.publish_requested_at, pv.publish_requested_by = now, actor
        pv.publish_request_reason = reason
        await db.flush()
        await record_audit(db, actor=actor, action="POLICY_PUBLISH_REQUESTED",
                           entity_type="risk_policy_version", entity_id=pv.id, reason=reason,
                           diff={"loosened": loosened, "config_sha256": pv.config_sha256})
    return PublishOutcome("PENDING", pv, loosened, pv.publish_requested_at + REVIEW_DELAY)


async def confirm_policy_publication(db: AsyncSession, pv: RiskPolicyVersion, *, actor: str,
                                     now: datetime | None = None) -> RiskPolicyVersion:
    now = now or utcnow()
    if pv.status is not PolicyVersionStatus.DRAFT or pv.publish_requested_at is None:
        raise StateConflict("no pending publication for this policy version")
    if actor != pv.publish_requested_by:
        raise StateConflict("the publication must be confirmed by the user who requested it")
    confirm_after = pv.publish_requested_at + REVIEW_DELAY
    if now < confirm_after:
        raise ReviewDelayPending("review delay not elapsed",
                                 details={"confirm_after": confirm_after.isoformat()})
    reason = pv.publish_request_reason or "publication confirmée"
    await publish_policy_version(db, pv, actor=actor, reason=reason, self_approved=True)
    await record_audit(db, actor=actor, action="POLICY_PUBLISH_CONFIRMED",
                       entity_type="risk_policy_version", entity_id=pv.id, reason=reason,
                       diff={"requested_at": pv.publish_requested_at.isoformat()})
    return pv


async def deprecate_policy(db: AsyncSession, pv: RiskPolicyVersion, *, actor: str,
                           reason: str) -> RiskPolicyVersion:
    if not reason or not reason.strip():
        raise ValidationFailed("a reason is required to deprecate")
    if pv.status is not PolicyVersionStatus.PUBLISHED:
        raise StateConflict(f"cannot deprecate a {pv.status.value} risk policy version")
    others = await latest_published_policy(db, pv.policy_id, exclude_id=pv.id)
    bound = await db.scalar(select(Bot.id).where(Bot.risk_policy_version_id == pv.id).limit(1))
    if others is None and bound is not None:
        raise StateConflict("only published version of a policy used by a bot")
    pv.status = PolicyVersionStatus.DEPRECATED
    await db.flush()
    await record_audit(db, actor=actor, action="POLICY_DEPRECATED", entity_type="risk_policy_version",
                       entity_id=pv.id, reason=reason, self_approved=True,
                       diff={"status": {"from": "PUBLISHED", "to": "DEPRECATED"}})
    await append_event(db, event_type="POLICY_DEPRECATED", aggregate_type="risk_policy_version",
                       aggregate_id=pv.id, source="api",
                       payload={"policy_id": pv.policy_id, "version": pv.version})
    return pv


async def validate_policy(config: dict[str, Any]) -> list[dict[str, str]]:
    try:
        validate_risk_policy_config(config)
    except ValidationFailed as exc:
        return [{"path": "", "message": str(exc.details.get("errors") or exc.message)}]
    return []


# ---------------------------------------------------------------- bots

async def bind_bot(db: AsyncSession, bot: Bot, *, strategy_version: StrategyVersion,
                   risk_policy_version: RiskPolicyVersion, actor: str, reason: str,
                   if_match: int | None = None) -> Bot:
    """Attach a PAPER bot at rest to published versions (05-strategies/04 « Règles » 7)."""
    if not reason or not reason.strip():
        raise ValidationFailed("a reason is required to bind a bot")
    if if_match is not None and if_match != bot.row_version:
        raise PreconditionFailed("the bot was modified since it was read",
                                 details={"current": bot.row_version})
    if bot.environment is not Environment.PAPER:
        raise BotNotRebindable("only PAPER bots can be bound", details={"environment": bot.environment.value})
    if bot.status not in REBINDABLE:
        raise BotNotRebindable(f"bot is {bot.status.value}: stop it first",
                               details={"status": bot.status.value})
    busy = await db.scalar(select(Position.id).where(
        Position.bot_id == bot.id, Position.status.in_(OPEN_POSITIONS)).limit(1))
    if busy is not None:
        raise BotNotRebindable("bot still has open positions", details={"status": bot.status.value})
    if strategy_version.status is not StrategyVersionStatus.PUBLISHED:
        raise StrategyNotPublished(f"strategy version is {strategy_version.status.value}")
    if risk_policy_version.status is not PolicyVersionStatus.PUBLISHED:
        raise StrategyNotPublished(f"risk policy version is {risk_policy_version.status.value}")
    if strategy_version.config.get("risk_policy_id") != risk_policy_version.policy_id:
        raise ValidationFailed("the policy must be the one named by the strategy",
                               details={"errors": [{"path": "risk_policy_version_id", "message":
                                        f"la stratégie exige la policy « {strategy_version.config.get('risk_policy_id')} »"}]})
    before = {"strategy_version_id": bot.strategy_version_id,
              "risk_policy_version_id": bot.risk_policy_version_id, "status": bot.status.value}
    bot.strategy_version_id = strategy_version.id
    bot.risk_policy_version_id = risk_policy_version.id
    bot.row_version += 1
    bot.updated_at = utcnow()
    await db.flush()
    if bot.status is BotStatus.STOPPED:
        await transition_bot(db, bot, BotStatus.READY, actor=actor, reason=reason)
    after = {"strategy_version_id": bot.strategy_version_id,
             "risk_policy_version_id": bot.risk_policy_version_id, "status": bot.status.value}
    await record_audit(db, actor=actor, action="BOT_REBOUND", entity_type="bot", entity_id=bot.id,
                       reason=reason, self_approved=True, diff={"from": before, "to": after})
    await append_event(db, event_type="BOT_REBOUND", aggregate_type="bot", aggregate_id=bot.id,
                       bot_id=bot.id, source="api",
                       payload={"from": before, "to": after,
                                "strategy": {"strategy_id": strategy_version.strategy_id,
                                             "version": strategy_version.version},
                                "risk_policy": {"policy_id": risk_policy_version.policy_id,
                                                "version": risk_policy_version.version}})
    return bot


async def bots_bound_to(db: AsyncSession) -> dict[Any, list[str]]:
    """version id -> names of the bots attached to it (strategy or policy)."""
    out: dict[Any, list[str]] = {}
    for b in (await db.scalars(select(Bot).order_by(Bot.name))).all():
        out.setdefault(b.strategy_version_id, []).append(b.name)
        out.setdefault(b.risk_policy_version_id, []).append(b.name)
    return out


class BotLimitReached(StateConflict):
    code = "BOT_LIMIT_REACHED"


async def create_paper_bot(db: AsyncSession, *, name: str, strategy_version: StrategyVersion,
                           risk_policy_version: RiskPolicyVersion, capital: Decimal, actor: str,
                           reason: str) -> Bot:
    """New PAPER bot, READY, funded with `capital` (06-agents/04 « Ce que M2.4 ajoute » 2–3).
    It never starts by itself: START is an operator command."""
    errors: list[dict[str, str]] = []
    if not BOT_NAME.match(name or ""):
        errors.append({"path": "name", "message": "3 à 64 caractères : minuscules, chiffres et « - »"})
    if not (MIN_CAPITAL <= capital <= MAX_CAPITAL):
        errors.append({"path": "capital_usd", "message": f"entre {MIN_CAPITAL} et {MAX_CAPITAL} USD"})
    if not reason or not reason.strip():
        errors.append({"path": "reason", "message": "motif obligatoire"})
    if strategy_version.config.get("risk_policy_id") != risk_policy_version.policy_id:
        errors.append({"path": "risk_policy_version_id", "message":
                       f"la stratégie exige la policy « {strategy_version.config.get('risk_policy_id')} »"})
    if errors:
        raise ValidationFailed("invalid bot", details={"errors": errors})
    if strategy_version.status is not StrategyVersionStatus.PUBLISHED:
        raise StrategyNotPublished(f"strategy version is {strategy_version.status.value}")
    if risk_policy_version.status is not PolicyVersionStatus.PUBLISHED:
        raise StrategyNotPublished(f"risk policy version is {risk_policy_version.status.value}")
    if await db.scalar(select(Bot.id).where(Bot.name == name)) is not None:
        raise StateConflict(f"a bot named {name} already exists")
    active = len((await db.scalars(select(Bot.id).where(Bot.status != BotStatus.STOPPED))).all())
    if active >= MAX_ACTIVE_BOTS:
        raise BotLimitReached(f"at most {MAX_ACTIVE_BOTS} bots that are not STOPPED",
                              details={"active": active, "limit": MAX_ACTIVE_BOTS})
    bot = await create_bot(db, name=name, environment=Environment.PAPER, strategy_version=strategy_version,
                           risk_policy_version=risk_policy_version, actor=actor)
    await transition_bot(db, bot, BotStatus.READY, actor=actor, reason=reason)
    await record_cash_movement(db, bot_id=bot.id, kind=CashMovementKind.DEPOSIT, amount=capital,
                               actor=actor, reason=f"capital paper initial — {reason}")
    await record_audit(db, actor=actor, action="BOT_CREATED_BY_OPERATOR", entity_type="bot",
                       entity_id=bot.id, reason=reason, self_approved=True,
                       diff={"name": name, "capital_usd": format(capital, "f"),
                             "strategy": f"{strategy_version.strategy_id}@{strategy_version.version}",
                             "risk_policy": f"{risk_policy_version.policy_id}@{risk_policy_version.version}"})
    return bot


async def initial_capital(db: AsyncSession) -> dict[Any, Decimal]:
    """bot id -> total deposited (paper capital contributed)."""
    from app.domain.trading.models import CashMovement
    rows = await db.execute(select(CashMovement.bot_id, CashMovement.kind, CashMovement.amount))
    out: dict[Any, Decimal] = {}
    for bot_id, kind, amount in rows:
        out[bot_id] = out.get(bot_id, Decimal(0)) + (amount if kind is CashMovementKind.DEPOSIT else -amount)
    return out
