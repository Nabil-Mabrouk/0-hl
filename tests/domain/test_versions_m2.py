"""M2.3 — versions without code (05-strategies/04, T-M2.3-01..07)."""

from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.core.auth.security import create_access_token
from app.core.database import get_db
from app.core.models import UserRole
from app.domain.routers import trading_v1_router, versions_v1_router
from app.domain.trading.enums import BotStatus, TradingRole
from app.domain.trading.lifecycle import classify_policy_change
from app.domain.trading.models import AuditEvent, Bot, BotSession, RiskPolicyVersion, StrategyVersion
from app.domain.trading.rbac import TradingPrincipal, active_roles, grant_role
from app.domain.trading.seed import run_seed

from .agent_fakes import NOW, Clock, engine, market_state
from .factories import make_user

pytestmark = pytest.mark.anyio


def _h(user_id: int, **extra) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id)}", **extra}


class World:
    async def build(self, session_factory):
        self.sf = session_factory
        async with session_factory() as db:
            seed = await run_seed(db)
            self.admin = await make_user(db, "admin@x.io", UserRole.admin)
            self.observer = await make_user(db, "obs@x.io")
            self.analyst = await make_user(db, "ana@x.io")
            await db.commit()
        self.bot_id = str(seed.bot.id)
        self.sv0 = str(seed.strategy_version.id)
        self.pv0 = str(seed.policy_version.id)
        app = FastAPI()
        app.include_router(trading_v1_router)
        app.include_router(versions_v1_router)

        async def _db():
            async with session_factory() as s:
                yield s

        app.dependency_overrides[get_db] = _db
        self.c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
        assert (await self.c.get("/api/v1/me", headers=_h(self.admin.id))).status_code == 200
        async with session_factory() as db:
            granter = TradingPrincipal(self.admin.id, await active_roles(db, self.admin.id))
            await grant_role(db, granter=granter, user_id=self.observer.id, role=TradingRole.OBSERVER)
            await grant_role(db, granter=granter, user_id=self.analyst.id, role=TradingRole.ANALYST)
            await db.commit()
        self.clock = Clock(NOW)
        self.engine = engine(session_factory, market_state(), self.clock)
        return self

    def a(self, **extra):
        return _h(self.admin.id, **extra)

    async def duplicate(self, kind: str, vid: str, version: str) -> dict:
        r = await self.c.post(f"/api/v1/{kind}/versions", json={"from_version_id": vid, "version": version},
                              headers=self.a())
        assert r.status_code == 201, r.text
        return r.json()

    async def edit(self, kind: str, v: dict, **changes) -> dict:
        cfg = v["config"] | changes
        r = await self.c.put(f"/api/v1/{kind}/versions/{v['id']}", json={"config": cfg},
                             headers=self.a(**{"If-Match": f'"{v["config_sha256"]}"'}))
        assert r.status_code == 200, r.text
        return r.json()

    async def command(self, command: str, key: str):
        r = await self.c.post(f"/api/v1/bots/{self.bot_id}/commands", json={"command": command},
                              headers=self.a(**{"Idempotency-Key": key}))
        assert r.status_code == 202, r.text
        self.clock.advance(seconds=5)
        await self.engine.tick()


@pytest.fixture
async def w(session_factory):
    world = await World().build(session_factory)
    yield world
    await world.c.aclose()


# T-M2.3-01 ---------------------------------------------------------------------------------
async def test_published_version_is_immutable(w):
    v = await w.duplicate("strategies", w.sv0, "0.3.0")
    r = await w.c.post(f"/api/v1/strategies/versions/{v['id']}/publish", json={"reason": "essai"},
                       headers=w.a())
    assert r.status_code == 200 and r.json()["status"] == "PUBLISHED"
    sha = r.json()["config_sha256"]
    put = await w.c.put(f"/api/v1/strategies/versions/{v['id']}", json={"config": v["config"]}, headers=w.a())
    assert put.status_code == 409 and put.json()["code"] == "VERSION_IMMUTABLE"
    assert (await w.c.delete(f"/api/v1/strategies/versions/{v['id']}", headers=w.a())).json()["code"] == "VERSION_IMMUTABLE"
    again = await w.c.post(f"/api/v1/strategies/versions/{v['id']}/publish", json={"reason": "x"}, headers=w.a())
    assert again.status_code == 409
    detail = await w.c.get("/api/v1/strategies/volume-spike/versions/0.3.0", headers=w.a())
    assert detail.json()["config_sha256"] == sha and detail.headers["ETag"] == f'"{sha}"'


# T-M2.3-02 ---------------------------------------------------------------------------------
@pytest.mark.parametrize("mutate, path", [
    (lambda c: c["entry"]["conditions"][0].update(feature="volume_spik"), "entry.conditions[0].feature"),
    (lambda c: c["entry"]["conditions"][0].update(unit="bps"), "entry.conditions[0].unit"),
    (lambda c: c["exit"].update(initial_stop_pct="0"), "exit.initial_stop_pct"),
    (lambda c: c.update(risk_policy_id="nope"), "risk_policy_id"),
])
async def test_incoherent_strategy_is_refused_with_the_field_path(w, mutate, path):
    v = await w.duplicate("strategies", w.sv0, "0.3.0")
    cfg = v["config"]
    mutate(cfg)
    r = await w.c.put(f"/api/v1/strategies/versions/{v['id']}", json={"config": cfg}, headers=w.a())
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION_FAILED"
    assert path in [e["path"] for e in r.json()["extra"]["errors"]]
    check = await w.c.post("/api/v1/strategies/versions/validate", json={"config": cfg}, headers=w.a())
    assert check.json()["valid"] is False and path in [e["path"] for e in check.json()["errors"]]
    feats = await w.c.get("/api/v1/features", headers=_h(w.observer.id))
    assert {f["name"] for f in feats.json()["items"]} >= {"volume_spike", "window_trade_count"}


# T-M2.3-03 ---------------------------------------------------------------------------------
async def test_if_match_and_duplication(w):
    v = await w.duplicate("strategies", w.sv0, "0.3.0")
    async with w.sf() as db:
        src = await db.get(StrategyVersion, __import__("uuid").UUID(w.sv0))
    assert {k: x for k, x in v["config"].items() if k != "version"} == \
        {k: x for k, x in src.config.items() if k != "version"}
    assert v["status"] == "DRAFT" and v["config"]["version"] == "0.3.0"
    edited = await w.edit("strategies", v, cooldown_seconds=3600)
    stale = await w.c.put(f"/api/v1/strategies/versions/{v['id']}", json={"config": v["config"]},
                          headers=w.a(**{"If-Match": f'"{v["config_sha256"]}"'}))
    assert stale.status_code == 412 and stale.json()["code"] == "PRECONDITION_FAILED"
    assert edited["config"]["cooldown_seconds"] == 3600
    dup = await w.c.post("/api/v1/strategies/versions", json={"from_version_id": w.sv0, "version": "0.3.0"},
                         headers=w.a())
    assert dup.status_code == 409                                      # version already exists


# T-M2.3-04 ---------------------------------------------------------------------------------
async def test_loosened_policy_waits_for_the_review_delay(w):
    loose = await w.edit("risk-policies", await w.duplicate("risk-policies", w.pv0, "2"),
                         max_daily_loss_pct="5")
    r = await w.c.post(f"/api/v1/risk-policies/versions/{loose['id']}/publish", json={"reason": "plus de marge"},
                       headers=w.a())
    assert r.status_code == 202 and r.json()["outcome"] == "PENDING"
    assert [x["path"] for x in r.json()["loosened"]] == ["max_daily_loss_pct"]
    early = await w.c.post(f"/api/v1/risk-policies/versions/{loose['id']}/publish/confirm", headers=w.a())
    assert early.status_code == 409 and early.json()["code"] == "REVIEW_DELAY_PENDING"
    async with w.sf() as db:                                           # 11 minutes later
        pv = await db.get(RiskPolicyVersion, __import__("uuid").UUID(loose["id"]))
        pv.publish_requested_at -= timedelta(minutes=11)
        await db.commit()
    done = await w.c.post(f"/api/v1/risk-policies/versions/{loose['id']}/publish/confirm", headers=w.a())
    assert done.status_code == 200 and done.json()["status"] == "PUBLISHED"

    strict = await w.edit("risk-policies", await w.duplicate("risk-policies", w.pv0, "3"),
                          max_daily_loss_pct="2")
    now = await w.c.post(f"/api/v1/risk-policies/versions/{strict['id']}/publish", json={"reason": "prudence"},
                         headers=w.a())
    assert now.status_code == 200 and now.json()["outcome"] == "PUBLISHED"


def test_policy_change_classification():
    assert classify_policy_change("max_daily_loss_pct", "3", "5") == "plus souple"
    assert classify_policy_change("max_daily_loss_pct", "3", "2") == "plus strict"
    assert classify_policy_change("max_drawdown_pct", "15", None) == "plus souple"
    assert classify_policy_change("min_volume_24h_usd", "1000000", "500000") == "plus souple"
    assert classify_policy_change("cooldown_seconds", 1800, None) == "plus souple"
    assert classify_policy_change("kill_switch", False, True) == "plus strict"
    assert classify_policy_change("max_open_positions", 5, 5) == "neutre"


# T-M2.3-05 ---------------------------------------------------------------------------------
async def test_bind_only_at_rest_and_next_session_uses_new_versions(w, session_factory):
    v3 = await w.edit("strategies", await w.duplicate("strategies", w.sv0, "0.3.0"), cooldown_seconds=3600)
    url = f"/api/v1/bots/{w.bot_id}/bind"
    body = {"strategy_version_id": v3["id"], "risk_policy_version_id": w.pv0, "reason": "v0.3.0"}
    draft = await w.c.post(url, json=body, headers=w.a())
    assert draft.status_code == 409 and draft.json()["code"] == "STRATEGY_NOT_PUBLISHED"
    await w.c.post(f"/api/v1/strategies/versions/{v3['id']}/publish", json={"reason": "ok"}, headers=w.a())

    await w.engine.tick()                                              # autostart ⇒ RUNNING
    running = await w.c.post(url, json=body, headers=w.a())
    assert running.status_code == 409 and running.json()["code"] == "BOT_NOT_REBINDABLE"
    assert running.json()["extra"]["status"] == "RUNNING"
    await w.command("STOP", "k-stop-0001")
    async with session_factory() as db:
        assert (await db.get(Bot, __import__("uuid").UUID(w.bot_id))).status is BotStatus.STOPPED

    other = await w.c.post("/api/v1/risk-policies/versions", headers=w.a(), json={
        "config": {**(await w.c.get("/api/v1/risk-policies/paper-default/versions/1", headers=w.a())).json()["config"],
                   "policy_id": "autre", "version": "1"}})
    await w.c.post(f"/api/v1/risk-policies/versions/{other.json()['id']}/publish", json={"reason": "x"}, headers=w.a())
    wrong = await w.c.post(url, json=body | {"risk_policy_version_id": other.json()["id"]}, headers=w.a())
    assert wrong.status_code == 422

    bound = await w.c.post(url, json=body, headers=w.a())
    assert bound.status_code == 200 and bound.json()["status"] == "READY", bound.text
    await w.command("START", "k-start-0001")
    async with session_factory() as db:
        sessions = (await db.scalars(select(BotSession).order_by(BotSession.started_at))).all()
        audit = (await db.scalars(select(AuditEvent).where(AuditEvent.action == "BOT_REBOUND"))).all()
    assert len(sessions) == 2 and sessions[-1].strategy_sha256 == v3["config_sha256"]
    assert audit[0].reason == "v0.3.0" and audit[0].diff["from"]["status"] == "STOPPED"

    dep = await w.c.post(f"/api/v1/strategies/versions/{v3['id']}/deprecate", json={"reason": "fin"}, headers=w.a())
    assert dep.json()["status"] == "DEPRECATED"


# T-M2.3-06 ---------------------------------------------------------------------------------
async def test_seed_never_undoes_an_operator_binding(w, session_factory):
    v3 = await w.duplicate("strategies", w.sv0, "0.3.0")
    await w.c.post(f"/api/v1/strategies/versions/{v3['id']}/publish", json={"reason": "ok"}, headers=w.a())
    r = await w.c.post(f"/api/v1/bots/{w.bot_id}/bind", headers=w.a(), json={
        "strategy_version_id": v3["id"], "risk_policy_version_id": w.pv0, "reason": "choix opérateur"})
    assert r.status_code == 200                                        # bot READY, never started
    async with session_factory() as db:
        await run_seed(db)                                             # worker restart
    async with session_factory() as db:
        bot = await db.get(Bot, __import__("uuid").UUID(w.bot_id))
    assert str(bot.strategy_version_id) == v3["id"]


# T-M2.3-07 ---------------------------------------------------------------------------------
async def test_roles_and_audits(w, session_factory):
    obs, ana = _h(w.observer.id), _h(w.analyst.id)
    dup = {"from_version_id": w.sv0, "version": "0.3.0"}
    assert (await w.c.post("/api/v1/strategies/versions", json=dup, headers=obs)).status_code == 403
    created = await w.c.post("/api/v1/strategies/versions", json=dup, headers=ana)
    assert created.status_code == 201
    vid = created.json()["id"]
    assert (await w.c.post(f"/api/v1/strategies/versions/{vid}/publish", json={"reason": "x"},
                           headers=ana)).status_code == 403            # publishing needs RISK_OFFICER
    assert (await w.c.post("/api/v1/risk-policies/versions", json={"from_version_id": w.pv0, "version": "9"},
                           headers=ana)).status_code == 403
    assert (await w.c.post(f"/api/v1/bots/{w.bot_id}/bind", headers=ana, json={
        "strategy_version_id": w.sv0, "risk_policy_version_id": w.pv0, "reason": "x"})).status_code == 403
    diff = await w.c.get(f"/api/v1/strategies/diff?a={w.sv0}&b={vid}", headers=obs)
    assert diff.status_code == 200 and diff.json()["changes"] == [
        {"path": "version", "before": "0.2.0", "after": "0.3.0"}]
    assert (await w.c.delete(f"/api/v1/strategies/versions/{vid}", headers=ana)).status_code == 200
    async with session_factory() as db:
        actions = {a.action for a in (await db.scalars(select(AuditEvent))).all()}
    assert {"STRATEGY_VERSION_DUPLICATED", "STRATEGY_DRAFT_DELETED", "BOT_BIND_DENIED",
            "VERSION_GOVERNANCE_DENIED"} <= actions


# T-M2.3-08 ---------------------------------------------------------------------------------
async def test_admin_command_matches_the_api(w, session_factory, tmp_path):
    from app.domain.trading.admin import execute

    async def adm(*argv, who="admin@x.io"):
        return await execute(session_factory, ["--as", who, *argv])

    code, out = await adm("strategy", "duplicate", "volume-spike@0.2.0", "--version", "0.3.0")
    assert code == 0 and out["status"] == "DRAFT"
    cfg = out["config"] | {"cooldown_seconds": 3600}
    f = tmp_path / "v3.json"
    f.write_text(__import__("json").dumps(cfg), encoding="utf-8")
    assert (await adm("--dry-run", "strategy", "edit", "volume-spike@0.3.0", "--file", str(f)))[0] == 0
    async with session_factory() as db:                                # dry-run wrote nothing
        v = await db.scalar(select(StrategyVersion).where(StrategyVersion.version == "0.3.0"))
        assert "cooldown_seconds" not in v.config or v.config["cooldown_seconds"] != 3600
    assert (await adm("strategy", "edit", "volume-spike@0.3.0", "--file", str(f)))[0] == 0
    code, err = await adm("strategy", "publish", "volume-spike@0.3.0", "--reason", "x", who="ana@x.io")
    assert code == 1 and err["code"] == "PERMISSION_DENIED"
    code, err = await adm("strategy", "publish", "volume-spike@0.3.0")
    assert code == 1 and err["code"] == "VALIDATION_FAILED"            # reason required, like the API
    assert (await adm("strategy", "publish", "volume-spike@0.3.0", "--reason", "cli"))[0] == 0
    code, out = await adm("bot", "bind", "paper-kraken-01", "--strategy", "volume-spike@0.3.0",
                          "--policy", "paper-default@1", "--reason", "via cli")
    assert code == 0 and out["status"] == "READY"
    api = await w.c.get("/api/v1/strategies/volume-spike/versions/0.3.0", headers=w.a())
    assert api.json()["status"] == "PUBLISHED" and api.json()["bots"] == ["paper-kraken-01"]
    code, diff = await adm("strategy", "diff", "volume-spike@0.2.0", "volume-spike@0.3.0")
    assert {d["path"] for d in diff} == {"version", "cooldown_seconds"}
    async with session_factory() as db:
        audit = (await db.scalars(select(AuditEvent).where(AuditEvent.action == "BOT_REBOUND"))).one()
    assert audit.actor == f"user:{w.admin.id}" and audit.reason == "via cli"
