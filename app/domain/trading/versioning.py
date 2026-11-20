"""Versioned strategy and risk-policy configuration (INV-06, spec 03-ecrans/UI-04).

A version's config is editable only while DRAFT; once published it is
immutable and identified by `config_sha256` (status excluded from the hash).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.trading.agents.features import FEATURE_CATALOGUE
from app.domain.trading.audit import record_audit
from app.domain.trading.config_schemas import (
    stored_policy_config,
    stored_strategy_config,
    validate_risk_policy_config,
    validate_strategy_config,
)
from app.domain.trading.enums import PolicyVersionStatus, StrategyVersionStatus
from app.domain.trading.errors import StateConflict, ValidationFailed, VersionImmutable
from app.domain.trading.events import append_event
from app.domain.trading.models import RiskPolicy, RiskPolicyVersion, Strategy, StrategyVersion
from app.domain.trading.types import sha256_hex, utcnow

_PUBLISHABLE_STRATEGY = {StrategyVersionStatus.DRAFT, StrategyVersionStatus.APPROVED}


async def strategy_coherence_errors(db: AsyncSession, config: Any) -> list[dict[str, str]]:
    """Rules beyond the JSON schema (05-strategies/04 « Règles » 2): known feature and unit,
    sane exits, existing risk policy. Returns [] when coherent."""
    errors: list[dict[str, str]] = []
    for i, cond in enumerate(config.entry.conditions):
        path = f"entry.conditions[{i}]"
        known = FEATURE_CATALOGUE.get(cond.feature)
        if known is None:
            errors.append({"path": f"{path}.feature", "message":
                           f"feature inconnue « {cond.feature} » (catalogue : {', '.join(FEATURE_CATALOGUE)})"})
        elif cond.unit != known[0]:
            errors.append({"path": f"{path}.unit",
                           "message": f"unité « {cond.unit} » au lieu de « {known[0]} » pour {cond.feature}"})

    def dec(value: str | None) -> Decimal | None:
        try:
            return None if value is None else Decimal(value)
        except InvalidOperation:
            return None

    stop = dec(config.exit.initial_stop_pct)
    if stop is None or not (Decimal(0) < stop < Decimal(100)):
        errors.append({"path": "exit.initial_stop_pct", "message": "doit être > 0 et < 100"})
    tp = dec(config.exit.take_profit_r)
    if config.exit.take_profit_r is not None and (tp is None or tp <= 0):
        errors.append({"path": "exit.take_profit_r", "message": "doit être > 0"})
    if config.exit.max_hold_seconds is not None and config.exit.max_hold_seconds < 60:
        errors.append({"path": "exit.max_hold_seconds", "message": "doit être ≥ 60"})
    if await db.get(RiskPolicy, config.risk_policy_id) is None:
        errors.append({"path": "risk_policy_id",
                       "message": f"policy « {config.risk_policy_id} » inexistante"})
    return errors


async def _ensure_coherent(db: AsyncSession, config: Any) -> None:
    errors = await strategy_coherence_errors(db, config)
    if errors:
        raise ValidationFailed("incoherent strategy config", details={"errors": errors})


# --------------------------------------------------------------------------- #
# Strategies
# --------------------------------------------------------------------------- #
async def get_strategy_version(
    db: AsyncSession, strategy_id: str, version: str
) -> StrategyVersion | None:
    return await db.scalar(
        select(StrategyVersion).where(
            StrategyVersion.strategy_id == strategy_id, StrategyVersion.version == version
        )
    )


async def create_strategy_version(
    db: AsyncSession, *, config: dict[str, Any], name: str, actor: str
) -> StrategyVersion:
    parsed = validate_strategy_config(config)
    if parsed.status != "DRAFT":
        raise ValidationFailed("a new strategy version is always created as DRAFT")
    await _ensure_coherent(db, parsed)
    if await get_strategy_version(db, parsed.strategy_id, parsed.version) is not None:
        raise StateConflict(f"strategy {parsed.strategy_id} v{parsed.version} already exists")
    if await db.get(Strategy, parsed.strategy_id) is None:
        db.add(Strategy(id=parsed.strategy_id, name=name, created_by=actor))
        await db.flush()
    stored = stored_strategy_config(parsed)
    sv = StrategyVersion(
        strategy_id=parsed.strategy_id,
        version=parsed.version,
        status=StrategyVersionStatus.DRAFT,
        config=stored,
        config_sha256=sha256_hex(stored),
        created_by=actor,
    )
    db.add(sv)
    await db.flush()
    await record_audit(
        db, actor=actor, action="STRATEGY_VERSION_CREATED", entity_type="strategy_version",
        entity_id=sv.id, diff={"strategy_id": sv.strategy_id, "version": sv.version,
                               "config_sha256": sv.config_sha256},
    )
    return sv


async def update_strategy_draft(
    db: AsyncSession, sv: StrategyVersion, *, config: dict[str, Any], actor: str
) -> StrategyVersion:
    if sv.status is not StrategyVersionStatus.DRAFT:
        raise VersionImmutable(f"strategy version {sv.id} is {sv.status.value}")
    parsed = validate_strategy_config({**config, "status": "DRAFT"})
    if (parsed.strategy_id, parsed.version) != (sv.strategy_id, sv.version):
        raise ValidationFailed("strategy_id/version cannot change on a draft update")
    await _ensure_coherent(db, parsed)
    stored = stored_strategy_config(parsed)
    before = sv.config_sha256
    sv.config = stored
    sv.config_sha256 = sha256_hex(stored)
    await db.flush()
    await record_audit(
        db, actor=actor, action="STRATEGY_DRAFT_UPDATED", entity_type="strategy_version",
        entity_id=sv.id, diff={"config_sha256": {"from": before, "to": sv.config_sha256}},
    )
    return sv


async def publish_strategy_version(
    db: AsyncSession,
    sv: StrategyVersion,
    *,
    actor: str,
    reason: str,
    self_approved: bool,
    backtest_ref: str | None = None,
) -> StrategyVersion:
    if sv.status not in _PUBLISHABLE_STRATEGY:
        raise StateConflict(f"cannot publish a {sv.status.value} strategy version")
    await _ensure_coherent(db, validate_strategy_config({**sv.config, "status": "DRAFT"}))
    previous = sv.status
    sv.status = StrategyVersionStatus.PUBLISHED
    sv.published_by = actor
    sv.published_at = utcnow()
    await db.flush()
    await record_audit(
        db, actor=actor, action="STRATEGY_PUBLISHED", entity_type="strategy_version",
        entity_id=sv.id, reason=reason, self_approved=self_approved,
        diff={"status": {"from": previous.value, "to": sv.status.value},
              "config_sha256": sv.config_sha256}
        | ({"backtest_ref": backtest_ref} if backtest_ref else {}),
    )
    await append_event(
        db, event_type="STRATEGY_PUBLISHED", aggregate_type="strategy_version",
        aggregate_id=sv.id, source="api",
        payload={"strategy_id": sv.strategy_id, "version": sv.version,
                 "config_sha256": sv.config_sha256},
    )
    return sv


# --------------------------------------------------------------------------- #
# Risk policies
# --------------------------------------------------------------------------- #
async def get_policy_version(
    db: AsyncSession, policy_id: str, version: str
) -> RiskPolicyVersion | None:
    return await db.scalar(
        select(RiskPolicyVersion).where(
            RiskPolicyVersion.policy_id == policy_id, RiskPolicyVersion.version == version
        )
    )


async def create_policy_version(
    db: AsyncSession, *, config: dict[str, Any], name: str, actor: str
) -> RiskPolicyVersion:
    parsed = validate_risk_policy_config(config)
    if await get_policy_version(db, parsed.policy_id, parsed.version) is not None:
        raise StateConflict(f"risk policy {parsed.policy_id} v{parsed.version} already exists")
    if await db.get(RiskPolicy, parsed.policy_id) is None:
        db.add(RiskPolicy(id=parsed.policy_id, name=name))
        await db.flush()
    stored = stored_policy_config(parsed)
    pv = RiskPolicyVersion(
        policy_id=parsed.policy_id,
        version=parsed.version,
        status=PolicyVersionStatus.DRAFT,
        config=stored,
        config_sha256=sha256_hex(stored),
        created_by=actor,
    )
    db.add(pv)
    await db.flush()
    await record_audit(
        db, actor=actor, action="POLICY_VERSION_CREATED", entity_type="risk_policy_version",
        entity_id=pv.id, diff={"policy_id": pv.policy_id, "version": pv.version,
                               "config_sha256": pv.config_sha256},
    )
    return pv


async def update_policy_draft(
    db: AsyncSession, pv: RiskPolicyVersion, *, config: dict[str, Any], actor: str
) -> RiskPolicyVersion:
    if pv.status is not PolicyVersionStatus.DRAFT:
        raise VersionImmutable(f"risk policy version {pv.id} is {pv.status.value}")
    parsed = validate_risk_policy_config(config)
    if (parsed.policy_id, parsed.version) != (pv.policy_id, pv.version):
        raise ValidationFailed("policy_id/version cannot change on a draft update")
    stored = stored_policy_config(parsed)
    before = pv.config_sha256
    pv.config = stored
    pv.config_sha256 = sha256_hex(stored)
    await db.flush()
    await record_audit(
        db, actor=actor, action="POLICY_DRAFT_UPDATED", entity_type="risk_policy_version",
        entity_id=pv.id, diff={"config_sha256": {"from": before, "to": pv.config_sha256}},
    )
    return pv


async def publish_policy_version(
    db: AsyncSession, pv: RiskPolicyVersion, *, actor: str, reason: str, self_approved: bool
) -> RiskPolicyVersion:
    if pv.status is not PolicyVersionStatus.DRAFT:
        raise StateConflict(f"cannot publish a {pv.status.value} risk policy version")
    pv.status = PolicyVersionStatus.PUBLISHED
    pv.published_by = actor
    pv.published_at = utcnow()
    pv.effective_from = pv.published_at
    await db.flush()
    await record_audit(
        db, actor=actor, action="POLICY_UPDATED", entity_type="risk_policy_version",
        entity_id=pv.id, reason=reason, self_approved=self_approved,
        diff={"status": {"from": "DRAFT", "to": "PUBLISHED"}, "config_sha256": pv.config_sha256},
    )
    await append_event(
        db, event_type="POLICY_UPDATED", aggregate_type="risk_policy_version",
        aggregate_id=pv.id, source="api",
        payload={"policy_id": pv.policy_id, "version": pv.version,
                 "config_sha256": pv.config_sha256},
    )
    return pv
