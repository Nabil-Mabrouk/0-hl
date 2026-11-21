"""Agent loop and run journal (spec 03-ecrans/UI-16 « Agents », « Journal détaillé »).

A run is created RUNNING by the API, then executed in an asyncio task of the API process (the worker
has no access to the proxy). Every step is written and committed before the next one. Limits and
the daily budget are checked before every model call.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Callable

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.ai import budget as bud
from app.domain.ai.agents import Agent
from app.domain.ai.models import AiRun, AiStep
from app.domain.ai.proxy import ChatResult, ProxyClient
from app.domain.ai.services import SERVICES, Service
from app.domain.ai.tools import TOOLS, call_tool, validate_strategy_config_tool, ConfigArgs
from app.domain.trading.errors import DomainError, StateConflict, ValidationFailed
from app.domain.trading.events import append_event
from app.domain.trading.types import utcnow

log = logging.getLogger("hl.ai")
D = Decimal
ZERO = D(0)
MAX_RUNNING = 2
STALE_AFTER = timedelta(minutes=5)        # > run timeout (3 min): a RUNNING run older is abandoned

RUNNING, SUCCEEDED, FAILED = "RUNNING", "SUCCEEDED", "FAILED"
LIMIT_REACHED, BUDGET_BLOCKED, ABANDONED = "LIMIT_REACHED", "BUDGET_BLOCKED", "ABANDONED"
FINAL = {SUCCEEDED, FAILED, LIMIT_REACHED, BUDGET_BLOCKED, ABANDONED}

_tasks: set[asyncio.Task] = set()


def plain(value: Any) -> Any:
    """JSON-safe copy for the journal: Decimal, datetime, UUID as strings; floats kept as sent by
    the model or the proxy (the domain `to_jsonable` refuses floats by design)."""
    return json.loads(json.dumps(value, ensure_ascii=False, default=_default))


def _default(o: Any) -> str:
    return o.isoformat() if hasattr(o, "isoformat") else format(o, "f") if isinstance(o, Decimal) else str(o)


class AiBusy(DomainError):
    http_status = 409
    code = "AI_BUSY"


# ---------------------------------------------------------------- creation

async def abandon_stale(db: AsyncSession, now: datetime) -> int:
    """A RUNNING run older than the run timeout lost its task (API restart): ABANDONED."""
    rows = (await db.scalars(select(AiRun).where(AiRun.status == RUNNING,
                                                 AiRun.started_at < now - STALE_AFTER))).all()
    for r in rows:
        r.status, r.finished_at, r.error_code = ABANDONED, now, "ABANDONED"
        await append_event(db, event_type="AI_RUN_FINISHED", aggregate_type="ai_run", aggregate_id=r.id,
                           source="api", correlation_id=r.correlation_id,
                           payload={"status": ABANDONED, "cost_usd": r.cost_actual_usd + r.cost_estimated_usd})
    return len(rows)


async def create_run(db: AsyncSession, *, user_id: int, service_id: str, raw_input: dict[str, Any],
                     idempotency_key: str | None, now: datetime) -> tuple[AiRun, bool]:
    """(run, created). The caller commits, then calls `launch` when created."""
    service = SERVICES.get(service_id)
    if service is None:
        raise ValidationFailed(f"service inconnu : {service_id}")
    if idempotency_key:
        existing = await db.scalar(select(AiRun).where(AiRun.user_id == user_id,
                                                       AiRun.idempotency_key == idempotency_key))
        if existing is not None:
            return existing, False
    try:
        parsed = service.input_model.model_validate(raw_input)
    except ValidationError as exc:
        raise ValidationFailed("entrée invalide", details={"errors": [
            {"path": ".".join(map(str, e["loc"])), "message": e["msg"]} for e in exc.errors()]}) from exc
    await abandon_stale(db, now)
    running = await db.scalar(select(func.count()).select_from(AiRun).where(AiRun.status == RUNNING))
    if (running or 0) >= MAX_RUNNING:
        raise AiBusy(f"{running} exécutions en cours (au plus {MAX_RUNNING})")
    settings = await bud.load_settings(db)
    model = bud.model_for(settings, service_id)
    if bud.price(settings, model) is None:
        raise StateConflict(f"modèle {model} sans barème de prix : choisir un autre modèle dans les réglages")
    agent = service.agent
    run = AiRun(service_id=service.id, service_version=service.version, agent_id=agent.id,
                agent_version=agent.version, instructions_sha256=agent.sha256, model=model,
                user_id=user_id, idempotency_key=idempotency_key, input=parsed.model_dump(mode="json"),
                status=RUNNING, started_at=now, correlation_id=uuid.uuid4())
    db.add(run)
    await db.flush()
    await append_event(db, event_type="AI_RUN_STARTED", aggregate_type="ai_run", aggregate_id=run.id,
                       source="api", correlation_id=run.correlation_id,
                       payload={"service_id": service.id, "agent_id": agent.id, "model": model})
    return run, True


def launch(sessions: async_sessionmaker[AsyncSession], run_id: uuid.UUID,
           proxy: ProxyClient | None = None) -> asyncio.Task:
    task = asyncio.create_task(execute(sessions, run_id, proxy or ProxyClient()))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


# ---------------------------------------------------------------- loop

@dataclass
class _State:
    seq: int = 0
    sent_upto: int = 0
    corrections: int = 0


def _assistant_message(msg: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"role": "assistant", "content": msg.get("content") or ""}
    if msg.get("tool_calls"):
        out["tool_calls"] = [{"id": tc.get("id"), "type": "function",
                              "function": {"name": (tc.get("function") or {}).get("name"),
                                           "arguments": (tc.get("function") or {}).get("arguments") or "{}"}}
                             for tc in msg["tool_calls"]]
    return out


_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def extract_json(text: str) -> Any:
    m = _JSON_BLOCK.search(text or "")
    raw = m.group(1) if m else (text or "").strip()
    return json.loads(raw)


async def execute(sessions: async_sessionmaker[AsyncSession], run_id: uuid.UUID, proxy: ProxyClient,
                  clock: Callable[[], datetime] = utcnow) -> str:
    try:
        return await _execute(sessions, run_id, proxy, clock)
    except Exception as exc:  # noqa: BLE001 — a run never stays RUNNING on a bug
        log.exception("AI run %s failed", run_id)
        async with sessions() as db:
            run = await db.get(AiRun, run_id)
            if run is not None and run.status == RUNNING:
                await _finish(db, run, FAILED, clock(), "INTERNAL_ERROR", f"{type(exc).__name__}: {exc}"[:2000])
                await db.commit()
        return FAILED


async def _finish(db: AsyncSession, run: AiRun, status: str, now: datetime, code: str | None = None,
                  detail: str | None = None, text: str | None = None, data: Any = None) -> None:
    run.status, run.finished_at, run.error_code, run.error_detail = status, now, code, detail
    if text is not None:
        run.output_text = text
    if data is not None:
        run.output_json = plain(data)
    await append_event(db, event_type="AI_RUN_FINISHED", aggregate_type="ai_run", aggregate_id=run.id,
                       source="api", correlation_id=run.correlation_id,
                       payload={"status": status, "error_code": code,
                                "cost_usd": run.cost_actual_usd + run.cost_estimated_usd,
                                "model_calls": run.model_calls, "tool_calls": run.tool_calls})


async def _step(db: AsyncSession, run: AiRun, st: _State, kind: str, detail: dict[str, Any], *,
                tokens_in: int = 0, tokens_out: int = 0, cost_actual: Decimal | None = None,
                cost_estimated: Decimal | None = None, duration_ms: int = 0, error: str | None = None,
                now: datetime) -> AiStep:
    st.seq += 1
    step = AiStep(run_id=run.id, seq=st.seq, kind=kind, at=now, detail=plain(detail),
                  tokens_in=tokens_in, tokens_out=tokens_out, cost_actual_usd=cost_actual,
                  cost_estimated_usd=cost_estimated, duration_ms=duration_ms, error_code=error)
    db.add(step)
    await db.flush()
    return step


async def _execute(sessions: async_sessionmaker[AsyncSession], run_id: uuid.UUID, proxy: ProxyClient,
                   clock: Callable[[], datetime]) -> str:
    async with sessions() as db:
        run = await db.get(AiRun, run_id)
        if run is None or run.status != RUNNING:
            return "SKIPPED"
        service: Service = SERVICES[run.service_id]
        agent: Agent = service.agent
        settings = await bud.load_settings(db)
        prices = bud.price(settings, run.model)
        parsed = service.input_model.model_validate(run.input)
        messages: list[dict[str, Any]] = [{"role": "system", "content": agent.instructions},
                                          {"role": "user", "content": service.prompt(parsed, clock())}]
        tool_specs = [TOOLS[n].spec() for n in agent.tools]
        lim = agent.limits
        st = _State()
        deadline = time.monotonic() + lim.run_timeout_s

        async def stop(status: str, code: str, detail: str | None = None, text: str | None = None) -> str:
            await _finish(db, run, status, clock(), code, detail, text)
            await db.commit()
            return status

        last_text: str | None = None
        while True:
            # limits (UI-16 « Limites par exécution »)
            remaining = deadline - time.monotonic()
            if run.model_calls >= lim.model_calls:
                return await stop(LIMIT_REACHED, "LIMIT_MODEL_CALLS", text=last_text)
            if run.tokens_in + run.tokens_out >= lim.tokens:
                return await stop(LIMIT_REACHED, "LIMIT_TOKENS", text=last_text)
            if remaining <= 0:
                return await stop(LIMIT_REACHED, "LIMIT_DURATION", text=last_text)
            now = clock()
            est_max = bud.estimate_max(prices, messages, tool_specs, lim.max_tokens) if prices else ZERO
            if run.cost_actual_usd + run.cost_estimated_usd + est_max > lim.cost_usd:
                return await stop(LIMIT_REACHED, "LIMIT_COST", text=last_text)
            spent, _, _ = await bud.spent_today(db, now)
            cap = D(settings.daily_budget_usd)
            if spent + est_max > cap:
                await _step(db, run, st, "MODEL_CALL", {"blocked": "daily budget", "spent_usd": spent,
                                                        "budget_usd": cap, "max_estimated_usd": est_max},
                            error="BUDGET_BLOCKED", now=now)
                await bud.check_alerts(db, settings, max(spent, cap), now, force_full=True)
                return await stop(BUDGET_BLOCKED, "BUDGET_BLOCKED",
                                  f"dépense du jour {spent} + estimation {est_max} > plafond {cap}", last_text)

            delta = messages[st.sent_upto:]
            result: ChatResult | None = None
            for attempt in (1, 2):
                result = await proxy.chat(model=run.model, messages=messages, tools=tool_specs,
                                          temperature=lim.temperature, max_tokens=lim.max_tokens,
                                          timeout=max(1.0, min(lim.call_timeout_s, remaining)))
                est = bud.estimate(prices, result.tokens_in, result.tokens_out)
                run.model_calls += 1
                run.tokens_in += result.tokens_in
                run.tokens_out += result.tokens_out
                if result.cost_usd is not None:
                    run.cost_actual_usd += result.cost_usd
                else:
                    run.cost_estimated_usd += est
                    if result.tokens_in or result.tokens_out:
                        run.cost_estimated_only = True
                await _step(db, run, st, "MODEL_CALL", {
                    "messages": delta if attempt == 1 else [], "messages_total": len(messages),
                    "params": {"model": run.model, "temperature": lim.temperature, "max_tokens": lim.max_tokens,
                               "tools": [t["function"]["name"] for t in tool_specs]},
                    "max_estimated_usd": est_max, "response": result.message,
                    "finish_reason": result.finish_reason, "http_status": result.http_status,
                    "attempt": attempt, "error_detail": result.error_detail},
                    tokens_in=result.tokens_in, tokens_out=result.tokens_out, cost_actual=result.cost_usd,
                    cost_estimated=None if result.cost_usd is not None else est,
                    duration_ms=result.latency_ms, error=result.error_code, now=clock())
                await db.commit()
                if result.error_code == "TOOL_USE_FAILED" and attempt == 1:
                    continue
                break
            assert result is not None
            st.sent_upto = len(messages)
            spent, _, _ = await bud.spent_today(db, clock())
            await bud.check_alerts(db, settings, spent, clock())
            await db.commit()
            if not result.ok:
                if result.error_code == "BUDGET_EXCEEDED":
                    await bud.check_alerts(db, settings, spent, clock(), source="proxy", force_full=True)
                    return await stop(BUDGET_BLOCKED, "PROXY_BUDGET_EXCEEDED", result.error_detail, last_text)
                return await stop(FAILED, result.error_code or "PROXY_ERROR", result.error_detail, last_text)

            msg = _assistant_message(result.message)
            messages.append(msg)
            calls = msg.get("tool_calls") or []
            if calls:
                for tc in calls:
                    name = tc["function"]["name"]
                    args = tc["function"]["arguments"]
                    t0 = time.monotonic()
                    if run.tool_calls >= lim.tool_calls:
                        return await stop(LIMIT_REACHED, "LIMIT_TOOL_CALLS", text=last_text)
                    run.tool_calls += 1
                    if name not in agent.tools or name not in TOOLS:
                        out_result: Any = {"error": "TOOL_NOT_ALLOWED",
                                           "detail": f"outil {name} non autorisé pour l'agent {agent.id}"}
                        error, truncated, sha = "TOOL_NOT_ALLOWED", False, None
                    else:
                        async with sessions() as rdb:
                            oc = await call_tool(rdb, name, args)
                            await rdb.rollback()
                        out_result, error, truncated, sha = oc.result, oc.error, oc.truncated, oc.sha256
                    await _step(db, run, st, "TOOL_CALL", {
                        "tool": name, "tool_call_id": tc.get("id"), "arguments": args, "result": out_result,
                        "truncated": truncated, "result_sha256": sha},
                        duration_ms=int((time.monotonic() - t0) * 1000), error=error, now=clock())
                    await db.commit()
                    messages.append({"role": "tool", "tool_call_id": tc.get("id"),
                                     "content": json.dumps(out_result, ensure_ascii=False, default=str)})
                continue

            last_text = msg.get("content") or ""
            if agent.output != "strategy_json":
                return await stop(SUCCEEDED, None, text=last_text) if last_text.strip() else \
                    await stop(FAILED, "EMPTY_ANSWER")
            problem = None
            data: Any = None
            try:
                data = extract_json(last_text)
                if not isinstance(data, dict) or not isinstance(data.get("config"), dict):
                    problem = "la réponse doit contenir un objet JSON avec la clé « config »"
                else:
                    async with sessions() as rdb:
                        check = await validate_strategy_config_tool(rdb, ConfigArgs(config=data["config"]))
                        await rdb.rollback()
                    if not check["ok"]:
                        problem = f"configuration invalide : {json.dumps(check['errors'], ensure_ascii=False)}"
            except (ValueError, TypeError) as exc:
                problem = f"JSON illisible : {exc}"
            if problem is None:
                await _finish(db, run, SUCCEEDED, clock(), text=last_text, data=data)
                await db.commit()
                return SUCCEEDED
            if st.corrections >= 1:
                return await stop(FAILED, "OUTPUT_INVALID", problem, last_text)
            st.corrections += 1
            messages.append({"role": "user", "content": f"Ta réponse n'est pas acceptable : {problem}. "
                             "Corrige la configuration (valide-la avec validate_strategy_config) et renvoie "
                             "le bloc ```json complet."})
