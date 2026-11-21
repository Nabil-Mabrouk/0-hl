"""AI services: proxy client, agent loop, journal, budget, alerts, API (03-ecrans/UI-16, T-AI-01..T-AI-11)."""

import json
import uuid
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.core.auth.security import create_access_token
from app.core.database import get_db
from app.core.models import UserRole
from app.domain.ai import api as ai_api
from app.domain.ai import budget as bud
from app.domain.ai import runner
from app.domain.ai.models import AiAlert, AiRun, AiSettings, AiStep
from app.domain.ai.proxy import ChatResult, ProxyClient
from app.domain.ai.tools import TOOLS, call_tool
from app.domain.routers import trading_v1_router
from app.domain.trading.enums import TradingRole
from app.domain.trading.models import AuditEvent, DomainEvent
from app.domain.trading.rbac import TradingPrincipal, active_roles, grant_role
from app.domain.trading.seed import run_seed
from app.domain.trading.types import utcnow

from .factories import make_user

pytestmark = pytest.mark.anyio
D = Decimal


def _h(user_id: int, **extra) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id)}", **extra}


def tool_call(name: str, args: dict, cid: str = "c1") -> ChatResult:
    return ChatResult(True, 200, 12, message={"role": "assistant", "content": None, "tool_calls": [
        {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]},
        finish_reason="tool_calls", tokens_in=100, tokens_out=20, cost_usd=D("0.0001"))


def answer(text: str, cost: Decimal | None = D("0.0002")) -> ChatResult:
    return ChatResult(True, 200, 15, message={"role": "assistant", "content": text}, finish_reason="stop",
                      tokens_in=300, tokens_out=80, cost_usd=cost)


def error(code: str, status: int = 400) -> ChatResult:
    return ChatResult(False, status, 5, error_code=code, error_detail=code.lower())


class FakeProxy(ProxyClient):
    def __init__(self, script: list[ChatResult]):
        super().__init__("http://proxy", "t")
        self.script = list(script)
        self.calls: list[dict] = []

    async def chat(self, **kw):
        self.calls.append(json.loads(json.dumps(kw)))
        return self.script.pop(0)

    async def models(self):
        return ["groq-gpt-oss-120b", "groq-gpt-oss-20b", "other-model"]

    async def key_info(self):
        return {"spend": "0.0003", "max_budget": "12.0", "budget_duration": "1d"}


async def _setup(session_factory):
    async with session_factory() as db:
        seed = await run_seed(db)
        user = await make_user(db, f"u{uuid.uuid4().hex[:6]}@x.io")
        await db.commit()
    return seed, user


async def _run(session_factory, user, service_id, inp, script, **kw):
    async with session_factory() as db:
        run, _ = await runner.create_run(db, user_id=user.id, service_id=service_id, raw_input=inp,
                                         idempotency_key=None, now=utcnow())
        await db.commit()
    proxy = FakeProxy(script)
    status = await runner.execute(session_factory, run.id, proxy)
    async with session_factory() as db:
        run = await db.get(AiRun, run.id)
        steps = (await db.scalars(select(AiStep).where(AiStep.run_id == run.id).order_by(AiStep.seq))).all()
    return status, run, list(steps), proxy


# T-AI-01 -----------------------------------------------------------------------------------
async def test_loop_runs_tools_and_journals_every_step(session_factory):
    seed, user = await _setup(session_factory)
    status, run, steps, proxy = await _run(session_factory, user, "bot-review", {"bot_id": str(seed.bot.id)}, [
        tool_call("get_stats", {"bot_id": str(seed.bot.id)}), answer("Bilan : aucun trade [get_stats].")])
    assert status == run.status == "SUCCEEDED"
    assert [s.kind for s in steps] == ["MODEL_CALL", "TOOL_CALL", "MODEL_CALL"]
    assert steps[0].detail["messages"][0]["role"] == "system" and steps[0].tokens_in == 100
    assert steps[1].detail["tool"] == "get_stats" and steps[1].detail["result"]["overall"]["trades"] == 0
    assert steps[2].detail["messages"][-1]["role"] == "tool"          # only messages added since the last call
    assert run.output_text.startswith("Bilan") and run.cost_actual_usd == D("0.0003")
    assert (run.model_calls, run.tool_calls, run.tokens_in, run.tokens_out) == (2, 1, 400, 100)
    assert proxy.calls[0]["tools"] and proxy.calls[0]["model"] == "groq-gpt-oss-120b"
    async with session_factory() as db:
        types = (await db.scalars(select(DomainEvent.event_type).where(
            DomainEvent.aggregate_id == str(run.id)))).all()
    assert types == ["AI_RUN_STARTED", "AI_RUN_FINISHED"]


# T-AI-02 -----------------------------------------------------------------------------------
async def test_disallowed_tool_and_invalid_arguments_go_back_to_the_model(session_factory, db):
    seed, user = await _setup(session_factory)
    status, run, steps, _ = await _run(session_factory, user, "explain-trade", {"position_id": "x"}, [
        tool_call("list_bots", {}), tool_call("get_trade", {"position_id": "pas-un-uuid"}, "c2"),
        answer("Trade inconnu.")])
    assert status == "SUCCEEDED"
    assert steps[1].error_code == "TOOL_NOT_ALLOWED"                  # list_bots is not a trade-analyst tool
    assert steps[3].error_code == "INVALID_ARGUMENTS" and steps[3].detail["result"]["error"] == "INVALID_ARGUMENTS"


async def test_large_tool_result_is_truncated_with_its_hash(db, monkeypatch):
    from app.domain.ai.tools import NoArgs, Tool

    async def huge(db, a):
        return {"blob": "y" * 40000}
    monkeypatch.setitem(TOOLS, "huge", Tool("huge", "test", NoArgs, huge))
    big = await call_tool(db, "huge", {})
    assert big.truncated and big.result["truncated"] and big.size > 32 * 1024 and len(big.sha256) == 64
    assert len(json.dumps(big.result)) < 33 * 1024


# T-AI-03 -----------------------------------------------------------------------------------
async def test_proxy_errors(session_factory):
    seed, user = await _setup(session_factory)
    inp = {"bot_id": str(seed.bot.id)}
    status, run, steps, _ = await _run(session_factory, user, "bot-review", inp,
                                       [error("TOOL_USE_FAILED"), answer("ok")])
    assert status == "SUCCEEDED" and [s.detail["attempt"] for s in steps] == [1, 2]
    status, run, _, _ = await _run(session_factory, user, "bot-review", inp,
                                   [error("TOOL_USE_FAILED"), error("TOOL_USE_FAILED")])
    assert (status, run.error_code) == ("FAILED", "TOOL_USE_FAILED")
    status, run, steps, _ = await _run(session_factory, user, "bot-review", inp, [error("BUDGET_EXCEEDED")])
    assert (status, run.error_code, len(steps)) == ("BUDGET_BLOCKED", "PROXY_BUDGET_EXCEEDED", 1)
    status, run, _, _ = await _run(session_factory, user, "bot-review", inp, [error("MODEL_NOT_ALLOWED", 403)])
    assert (status, run.error_code) == ("FAILED", "MODEL_NOT_ALLOWED")


async def test_proxy_client_reads_cost_and_classifies_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.headers["Authorization"] == "Bearer tok" and body["tool_choice"] == "auto"
        if body["model"] == "broke":
            return httpx.Response(400, json={"error": {"type": "budget_exceeded", "message": "Budget has been exceeded"}})
        if body["model"] == "denied":
            return httpx.Response(403, json={"error": {"type": "key_model_access_denied"}})
        return httpx.Response(200, headers={"x-litellm-response-cost": "0.00004"}, json={
            "choices": [{"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2}})
    c = ProxyClient("http://proxy", "tok", transport=httpx.MockTransport(handler))
    kw = dict(messages=[{"role": "user", "content": "x"}], tools=[{"type": "function"}], temperature=0.2, max_tokens=10)
    r = await c.chat(model="groq-gpt-oss-120b", **kw)
    assert (r.ok, r.cost_usd, r.tokens_in, r.message["content"]) == (True, D("0.00004"), 10, "hi")
    assert (await c.chat(model="broke", **kw)).error_code == "BUDGET_EXCEEDED"
    assert (await c.chat(model="denied", **kw)).error_code == "MODEL_NOT_ALLOWED"
    assert not ProxyClient("", "").configured


# T-AI-04 -----------------------------------------------------------------------------------
async def test_limits_stop_the_run(session_factory):
    seed, user = await _setup(session_factory)
    loop = [tool_call("get_stats", {}, f"c{i}") for i in range(10)]
    status, run, _, _ = await _run(session_factory, user, "bot-review", {"bot_id": str(seed.bot.id)}, loop)
    assert (status, run.error_code, run.model_calls) == ("LIMIT_REACHED", "LIMIT_MODEL_CALLS", 8)
    many = ChatResult(True, 200, 5, message={"role": "assistant", "content": None, "tool_calls": [
        {"id": f"t{i}", "type": "function", "function": {"name": "get_stats", "arguments": "{}"}} for i in range(17)]},
        finish_reason="tool_calls", tokens_in=10, tokens_out=10, cost_usd=D("0.0001"))
    status, run, _, _ = await _run(session_factory, user, "bot-review", {"bot_id": str(seed.bot.id)}, [many])
    assert (status, run.error_code, run.tool_calls) == ("LIMIT_REACHED", "LIMIT_TOOL_CALLS", 16)
    huge = ChatResult(True, 200, 5, message={"role": "assistant", "content": None, "tool_calls": [
        {"id": "h", "type": "function", "function": {"name": "get_stats", "arguments": "{}"}}]},
        finish_reason="tool_calls", tokens_in=59_000, tokens_out=2000, cost_usd=D("0.01"))
    status, run, _, _ = await _run(session_factory, user, "bot-review", {"bot_id": str(seed.bot.id)}, [huge])
    assert (status, run.error_code) == ("LIMIT_REACHED", "LIMIT_TOKENS")


# T-AI-05 -----------------------------------------------------------------------------------
async def test_daily_budget_blocks_before_the_call(session_factory):
    seed, user = await _setup(session_factory)
    async with session_factory() as db:
        s = bud.default_settings()
        s.daily_budget_usd, s.version = D("0.002"), 1
        db.add(s)
        await db.commit()
    inp = {"bot_id": str(seed.bot.id)}
    status, run, _, _ = await _run(session_factory, user, "bot-review", inp, [answer("a", D("0.0019"))])
    assert status == "SUCCEEDED"
    status, run, steps, proxy = await _run(session_factory, user, "bot-review", inp, [answer("b")])
    assert (status, run.error_code, proxy.calls) == ("BUDGET_BLOCKED", "BUDGET_BLOCKED", [])
    assert steps[0].error_code == "BUDGET_BLOCKED"
    async with session_factory() as db:
        total, actual, est = await bud.spent_today(db, utcnow())
        assert (total, actual) == (D("0.0019"), D("0.0019"))
        assert await bud.spent_today(db, utcnow() + timedelta(days=1)) == (0, 0, 0)   # reset at 00:00 UTC
    # no cost header ⇒ cost estimated from the tokens with the price table
    seed2, user2 = await _setup(session_factory)
    async with session_factory() as db:
        s = await db.get(AiSettings, "default")
        s.daily_budget_usd = D(10)
        await db.commit()
    status, run, steps, _ = await _run(session_factory, user2, "bot-review", inp, [answer("c", None)])
    assert run.cost_estimated_only and steps[0].cost_actual_usd is None
    assert steps[0].cost_estimated_usd == (D(300) * D("0.15") + D(80) * D("0.60")) / D(1_000_000)


# T-AI-06 -----------------------------------------------------------------------------------
async def test_alerts_once_per_threshold_and_day(session_factory, monkeypatch):
    monkeypatch.setenv("ALERT_EMAIL_TO", "ops@example.invalid")
    sent = []
    async with session_factory() as db:
        s = bud.default_settings()
        now = utcnow()
        assert await bud.check_alerts(db, s, D(5), now, mailer=lambda *a: sent.append(a)) == []
        assert await bud.check_alerts(db, s, D(8), now, mailer=lambda *a: sent.append(a)) == [80]
        assert await bud.check_alerts(db, s, D("8.5"), now, mailer=lambda *a: sent.append(a)) == []
        assert await bud.check_alerts(db, s, D(10), now, mailer=lambda *a: sent.append(a)) == [100]
        await db.commit()
        assert [a[0] for a in sent] == ["ops@example.invalid"] * 2 and "épuisé" in sent[1][1]

        def boom(*a):
            raise OSError("smtp down")
        later = now + timedelta(days=1)
        assert await bud.check_alerts(db, s, D(10), later, mailer=boom) == [80, 100]
        await db.commit()
        rows = (await db.scalars(select(AiAlert).where(AiAlert.day == later.date()))).all()
        assert all(not r.email_sent and "smtp down" in r.email_error for r in rows)
    monkeypatch.delenv("ALERT_EMAIL_TO")
    async with session_factory() as db:
        day3 = utcnow() + timedelta(days=2)
        await bud.check_alerts(db, bud.default_settings(), D(9), day3, mailer=lambda *a: sent.append(a))
        r = await db.scalar(select(AiAlert).where(AiAlert.day == day3.date()))
        assert r.email_error == "ALERT_EMAIL_TO absent" and len(sent) == 2


# T-AI-07 -----------------------------------------------------------------------------------
async def test_tools_are_deterministic_read_only_and_scrubbed(session_factory):
    seed, _ = await _setup(session_factory)
    async with session_factory() as db:
        a = await call_tool(db, "list_bots", {})
        b = await call_tool(db, "list_bots", {})
        assert a.result == b.result and a.result["bots"][0]["name"] == seed.bot.name
        v = await call_tool(db, "get_strategy_version", {"strategy_id": seed.strategy_version.strategy_id,
                                                         "version": seed.strategy_version.version})
        assert "created_by" not in json.dumps(v.result) and v.result["config"]["entry"]
        cat = await call_tool(db, "get_feature_catalogue", {})
        assert any(f["name"] == "breadth_24h" for f in cat.result["features"])
        ok = await call_tool(db, "validate_strategy_config", {"config": seed.strategy_version.config | {"status": "DRAFT"}})
        assert ok.result["ok"] is True
        mins = await call_tool(db, "get_market_minutes", {"instrument_id": "kraken_spot:BTC/USD",
                                                          "until": utcnow().isoformat(), "minutes": 10})
        assert mins.result["minutes"] == []
        assert not db.new and not db.dirty
    assert set(TOOLS) == {"list_bots", "get_trade", "list_trades", "get_trade_trace", "get_stats",
                          "get_strategy_version", "list_strategy_versions", "get_feature_catalogue",
                          "get_market_minutes", "get_features", "get_feed_status", "validate_strategy_config"}


# T-AI-08 -----------------------------------------------------------------------------------
async def test_strategy_draft_is_validated_with_one_correction(session_factory):
    seed, user = await _setup(session_factory)
    good = seed.strategy_version.config | {"status": "DRAFT", "strategy_id": "idea-x", "version": "0.1.0"}
    bad = good | {"risk_policy_id": "nope"}
    block = lambda cfg: "```json\n" + json.dumps({"config": cfg, "hypothesis": "h", "explanations": []}) + "\n```"
    status, run, _, proxy = await _run(session_factory, user, "strategy-draft", {"idea": "acheter les replis propres"},
                                       [answer(block(bad)), answer(block(good))])
    assert status == "SUCCEEDED" and run.output_json["config"]["strategy_id"] == "idea-x"
    assert "pas acceptable" in proxy.calls[1]["messages"][-1]["content"]
    status, run, _, _ = await _run(session_factory, user, "strategy-draft", {"idea": "acheter les replis propres"},
                                   [answer(block(bad)), answer("pas de json")])
    assert (status, run.error_code) == ("FAILED", "OUTPUT_INVALID")


# API: T-AI-09, T-AI-10, T-AI-11 -------------------------------------------------------------
class World:
    async def build(self, session_factory, monkeypatch, script):
        async with session_factory() as db:
            self.seed = await run_seed(db)
            self.admin = await make_user(db, "admin@x.io", UserRole.admin)
            self.observer = await make_user(db, "obs@x.io")
            self.analyst = await make_user(db, "ana@x.io")
            await db.commit()
        app = FastAPI()
        app.include_router(trading_v1_router)
        app.include_router(ai_api.router)

        async def _db():
            async with session_factory() as s:
                yield s
        app.dependency_overrides[get_db] = _db
        self.proxy = FakeProxy(script)
        self.tasks = []
        monkeypatch.setattr(ai_api, "SESSIONS", lambda: session_factory)
        monkeypatch.setattr(ai_api, "PROXY", lambda: self.proxy)
        real_launch = runner.launch
        monkeypatch.setattr(runner, "launch", lambda sf, rid, px=None: self.tasks.append(real_launch(sf, rid, px)))
        self.c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
        assert (await self.c.get("/api/v1/me", headers=_h(self.admin.id))).status_code == 200
        async with session_factory() as db:
            g = TradingPrincipal(self.admin.id, await active_roles(db, self.admin.id))
            await grant_role(db, granter=g, user_id=self.observer.id, role=TradingRole.OBSERVER)
            await grant_role(db, granter=g, user_id=self.analyst.id, role=TradingRole.ANALYST)
            await db.commit()
        return self


async def test_api_rbac_settings_and_audit(session_factory, monkeypatch):
    w = await World().build(session_factory, monkeypatch, [])
    body = {"service_id": "bot-review", "input": {"bot_id": str(w.seed.bot.id)}}
    assert (await w.c.post("/api/v1/ai/runs", json=body, headers=_h(w.observer.id))).status_code == 403
    assert (await w.c.get("/api/v1/ai/settings", headers=_h(w.analyst.id))).status_code == 403
    r = await w.c.get("/api/v1/ai/settings", headers=_h(w.admin.id))
    assert r.json()["daily_budget_usd"] == "10" and r.json()["version"] == 0
    bad = await w.c.patch("/api/v1/ai/settings", json={"daily_budget_usd": "13"}, headers=_h(w.admin.id))
    assert bad.status_code == 422
    r = await w.c.patch("/api/v1/ai/settings", json={"daily_budget_usd": "5",
                                                     "models": {"bot-review": "groq-gpt-oss-20b"}},
                        headers=_h(w.admin.id, **{"If-Match": '"0"'}))
    assert r.status_code == 200 and r.json()["version"] == 1
    stale = await w.c.patch("/api/v1/ai/settings", json={"daily_budget_usd": "6"},
                            headers=_h(w.admin.id, **{"If-Match": '"0"'}))
    assert stale.status_code == 409
    nomodel = await w.c.patch("/api/v1/ai/settings", json={"models": {"bot-review": "other-model"}},
                              headers=_h(w.admin.id))
    assert nomodel.status_code == 422
    async with session_factory() as db:
        audit = (await db.scalars(select(AuditEvent).where(AuditEvent.action == "AI_SETTINGS_UPDATED"))).all()
        assert len(audit) == 1 and "daily_budget_usd" in audit[0].diff
    models = (await w.c.get("/api/v1/ai/models", headers=_h(w.observer.id))).json()["items"]
    assert {m["id"]: m["selectable"] for m in models} == {
        "groq-gpt-oss-120b": True, "groq-gpt-oss-20b": True, "other-model": False}
    services = (await w.c.get("/api/v1/ai/services", headers=_h(w.observer.id))).json()["items"]
    assert {s["id"]: s["model"] for s in services}["bot-review"] == "groq-gpt-oss-20b"


async def test_api_run_idempotency_concurrency_and_abandon(session_factory, monkeypatch):
    w = await World().build(session_factory, monkeypatch, [answer("bilan")])
    body = {"service_id": "bot-review", "input": {"bot_id": str(w.seed.bot.id)}}
    h = _h(w.analyst.id, **{"Idempotency-Key": "k1"})
    r1 = await w.c.post("/api/v1/ai/runs", json=body, headers=h)
    assert r1.status_code == 202
    for t in w.tasks:
        await t
    r2 = await w.c.post("/api/v1/ai/runs", json=body, headers=h)
    assert r2.json()["id"] == r1.json()["id"] and len(w.tasks) == 1
    d = (await w.c.get(f"/api/v1/ai/runs/{r1.json()['id']}", headers=_h(w.observer.id))).json()
    assert d["status"] == "SUCCEEDED" and [s["kind"] for s in d["steps"]] == ["MODEL_CALL"]
    assert d["output_text"] == "bilan" and d["cost_usd"] == "0.0002"
    bad = await w.c.post("/api/v1/ai/runs", json={"service_id": "bot-review", "input": {}}, headers=_h(w.analyst.id))
    assert bad.status_code == 422
    # two RUNNING runs ⇒ 409 AI_BUSY; a run older than the timeout ⇒ ABANDONED
    async with session_factory() as db:
        for age in (0, 0):
            db.add(AiRun(service_id="bot-review", service_version="1", agent_id="a", agent_version="1",
                         instructions_sha256="x" * 64, model="m", user_id=w.analyst.id, input={},
                         status="RUNNING", started_at=utcnow() - timedelta(minutes=age), correlation_id=uuid.uuid4()))
        await db.commit()
    busy = await w.c.post("/api/v1/ai/runs", json=body, headers=_h(w.analyst.id))
    assert busy.status_code == 409 and busy.json()["code"] == "AI_BUSY"
    async with session_factory() as db:
        for r in (await db.scalars(select(AiRun).where(AiRun.status == "RUNNING"))).all():
            r.started_at = utcnow() - timedelta(minutes=10)
        await db.commit()
    runs = (await w.c.get("/api/v1/ai/runs?status=ABANDONED", headers=_h(w.observer.id))).json()
    assert runs["total"] == 2
    budget = (await w.c.get("/api/v1/ai/budget", headers=_h(w.observer.id))).json()
    assert budget["spent_usd"] == "0.0002" and budget["daily_budget_usd"] == "10" and not budget["exhausted"]
    assert budget["proxy"]["max_budget"] == "12.0" and budget["proxy_gap"] is False
    # the journal has no write route
    paths = {(r.path, m) for r in ai_api.router.routes for m in getattr(r, "methods", ())}
    assert not any("/runs" in p and m in ("PUT", "PATCH", "DELETE") for p, m in paths)
