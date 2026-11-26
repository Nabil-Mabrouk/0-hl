"""Strategy / risk-policy versions and bot binding — M2.3 (spec 05-strategies/04 « API »)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.domain.trading import lifecycle as lc
from app.domain.trading.agents.features import FEATURE_CATALOGUE
from app.domain.trading.api.common import ALL_ROLES, DomainRoute, ok, parse_uuid
from app.domain.trading.api.snapshot import bot_json
from app.domain.trading.enums import TradingRole
from app.domain.trading.errors import NotFound, PermissionDenied, ValidationFailed
from app.domain.trading.models import Bot, RiskPolicyVersion, StrategyVersion
from app.domain.trading.rbac import TradingPrincipal, require_trading_roles
from app.domain.trading.versioning import create_policy_version, create_strategy_version

router = APIRouter(prefix="/api/v1", tags=["0-hl versions"], route_class=DomainRoute)

DB = Annotated[AsyncSession, Depends(get_db)]
Reader = Annotated[TradingPrincipal, Depends(require_trading_roles(*ALL_ROLES))]
Analyst = Annotated[TradingPrincipal, Depends(require_trading_roles(
    TradingRole.ANALYST, audit_action="STRATEGY_DRAFT"))]
RiskOfficer = Annotated[TradingPrincipal, Depends(require_trading_roles(
    TradingRole.RISK_OFFICER, audit_action="VERSION_GOVERNANCE"))]
Binder = Annotated[TradingPrincipal, Depends(require_trading_roles(
    TradingRole.OPERATOR, TradingRole.RISK_OFFICER, audit_action="BOT_BIND"))]
Renamer = Annotated[TradingPrincipal, Depends(require_trading_roles(
    TradingRole.OPERATOR, audit_action="BOT_RENAME"))]
Deleter = Annotated[TradingPrincipal, Depends(require_trading_roles(
    TradingRole.OPERATOR, TradingRole.RISK_OFFICER, audit_action="BOT_DELETE"))]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateVersion(_Body):
    config: dict[str, Any] | None = None
    from_version_id: str | None = None
    version: str | None = Field(default=None, min_length=1, max_length=32)
    name: str | None = Field(default=None, max_length=128)


class EditVersion(_Body):
    config: dict[str, Any]


class Reasoned(_Body):
    reason: str = Field(min_length=1, max_length=500)


class PublishStrategy(Reasoned):
    backtest_ref: str | None = Field(default=None, max_length=128)


class ValidateBody(_Body):
    config: dict[str, Any]


class CreateBotBody(Reasoned):
    name: str = Field(min_length=1, max_length=64)
    strategy_version_id: str
    risk_policy_version_id: str
    capital_usd: str = "10000"


class RenameBotBody(Reasoned):
    name: str = Field(min_length=1, max_length=64)
    row_version: int | None = None


class DeleteBotBody(Reasoned):
    row_version: int | None = None


class BindBody(Reasoned):
    strategy_version_id: str
    risk_policy_version_id: str


def strategy_json(v: StrategyVersion, bound: dict | None = None) -> dict[str, Any]:
    return {"id": v.id, "strategy_id": v.strategy_id, "version": v.version, "status": v.status.value,
            "config_sha256": v.config_sha256, "config": v.config, "created_by": v.created_by,
            "created_at": v.created_at, "published_by": v.published_by,
            "published_at": v.published_at, "bots": (bound or {}).get(v.id, [])}


def policy_json(v: RiskPolicyVersion, bound: dict | None = None) -> dict[str, Any]:
    pending = None
    if v.publish_requested_at is not None:
        pending = {"requested_at": v.publish_requested_at, "requested_by": v.publish_requested_by,
                   "reason": v.publish_request_reason,
                   "confirm_after": v.publish_requested_at + lc.REVIEW_DELAY}
    return {"id": v.id, "policy_id": v.policy_id, "version": v.version, "status": v.status.value,
            "config_sha256": v.config_sha256, "config": v.config, "created_by": v.created_by,
            "created_at": v.created_at, "published_by": v.published_by,
            "published_at": v.published_at, "effective_from": v.effective_from,
            "pending_publication": pending, "bots": (bound or {}).get(v.id, [])}


def _etag(v: StrategyVersion | RiskPolicyVersion) -> dict[str, str]:
    return {"ETag": f'"{v.config_sha256}"'}


async def _sv(db: AsyncSession, vid: str) -> StrategyVersion:
    v = await db.get(StrategyVersion, parse_uuid(vid, "strategy version"))
    if v is None:
        raise NotFound(f"unknown strategy version {vid}")
    return v


async def _pv(db: AsyncSession, vid: str) -> RiskPolicyVersion:
    v = await db.get(RiskPolicyVersion, parse_uuid(vid, "risk policy version"))
    if v is None:
        raise NotFound(f"unknown risk policy version {vid}")
    return v


# ---------------------------------------------------------------- catalogue

@router.get("/features")
async def features(p: Reader):
    return ok({"items": [{"name": n, "unit": u, "description": d}
                         for n, (u, d) in FEATURE_CATALOGUE.items()]})


# ---------------------------------------------------------------- strategies

@router.get("/strategies/diff")
async def strategy_diff(p: Reader, db: DB, a: str = Query(...), b: str = Query(...)):
    va, vb = await _sv(db, a), await _sv(db, b)
    if va.strategy_id != vb.strategy_id:
        raise ValidationFailed("both versions must belong to the same strategy")
    return ok({"a": strategy_json(va), "b": strategy_json(vb),
               "changes": lc.diff_configs(va.config, vb.config)})


@router.get("/strategies/{strategy_id}/versions/{version}")
async def strategy_detail(strategy_id: str, version: str, p: Reader, db: DB):
    v = await db.scalar(select(StrategyVersion).where(
        StrategyVersion.strategy_id == strategy_id, StrategyVersion.version == version))
    if v is None:
        raise NotFound(f"unknown strategy {strategy_id} v{version}")
    return ok(strategy_json(v, await lc.bots_bound_to(db)), headers=_etag(v))


@router.post("/strategies/versions/validate")
async def strategy_validate_config(body: ValidateBody, p: Reader, db: DB):
    errors = await lc.validate_strategy(db, body.config)
    return ok({"valid": not errors, "errors": errors})


@router.post("/strategies/versions")
async def strategy_create(body: CreateVersion, p: Analyst, db: DB):
    if body.from_version_id:
        if not body.version:
            raise ValidationFailed("`version` is required to duplicate")
        v = await lc.duplicate_strategy_version(db, await _sv(db, body.from_version_id),
                                                version=body.version, actor=p.actor)
    elif body.config is not None:
        v = await create_strategy_version(db, config={**body.config, "status": "DRAFT"},
                                          name=body.name or str(body.config.get("strategy_id")),
                                          actor=p.actor)
    else:
        raise ValidationFailed("give `config` or `from_version_id` + `version`")
    await db.commit()
    return ok(strategy_json(v), status=201, headers=_etag(v))


@router.put("/strategies/versions/{vid}")
async def strategy_edit(vid: str, body: EditVersion, p: Analyst, db: DB,
                        if_match: Annotated[str | None, Header(alias="If-Match")] = None):
    v = await lc.edit_strategy_draft(db, await _sv(db, vid), config=body.config, actor=p.actor,
                                     if_match=if_match)
    await db.commit()
    return ok(strategy_json(v), headers=_etag(v))


@router.delete("/strategies/versions/{vid}")
async def strategy_delete(vid: str, p: Analyst, db: DB):
    await lc.delete_strategy_draft(db, await _sv(db, vid), actor=p.actor)
    await db.commit()
    return ok({"deleted": vid})


@router.post("/strategies/versions/{vid}/validate")
async def strategy_validate(vid: str, p: Reader, db: DB):
    v = await _sv(db, vid)
    errors = await lc.validate_strategy(db, v.config)
    return ok({"valid": not errors, "errors": errors})


@router.post("/strategies/versions/{vid}/publish")
async def strategy_publish(vid: str, body: PublishStrategy, p: RiskOfficer, db: DB):
    v = await lc.publish_strategy(db, await _sv(db, vid), actor=p.actor, reason=body.reason,
                                  backtest_ref=body.backtest_ref)
    await db.commit()
    return ok(strategy_json(v))


@router.post("/strategies/versions/{vid}/deprecate")
async def strategy_deprecate(vid: str, body: Reasoned, p: RiskOfficer, db: DB):
    v = await lc.deprecate_strategy(db, await _sv(db, vid), actor=p.actor, reason=body.reason)
    await db.commit()
    return ok(strategy_json(v))


# ---------------------------------------------------------------- risk policies

@router.get("/risk-policies/diff")
async def policy_diff(p: Reader, db: DB, a: str = Query(...), b: str = Query(...)):
    va, vb = await _pv(db, a), await _pv(db, b)
    if va.policy_id != vb.policy_id:
        raise ValidationFailed("both versions must belong to the same policy")
    return ok({"a": policy_json(va), "b": policy_json(vb),
               "changes": lc.policy_diff(va.config, vb.config)})


@router.get("/risk-policies/{policy_id}/versions/{version}")
async def policy_detail(policy_id: str, version: str, p: Reader, db: DB):
    v = await db.scalar(select(RiskPolicyVersion).where(
        RiskPolicyVersion.policy_id == policy_id, RiskPolicyVersion.version == version))
    if v is None:
        raise NotFound(f"unknown risk policy {policy_id} v{version}")
    body = policy_json(v, await lc.bots_bound_to(db))
    if v.status.value == "DRAFT":
        ref = await lc.latest_published_policy(db, v.policy_id, exclude_id=v.id)
        body["compared_to"] = None if ref is None else {"id": ref.id, "version": ref.version}
        body["changes"] = [] if ref is None else lc.policy_diff(ref.config, v.config)
    return ok(body, headers=_etag(v))


@router.post("/risk-policies/versions/validate")
async def policy_validate_config(body: ValidateBody, p: Reader):
    errors = await lc.validate_policy(body.config)
    return ok({"valid": not errors, "errors": errors})


@router.post("/risk-policies/versions")
async def policy_create(body: CreateVersion, p: RiskOfficer, db: DB):
    if body.from_version_id:
        if not body.version:
            raise ValidationFailed("`version` is required to duplicate")
        v = await lc.duplicate_policy_version(db, await _pv(db, body.from_version_id),
                                              version=body.version, actor=p.actor)
    elif body.config is not None:
        v = await create_policy_version(db, config=body.config,
                                        name=body.name or str(body.config.get("policy_id")),
                                        actor=p.actor)
    else:
        raise ValidationFailed("give `config` or `from_version_id` + `version`")
    await db.commit()
    return ok(policy_json(v), status=201, headers=_etag(v))


@router.put("/risk-policies/versions/{vid}")
async def policy_edit(vid: str, body: EditVersion, p: RiskOfficer, db: DB,
                      if_match: Annotated[str | None, Header(alias="If-Match")] = None):
    v = await lc.edit_policy_draft(db, await _pv(db, vid), config=body.config, actor=p.actor,
                                   if_match=if_match)
    await db.commit()
    return ok(policy_json(v), headers=_etag(v))


@router.delete("/risk-policies/versions/{vid}")
async def policy_delete(vid: str, p: RiskOfficer, db: DB):
    await lc.delete_policy_draft(db, await _pv(db, vid), actor=p.actor)
    await db.commit()
    return ok({"deleted": vid})


@router.post("/risk-policies/versions/{vid}/publish")
async def policy_publish(vid: str, body: Reasoned, p: RiskOfficer, db: DB):
    out = await lc.request_policy_publication(db, await _pv(db, vid), actor=p.actor,
                                              reason=body.reason)
    await db.commit()
    data = policy_json(out.version) | {"outcome": out.status, "loosened": out.loosened,
                                       "confirm_after": out.confirm_after}
    return ok(data, status=200 if out.status == "PUBLISHED" else 202)


@router.post("/risk-policies/versions/{vid}/publish/confirm")
async def policy_confirm(vid: str, p: RiskOfficer, db: DB):
    v = await lc.confirm_policy_publication(db, await _pv(db, vid), actor=p.actor)
    await db.commit()
    return ok(policy_json(v))


@router.post("/risk-policies/versions/{vid}/deprecate")
async def policy_deprecate(vid: str, body: Reasoned, p: RiskOfficer, db: DB):
    v = await lc.deprecate_policy(db, await _pv(db, vid), actor=p.actor, reason=body.reason)
    await db.commit()
    return ok(policy_json(v))


# ---------------------------------------------------------------- bots

@router.post("/bots")
async def create_bot_route(body: CreateBotBody, p: Binder, db: DB):
    """M2.4 (06-agents/04): new PAPER bot, READY, funded; starts only on an operator START."""
    from decimal import Decimal, InvalidOperation
    if not (p.has_any(TradingRole.OPERATOR) and p.has_any(TradingRole.RISK_OFFICER)):
        raise PermissionDenied("creating a bot requires OPERATOR and RISK_OFFICER")
    try:
        capital = Decimal(body.capital_usd)
    except InvalidOperation as exc:
        raise ValidationFailed("invalid bot", details={"errors": [
            {"path": "capital_usd", "message": "montant décimal attendu"}]}) from exc
    bot = await lc.create_paper_bot(db, name=body.name, strategy_version=await _sv(db, body.strategy_version_id),
                                    risk_policy_version=await _pv(db, body.risk_policy_version_id),
                                    capital=capital, actor=p.actor, reason=body.reason)
    await db.commit()
    return ok(bot_json(bot) | {"capital_usd": capital}, status=201, headers={"ETag": f'"{bot.row_version}"'})


@router.post("/bots/{bot_id}/bind")
async def bind(bot_id: str, body: BindBody, p: Binder, db: DB,
               if_match: Annotated[str | None, Header(alias="If-Match")] = None):
    if not (p.has_any(TradingRole.OPERATOR) and p.has_any(TradingRole.RISK_OFFICER)):
        raise PermissionDenied("binding a bot requires OPERATOR and RISK_OFFICER")
    bot = await db.get(Bot, parse_uuid(bot_id, "bot"))
    if bot is None:
        raise NotFound(f"unknown bot {bot_id}")
    expected = None
    if if_match is not None:
        try:
            expected = int(if_match.strip('"'))
        except ValueError as exc:
            raise ValidationFailed("If-Match must be the bot row_version") from exc
    bot = await lc.bind_bot(db, bot, strategy_version=await _sv(db, body.strategy_version_id),
                            risk_policy_version=await _pv(db, body.risk_policy_version_id),
                            actor=p.actor, reason=body.reason, if_match=expected)
    await db.commit()
    return ok(bot_json(bot), headers={"ETag": f'"{bot.row_version}"'})


async def _bot(db: AsyncSession, bot_id: str) -> Bot:
    bot = await db.get(Bot, parse_uuid(bot_id, "bot"))
    if bot is None:
        raise NotFound(f"unknown bot {bot_id}")
    return bot


@router.patch("/bots/{bot_id}")
async def rename_bot_route(bot_id: str, body: RenameBotBody, p: Renamer, db: DB):
    """ADR-032 (06-agents/04 « Gestion des bots »): rename at any status, audited."""
    bot = await lc.rename_bot(db, await _bot(db, bot_id), name=body.name, actor=p.actor,
                              reason=body.reason, row_version=body.row_version)
    await db.commit()
    return ok(bot_json(bot), headers={"ETag": f'"{bot.row_version}"'})


@router.post("/bots/{bot_id}/delete")
async def delete_bot_route(bot_id: str, body: DeleteBotBody, p: Deleter, db: DB):
    """ADR-032: delete = archive (READY or STOPPED, nothing open); history kept."""
    if not (p.has_any(TradingRole.OPERATOR) and p.has_any(TradingRole.RISK_OFFICER)):
        raise PermissionDenied("deleting a bot requires OPERATOR and RISK_OFFICER")
    bot = await lc.delete_bot(db, await _bot(db, bot_id), actor=p.actor, reason=body.reason,
                              row_version=body.row_version)
    await db.commit()
    return ok(bot_json(bot))
