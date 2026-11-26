"""ADR-032 — rename, delete (archive) and the 10-bot cap (06-agents/04 « Gestion des bots », T-B-01..06)."""

from decimal import Decimal

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from app.core.database import get_db
from app.domain.routers import trading_v1_router, versions_v1_router
from app.domain.trading import lifecycle as lc
from app.domain.trading.agents import commands as cmds
from app.domain.trading.api.public import public_stats
from app.domain.trading.enums import BotStatus, PositionStatus
from app.domain.trading.ledger import account_balances
from app.domain.trading.models import (
    AuditEvent,
    Bot,
    DomainEvent,
    LedgerEntry,
    Position,
    RiskPolicyVersion,
    StrategyVersion,
)
from app.domain.trading.seed import SEED_BOT_NAME, run_seed
from app.domain.trading.types import utcnow

from .test_api import World, _h

pytestmark = pytest.mark.anyio
D = Decimal


@pytest.fixture
async def bw(session_factory):
    w = await World().build(session_factory)
    app = FastAPI()
    app.include_router(trading_v1_router)
    app.include_router(versions_v1_router)

    async def _db():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_db] = _db
    w.v = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
    async with session_factory() as db:
        w.sv0 = str(await db.scalar(select(Bot.strategy_version_id).where(Bot.id == w.bot_id)))
        w.pv0 = str(await db.scalar(select(Bot.risk_policy_version_id).where(Bot.id == w.bot_id)))
    yield w
    await w.v.aclose()
    await w.client.aclose()


def _a(w):
    return _h(w.admin.id)


async def _bot(w, bot_id) -> dict:
    return (await w.v.get(f"/api/v1/bots/{bot_id}", headers=_a(w))).json()


async def _create(w, name: str) -> dict:
    r = await w.v.post("/api/v1/bots", headers=_a(w), json={
        "name": name, "strategy_version_id": w.sv0, "risk_policy_version_id": w.pv0,
        "capital_usd": "1000", "reason": "test"})
    assert r.status_code == 201, r.text
    return r.json()


async def _rename(w, bot_id, name, rv=None, headers=None):
    body = {"name": name, "reason": "clearer name"}
    if rv is not None:
        body["row_version"] = rv
    return await w.v.patch(f"/api/v1/bots/{bot_id}", json=body, headers=headers or _a(w))


async def _delete(w, bot_id, headers=None):
    return await w.v.post(f"/api/v1/bots/{bot_id}/delete", json={"reason": "no longer needed"},
                          headers=headers or _a(w))


# T-B-01 ------------------------------------------------------------------------------------
async def test_rename_rules_audit_and_history_follow(bw, session_factory):
    await bw.trade_cycle()                                            # running seed bot, one closed trade
    other = await _create(bw, "bot-b")
    rv = (await _bot(bw, bw.bot_id))["row_version"]
    assert (await _rename(bw, bw.bot_id, "Bad Name")).status_code == 422
    taken = await _rename(bw, bw.bot_id, "bot-b")
    assert taken.status_code == 409 and taken.json()["code"] == "STATE_CONFLICT"
    stale = await _rename(bw, bw.bot_id, "seed-renamed", rv=rv - 1)
    assert stale.status_code == 409 and stale.json()["code"] == "STALE_VERSION"
    assert (await _rename(bw, bw.bot_id, "seed-x", headers=_h(bw.observer.id))).status_code == 403

    r = await _rename(bw, bw.bot_id, "seed-renamed", rv=rv)
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "seed-renamed" and r.json()["status"] == "RUNNING"   # no interruption
    names = {b["name"] for b in (await bw.client.get("/api/v1/bots", headers=_a(bw))).json()["items"]}
    assert names == {"seed-renamed", other["name"]}
    trades = (await bw.client.get(f"/api/v1/trades?bot_id={bw.bot_id}", headers=_a(bw))).json()
    assert trades["total"] == 1                                       # history follows the bot id
    async with session_factory() as db:
        audit = await db.scalar(select(AuditEvent).where(AuditEvent.action == "BOT_RENAMED"))
        event = await db.scalar(select(DomainEvent).where(DomainEvent.event_type == "BOT_RENAMED"))
    assert audit.diff == {"from": SEED_BOT_NAME, "to": "seed-renamed"} and audit.reason == "clearer name"
    assert event.payload["from"] == SEED_BOT_NAME and event.payload["to"] == "seed-renamed"
    bw.clock.advance(seconds=60)
    await bw.engine.tick()                                            # the engine reads the new name


# T-B-02 / T-B-05 -----------------------------------------------------------------------------
async def test_deleted_bot_frees_its_name_and_its_slot(bw, session_factory):
    b = await _create(bw, "bot-b")
    async with session_factory() as db:
        before = await lc.count_active_bots(db)
    assert (await _delete(bw, b["id"])).status_code == 200            # READY ⇒ deletable
    async with session_factory() as db:
        assert await lc.count_active_bots(db) == before - 1
    gone = await _rename(bw, b["id"], "bot-c")
    assert gone.status_code == 409 and gone.json()["code"] == "STATE_CONFLICT"
    again = await _create(bw, "bot-b")                                # the name is free again
    assert again["id"] != b["id"]

    async with session_factory() as db:                               # cap: 10, deleted bots not counted
        sv = await db.get(StrategyVersion, __import__("uuid").UUID(bw.sv0))
        pv = await db.get(RiskPolicyVersion, __import__("uuid").UUID(bw.pv0))
        for i in range(lc.MAX_ACTIVE_BOTS - await lc.count_active_bots(db)):
            await lc.create_paper_bot(db, name=f"fill-{i}", strategy_version=sv, risk_policy_version=pv,
                                      capital=D(1000), actor="user:x", reason="fill")
        await db.commit()
    assert lc.MAX_ACTIVE_BOTS == 10
    full = await bw.v.post("/api/v1/bots", headers=_a(bw), json={
        "name": "bot-11", "strategy_version_id": bw.sv0, "risk_policy_version_id": bw.pv0,
        "capital_usd": "1000", "reason": "test"})
    assert full.status_code == 409 and full.json()["code"] == "BOT_LIMIT_REACHED"
    listing = (await bw.client.get("/api/v1/bots", headers=_a(bw))).json()
    assert (listing["active"], listing["limit"]) == (10, 10)


# T-B-03 ------------------------------------------------------------------------------------
async def test_delete_conditions_and_nothing_erased(bw, session_factory):
    await bw.engine.tick()                                            # autostart + entry: open position
    running = await _delete(bw, bw.bot_id)
    assert running.status_code == 409 and "stop the bot first" in running.json()["extra"]["blockers"]
    async with session_factory() as db:
        bot = await db.get(Bot, bw.bot_id)
        bot.status = BotStatus.STOPPED                                # stopped, position still open
        await db.commit()
    busy = await _delete(bw, bw.bot_id)
    assert busy.json()["extra"]["blockers"] == ["close its open positions first"]
    listed = (await bw.client.get("/api/v1/bots", headers=_a(bw))).json()["items"][0]
    assert listed["delete_blockers"] == ["close its open positions first"]
    async with session_factory() as db:
        for p in (await db.scalars(select(Position).where(Position.bot_id == bw.bot_id))).all():
            p.status, p.closed_at = PositionStatus.CLOSED, utcnow()
        await db.commit()
        counts = (await db.scalar(select(func.count()).select_from(Position)),
                  await db.scalar(select(func.count()).select_from(LedgerEntry)))
    assert (await _delete(bw, bw.bot_id, headers=_h(bw.risk.id))).status_code == 403   # needs both roles
    ok = await _delete(bw, bw.bot_id)
    assert ok.status_code == 200 and ok.json()["deleted_at"] is not None
    async with session_factory() as db:
        assert (await db.scalar(select(func.count()).select_from(Position)),
                await db.scalar(select(func.count()).select_from(LedgerEntry))) == counts
        assert sum((await account_balances(db, bw.bot_id)).values(), D(0)) == 0
        audit = await db.scalar(select(AuditEvent).where(AuditEvent.action == "BOT_DELETED"))
    assert audit.diff["name"] == SEED_BOT_NAME and audit.diff["final_cash_usd"]
    assert (await _delete(bw, bw.bot_id)).status_code == 409          # already deleted


# T-B-04 ------------------------------------------------------------------------------------
async def test_deleted_bot_disappears_but_its_trades_remain(bw, session_factory):
    await bw.trade_cycle()
    async with session_factory() as db:
        stats_before = await public_stats(db, utcnow())
        bot = await db.get(Bot, bw.bot_id)
        bot.status = BotStatus.STOPPED
        await db.commit()
    assert (await _delete(bw, bw.bot_id)).status_code == 200
    assert (await bw.client.get("/api/v1/bots", headers=_a(bw))).json()["items"] == []
    with_deleted = (await bw.client.get("/api/v1/bots?include_deleted=true", headers=_a(bw))).json()["items"]
    assert [b["name"] for b in with_deleted] == [SEED_BOT_NAME] and with_deleted[0]["deleted_at"]
    assert (await bw.client.get("/api/v1/health", headers=_a(bw))).json()["bots"] == []
    trades = (await bw.client.get(f"/api/v1/trades?bot_id={bw.bot_id}", headers=_a(bw))).json()
    assert trades["total"] == 1
    async with session_factory() as db:
        stats = await public_stats(db, utcnow())
    assert stats["bots_total"] == stats_before["bots_total"] - 1
    assert stats["trades_total"] == stats_before["trades_total"]      # history unchanged
    cmd = await bw.client.post(f"/api/v1/bots/{bw.bot_id}/commands", json={"command": "START"},
                               headers=_h(bw.admin.id, **{"Idempotency-Key": "after-delete-1"}))
    assert cmd.status_code == 409


# T-B-06 ------------------------------------------------------------------------------------
async def test_seed_does_not_recreate_renamed_or_deleted_bots(session_factory):
    async with session_factory() as db:
        await run_seed(db, observation=True)
        names = set((await db.scalars(select(Bot.name))).all())
        assert {"paper-kraken-02", "paper-kraken-03"} <= names
        b2 = await db.scalar(select(Bot).where(Bot.name == "paper-kraken-02"))
        b3 = await db.scalar(select(Bot).where(Bot.name == "paper-kraken-03"))
        seed = await db.scalar(select(Bot).where(Bot.name == SEED_BOT_NAME))
        await lc.rename_bot(db, b2, name="breakout-watch", actor="user:x", reason="r")
        await lc.rename_bot(db, seed, name="volume-watch", actor="user:x", reason="r")
        await lc.delete_bot(db, b3, actor="user:x", reason="r")
        await db.commit()
        total = await db.scalar(select(func.count()).select_from(Bot))
    async with session_factory() as db:
        result = await run_seed(db, observation=True)
        assert await db.scalar(select(func.count()).select_from(Bot)) == total
        assert result.bot.name == "volume-watch"
        live = set((await db.scalars(select(Bot.name).where(Bot.deleted_at.is_(None)))).all())
        assert not live & {SEED_BOT_NAME, "paper-kraken-02", "paper-kraken-03"}
        started = await cmds.ensure_autostart(db)                     # ADR-013 follows the identity
        assert [c.bot_id for c in started] == [result.bot.id]
