"""AI services API — `/api/v1/ai` (spec 03-ecrans/UI-16 « API »)."""

from __future__ import annotations

import os
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Callable

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import get_db
from app.domain.ai import budget as bud
from app.domain.ai import runner
from app.domain.ai.models import AiAlert, AiRun, AiSettings, AiStep
from app.domain.ai.proxy import ProxyClient
from app.domain.ai.services import SERVICES
from fastapi.responses import JSONResponse

from app.domain.trading.api.common import ALL_ROLES, DomainRoute, parse_uuid
from app.domain.trading.audit import record_audit
from app.domain.trading.enums import TradingRole
from app.domain.trading.errors import NotFound, ValidationFailed, VersionConflict
from app.domain.trading.events import append_event
from app.domain.trading.rbac import TradingPrincipal, require_trading_roles
from app.domain.trading.types import utcnow

router = APIRouter(prefix="/api/v1/ai", tags=["0-hl AI services"], route_class=DomainRoute)

DB = Annotated[AsyncSession, Depends(get_db)]
Reader = Annotated[TradingPrincipal, Depends(require_trading_roles(*ALL_ROLES))]
Analyst = Annotated[TradingPrincipal, Depends(require_trading_roles(TradingRole.ANALYST, audit_action="AI_RUN"))]
Admin = Annotated[TradingPrincipal, Depends(require_trading_roles(TradingRole.ADMIN, audit_action="AI_SETTINGS"))]


def _sessions() -> async_sessionmaker[AsyncSession]:
    from app.core.database import SessionLocal
    return SessionLocal


# test seams: the session factory of background runs and the proxy client
SESSIONS: Callable[[], async_sessionmaker[AsyncSession]] = _sessions
PROXY: Callable[[], ProxyClient] = ProxyClient


def ok(data: Any, status: int = 200, headers: dict[str, str] | None = None) -> JSONResponse:
    """Like the domain `ok`, but the journal keeps the floats sent by the model and the proxy."""
    return JSONResponse(content=runner.plain(data), status_code=status, headers=headers)


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunBody(_Body):
    service_id: str = Field(min_length=1, max_length=64)
    input: dict[str, Any]


class SettingsBody(_Body):
    daily_budget_usd: str | None = None
    models: dict[str, str] | None = None
    prices: dict[str, dict[str, str]] | None = None


def _money(v: Decimal | None) -> str | None:
    return None if v is None else format(v, "f")


def run_json(r: AiRun, steps: list[AiStep] | None = None) -> dict[str, Any]:
    out = {"id": r.id, "service_id": r.service_id, "service_version": r.service_version,
           "agent_id": r.agent_id, "agent_version": r.agent_version,
           "instructions_sha256": r.instructions_sha256, "model": r.model, "input": r.input,
           "status": r.status, "output_text": r.output_text, "output_json": r.output_json,
           "tokens_in": r.tokens_in, "tokens_out": r.tokens_out,
           "cost_actual_usd": _money(r.cost_actual_usd), "cost_estimated_usd": _money(r.cost_estimated_usd),
           "cost_usd": _money(r.cost_actual_usd + r.cost_estimated_usd),
           "cost_estimated_only": r.cost_estimated_only, "model_calls": r.model_calls,
           "tool_calls": r.tool_calls, "started_at": r.started_at, "finished_at": r.finished_at,
           "duration_ms": None if r.finished_at is None else int((r.finished_at - r.started_at).total_seconds() * 1000),
           "error_code": r.error_code, "error_detail": r.error_detail, "correlation_id": r.correlation_id}
    if steps is not None:
        out["steps"] = [{"seq": s.seq, "kind": s.kind, "at": s.at, "detail": s.detail,
                         "tokens_in": s.tokens_in, "tokens_out": s.tokens_out,
                         "cost_actual_usd": _money(s.cost_actual_usd),
                         "cost_estimated_usd": _money(s.cost_estimated_usd),
                         "duration_ms": s.duration_ms, "error_code": s.error_code} for s in steps]
    return out


def settings_json(s: AiSettings) -> dict[str, Any]:
    return {"daily_budget_usd": _money(s.daily_budget_usd), "max_budget_usd": _money(bud.MAX_BUDGET),
            "models": {sid: bud.model_for(s, sid) for sid in SERVICES}, "prices": s.prices,
            "version": s.version, "updated_at": s.updated_at, "email_configured": bool(os.environ.get("ALERT_EMAIL_TO"))}


@router.get("/services")
async def services(p: Reader, db: DB):
    s = await bud.load_settings(db)
    return ok({"items": [svc.as_json(bud.model_for(s, svc.id)) for svc in SERVICES.values()]})


@router.get("/models")
async def models(p: Reader, db: DB):
    s = await bud.load_settings(db)
    error = None
    try:
        ids = await PROXY().models()
    except Exception as exc:  # noqa: BLE001 — shown, never fatal
        ids, error = [], f"{type(exc).__name__}: {exc}"[:300]
    return ok({"items": [{"id": m, "price": (s.prices or {}).get(m), "selectable": bud.price(s, m) is not None}
                         for m in ids], "proxy_error": error})


@router.post("/runs")
async def create_run(body: RunBody, p: Analyst, db: DB,
                     idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None):
    run, created = await runner.create_run(db, user_id=p.user_id, service_id=body.service_id,
                                           raw_input=body.input, idempotency_key=idempotency_key, now=utcnow())
    await db.commit()
    if created:
        runner.launch(SESSIONS(), run.id, PROXY())
    return ok(run_json(run), status=202)


@router.get("/runs")
async def list_runs(p: Reader, db: DB, service_id: str | None = Query(None), status: str | None = Query(None),
                    since: datetime | None = Query(None), until: datetime | None = Query(None),
                    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    if await runner.abandon_stale(db, utcnow()):
        await db.commit()
    stmt = select(AiRun)
    if service_id:
        stmt = stmt.where(AiRun.service_id == service_id)
    if status:
        stmt = stmt.where(AiRun.status == status)
    if since:
        stmt = stmt.where(AiRun.started_at >= since)
    if until:
        stmt = stmt.where(AiRun.started_at < until)
    rows = (await db.scalars(stmt.order_by(AiRun.started_at.desc()))).all()
    return ok({"items": [run_json(r) for r in rows[offset:offset + limit]], "total": len(rows),
               "offset": offset, "limit": limit})


@router.get("/runs/{run_id}")
async def get_run(run_id: str, p: Reader, db: DB):
    if await runner.abandon_stale(db, utcnow()):
        await db.commit()
    run = await db.get(AiRun, parse_uuid(run_id, "run"))
    if run is None:
        raise NotFound(f"unknown run {run_id}")
    steps = (await db.scalars(select(AiStep).where(AiStep.run_id == run.id).order_by(AiStep.seq))).all()
    return ok(run_json(run, list(steps)))


@router.get("/budget")
async def budget(p: Reader, db: DB):
    now = utcnow()
    s = await bud.load_settings(db)
    total, actual, est = await bud.spent_today(db, now)
    cap = Decimal(s.daily_budget_usd)
    alerts = (await db.scalars(select(AiAlert).where(AiAlert.day == bud.day_start(now).date())
                               .order_by(AiAlert.threshold))).all()
    proxy: dict[str, Any] = {}
    try:
        proxy = await PROXY().key_info()
    except Exception as exc:  # noqa: BLE001
        proxy = {"error": f"{type(exc).__name__}: {exc}"[:300]}
    gap = None
    if proxy.get("spend") is not None:
        try:
            diff = abs(Decimal(str(proxy["spend"])) - total)
            gap = diff > Decimal("0.10") and (total == 0 or diff / max(total, Decimal("0.000001")) > Decimal("0.10"))
        except InvalidOperation:
            gap = None
    return ok({"daily_budget_usd": _money(cap), "spent_usd": _money(total), "spent_actual_usd": _money(actual),
               "spent_estimated_usd": _money(est),
               "spent_pct": None if cap <= 0 else f"{min(total / cap * 100, Decimal(999)):.1f}",
               "exhausted": total >= cap, "reset_at": bud.next_reset(now), "proxy": proxy, "proxy_gap": gap,
               "alerts": [{"id": a.id, "threshold": a.threshold, "source": a.source, "spent_usd": _money(a.spent_usd),
                           "created_at": a.created_at, "email_sent": a.email_sent, "email_error": a.email_error,
                           "acked_at": a.acked_at} for a in alerts],
               "email_configured": bool(os.environ.get("ALERT_EMAIL_TO"))})


@router.get("/settings")
async def get_settings(p: Admin, db: DB):
    return ok(settings_json(await bud.load_settings(db)))


@router.patch("/settings")
async def patch_settings(body: SettingsBody, p: Admin, db: DB,
                         if_match: Annotated[str | None, Header(alias="If-Match")] = None):
    s = await db.get(AiSettings, "default")
    current = s or bud.default_settings()
    if if_match is not None and if_match.strip('"') != str(current.version):
        raise VersionConflict(f"réglages modifiés entre-temps (version {current.version})")
    before = settings_json(current)
    prices = dict(current.prices or {}) if body.prices is None else body.prices
    for m, pr in prices.items():
        try:
            if set(pr) != {"input", "output"} or any(Decimal(str(v)) < 0 for v in pr.values()):
                raise ValueError
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise ValidationFailed(f"barème invalide pour {m} : {{input, output}} en USD par million") from exc
    budget_v = current.daily_budget_usd
    if body.daily_budget_usd is not None:
        try:
            budget_v = Decimal(body.daily_budget_usd)
        except InvalidOperation as exc:
            raise ValidationFailed("plafond invalide") from exc
        if not (Decimal(0) <= budget_v <= bud.MAX_BUDGET):
            raise ValidationFailed(f"plafond hors de 0 à {bud.MAX_BUDGET} USD (budget du proxy)")
    models = {**(current.models or {}), **(body.models or {})}
    for sid, m in models.items():
        if sid not in SERVICES:
            raise ValidationFailed(f"service inconnu : {sid}")
        if m not in prices:
            raise ValidationFailed(f"modèle {m} sans barème de prix")
    if s is None:
        s = AiSettings(id="default", version=0)
        db.add(s)
    s.daily_budget_usd, s.models, s.prices = budget_v, models, prices
    s.version = current.version + 1
    s.updated_at, s.updated_by = utcnow(), p.actor
    await db.flush()
    after = settings_json(s)
    diff = {k: {"before": before[k], "after": after[k]} for k in ("daily_budget_usd", "models", "prices")
            if before[k] != after[k]}
    await record_audit(db, actor=p.actor, action="AI_SETTINGS_UPDATED", entity_type="ai_settings",
                       entity_id="default", diff=diff)
    await append_event(db, event_type="AI_SETTINGS_UPDATED", aggregate_type="ai_settings",
                       aggregate_id="default", source="api", payload={"version": s.version, "changed": sorted(diff)})
    total, _, _ = await bud.spent_today(db, utcnow())
    await bud.check_alerts(db, s, total, utcnow())
    await db.commit()
    return ok(settings_json(s), headers={"ETag": f'"{s.version}"'})


@router.post("/alerts/{alert_id}/ack")
async def ack_alert(alert_id: str, p: Admin, db: DB):
    a = await db.get(AiAlert, parse_uuid(alert_id, "alert"))
    if a is None:
        raise NotFound(f"unknown alert {alert_id}")
    if a.acked_at is None:
        a.acked_at, a.acked_by = utcnow(), p.actor
        await record_audit(db, actor=p.actor, action="AI_ALERT_ACKED", entity_type="ai_alert", entity_id=a.id)
        await db.commit()
    return ok({"id": a.id, "acked_at": a.acked_at})
