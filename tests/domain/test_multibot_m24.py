"""M2.4 — several paper bots (06-agents/04, T-M2.4-01..06)."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.domain.trading import lifecycle as lc
from app.domain.trading.agents import commands as cmds
from app.domain.trading.agents.engine import DecisionEngine
from app.domain.trading.config_schemas import validate_strategy_config
from app.domain.trading.enums import BotStatus, CommandType, PositionStatus
from app.domain.trading.ledger import account_balances
from app.domain.trading.models import (
    AuditEvent,
    Bot,
    BotSession,
    CashMovement,
    DomainEvent,
    Instrument,
    Position,
    RiskPolicyVersion,
    StrategyVersion,
)
from app.domain.trading.seed import run_seed

from .agent_fakes import NOW, Clock, add_instrument, add_minutes, engine, market_state
from .test_versions_m2 import w  # noqa: F401  (fixture: seeded world + API client)

pytestmark = pytest.mark.anyio
D = Decimal


def _body(w, **kw):
    return {"name": "bot-b", "strategy_version_id": w.sv0, "risk_policy_version_id": w.pv0,
            "capital_usd": "5000", "reason": "comparaison"} | kw


# T-M2.4-01 / 02 / 05 -----------------------------------------------------------------------
async def test_create_bot_rules_capital_and_limit(w, session_factory):  # noqa: F811
    r = await w.c.post("/api/v1/bots", json=_body(w), headers=w.a())
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["status"] == "READY" and created["environment"] == "PAPER"
    async with session_factory() as db:
        bid = uuid.UUID(created["id"])
        dep = (await db.scalars(select(CashMovement).where(CashMovement.bot_id == bid))).one()
        bal = await account_balances(db, bid)
        audits = {a.action for a in (await db.scalars(select(AuditEvent).where(
            AuditEvent.entity_id == str(bid)))).all()}
    assert dep.amount == D(5000) and dep.kind.value == "DEPOSIT"
    assert sum(bal.values(), D(0)) == 0                                # balanced ledger
    assert {"BOT_CREATED", "BOT_CREATED_BY_OPERATOR"} <= audits

    listing = (await w.c.get("/api/v1/bots", headers=w.a())).json()["items"]
    b = next(x for x in listing if x["name"] == "bot-b")
    assert b["capital_usd"] == "5000" and b["strategy"]["version"] == "0.2.0"

    bad = [({"name": "bot-b"}, 409), ({"name": "Bad Name"}, 422), ({"name": "b2", "capital_usd": "50"}, 422),
           ({"name": "bot-c", "capital_usd": "2000000"}, 422), ({"name": "bot-c", "reason": ""}, 422)]
    for patch, code in bad:
        resp = await w.c.post("/api/v1/bots", json=_body(w, **patch), headers=w.a())
        assert resp.status_code == code, (patch, resp.text)
    draft = await w.duplicate("strategies", w.sv0, "0.3.0")
    nope = await w.c.post("/api/v1/bots", json=_body(w, name="bot-d", strategy_version_id=draft["id"]), headers=w.a())
    assert nope.json()["code"] == "STRATEGY_NOT_PUBLISHED"
    assert (await w.c.post("/api/v1/bots", json=_body(w, name="bot-e"), headers=_h_obs(w))).status_code == 403

    for i in range(3):                                                 # seed + bot-b + 3 = 5
        assert (await w.c.post("/api/v1/bots", json=_body(w, name=f"bot-x{i}"), headers=w.a())).status_code == 201
    full = await w.c.post("/api/v1/bots", json=_body(w, name="bot-y"), headers=w.a())
    assert full.status_code == 409 and full.json()["code"] == "BOT_LIMIT_REACHED"
    async with session_factory() as db:                                # a STOPPED bot does not count
        x0 = await db.scalar(select(Bot).where(Bot.name == "bot-x0"))
        x0.status = BotStatus.STOPPED
        await db.commit()
    assert (await w.c.post("/api/v1/bots", json=_body(w, name="bot-y"), headers=w.a())).status_code == 201

    await w.engine.tick()                                              # T-M2.4-05: no autostart
    async with session_factory() as db:
        assert (await db.scalar(select(Bot).where(Bot.name == "bot-b"))).status is BotStatus.READY
        assert (await db.scalar(select(BotSession.id).where(BotSession.bot_id == bid))) is None


def _h_obs(w):
    from .test_versions_m2 import _h
    return _h(w.observer.id)


# T-M2.4-03 ---------------------------------------------------------------------------------
def test_bot_universe_follows_its_strategy():
    cfg = validate_strategy_config({**__import__("json").loads(
        __import__("app.domain.trading.seed", fromlist=["STRATEGY_FILE"]).STRATEGY_FILE.read_text(encoding="utf-8")),
        "status": "DRAFT"})
    rows = [("kraken_spot:BTC/USD", D(9e8), "BTC", "USD"), ("kraken_spot:USDT/USD", D(9e9), "USDT", "USD"),
            ("kraken_spot:ETH/USD", D(5e8), "ETH", "USD"), ("kraken_spot:DUST/USD", D(1000), "DUST", "USD"),
            ("kraken_spot:ETH/EUR", D(9e8), "ETH", "EUR")]
    full = DecisionEngine.bot_universe(cfg, rows)
    assert list(full) == ["kraken_spot:BTC/USD", "kraken_spot:ETH/USD"]   # stablecoin, min volume, quote
    one = cfg.model_copy(update={"universe": cfg.universe.model_copy(update={"top_n_by_volume": 1})})
    assert list(DecisionEngine.bot_universe(one, rows)) == ["kraken_spot:BTC/USD"]


async def test_scan_cycle_covers_only_the_bot_universe(session_factory):
    async with session_factory() as db:
        seed = await run_seed(db)
        await add_instrument(db)                                       # TEST/USD, 50 M volume
        db.add(Instrument(id="kraken_spot:LOW/USD", venue="kraken_spot", symbol="LOW/USD", base="LOW",
                          quote="USD", pair_decimals=2, lot_decimals=4, status="online", in_universe=True,
                          volume_24h_usd=D(2000000)))
        await add_minutes(db)
        sv = await lc.duplicate_strategy_version(db, seed.strategy_version, version="0.3.0", actor="user:1")
        sv.config = {**sv.config, "universe": {**sv.config["universe"], "top_n_by_volume": 1}}
        sv.config_sha256 = "x" * 64
        await lc.publish_strategy(db, sv, actor="user:1", reason="t")
        narrow = await lc.create_paper_bot(db, name="narrow", strategy_version=sv,
                                           risk_policy_version=seed.policy_version, capital=D(1000),
                                           actor="user:1", reason="t")
        await cmds.create_command(db, bot_id=narrow.id, command=CommandType.START, actor="user:1",
                                  idempotency_key="start-narrow", reason="t")
        await db.commit()
    await engine(session_factory, market_state(), Clock(NOW)).tick()
    async with session_factory() as db:
        cycles = {e.bot_id: e.payload["evaluated"] for e in (await db.scalars(select(DomainEvent).where(
            DomainEvent.event_type == "SCAN_CYCLE_COMPLETED"))).all()}
    assert cycles[narrow.id] == 1 and cycles[seed.bot.id] == 2


# T-M2.4-04 ---------------------------------------------------------------------------------
async def test_bots_are_isolated(session_factory):
    async with session_factory() as db:
        seed = await run_seed(db)
        await add_instrument(db)
        await add_minutes(db)
        killed = await lc.duplicate_policy_version(db, seed.policy_version, version="2", actor="user:1")
        killed.config = {**killed.config, "kill_switch": True}
        killed.config_sha256 = "k" * 64
        out = await lc.request_policy_publication(db, killed, actor="user:1", reason="t")
        assert out.status == "PUBLISHED"                               # stricter ⇒ immediate
        twin = await lc.create_paper_bot(db, name="twin", strategy_version=seed.strategy_version,
                                         risk_policy_version=seed.policy_version, capital=D(10000),
                                         actor="user:1", reason="t")
        blocked = await lc.create_paper_bot(db, name="blocked", strategy_version=seed.strategy_version,
                                            risk_policy_version=killed, capital=D(10000),
                                            actor="user:1", reason="t")
        for b in (twin, blocked):
            await cmds.create_command(db, bot_id=b.id, command=CommandType.START, actor="user:1",
                                      idempotency_key=f"start-{b.name}", reason="t")
        await db.commit()
    clock, st = Clock(NOW), market_state()
    eng = engine(session_factory, st, clock)
    await eng.tick()                                                   # seed + twin enter, blocked refused
    async with session_factory() as db:
        pos = (await db.scalars(select(Position).where(Position.status == PositionStatus.OPEN))).all()
        refusals = (await db.scalars(select(DomainEvent).where(DomainEvent.event_type == "RISK_REJECTED",
                                                               DomainEvent.bot_id == blocked.id))).all()
    assert {p.bot_id for p in pos} == {seed.bot.id, twin.id}          # same pair, two positions
    assert {p.instrument_id for p in pos} == {"kraken_spot:TEST/USD"}
    assert refusals and "KILL_SWITCH" in refusals[0].payload["reasons"]

    async with session_factory() as db:                                # E-STOP the seed bot only
        await cmds.create_command(db, bot_id=seed.bot.id, command=CommandType.EMERGENCY_STOP,
                                  actor="user:1", idempotency_key="estop-seed", reason="t")
        await db.commit()
    clock.advance(seconds=5)
    await eng.tick()
    clock.advance(seconds=5)
    await eng.tick()
    async with session_factory() as db:
        mine = (await db.scalars(select(Position).where(Position.bot_id == seed.bot.id))).all()
        other = (await db.scalars(select(Position).where(Position.bot_id == twin.id))).all()
        cash_seed = (await account_balances(db, seed.bot.id))
        cash_twin = (await account_balances(db, twin.id))
    assert all(p.status is PositionStatus.CLOSED for p in mine)
    assert [p.status for p in other] == [PositionStatus.OPEN]
    assert cash_seed != cash_twin                                      # separate books


# T-M2.4-06 ---------------------------------------------------------------------------------
async def test_admin_command_creates_the_same_bot(w, session_factory):  # noqa: F811
    from app.domain.trading.admin import execute
    code, out = await execute(session_factory, ["--as", "admin@x.io", "bot", "create", "cli-bot", "--strategy",
                                                "volume-spike@0.2.0", "--policy", "paper-default@1",
                                                "--capital", "2500", "--reason", "via cli"])
    assert code == 0 and out["status"] == "READY"
    code, err = await execute(session_factory, ["--as", "admin@x.io", "bot", "create", "cli-bot", "--strategy",
                                                "volume-spike@0.2.0", "--policy", "paper-default@1", "--reason", "x"])
    assert code == 1 and err["code"] == "STATE_CONFLICT"
    code, err = await execute(session_factory, ["--as", "ana@x.io", "bot", "create", "cli-2", "--strategy",
                                                "volume-spike@0.2.0", "--policy", "paper-default@1", "--reason", "x"])
    assert code == 1 and err["code"] == "PERMISSION_DENIED"
    async with session_factory() as db:
        bot = await db.scalar(select(Bot).where(Bot.name == "cli-bot"))
        dep = (await db.scalars(select(CashMovement).where(CashMovement.bot_id == bot.id))).one()
        pv = await db.get(RiskPolicyVersion, bot.risk_policy_version_id)
        sv = await db.get(StrategyVersion, bot.strategy_version_id)
    assert dep.amount == D(2500) and (sv.version, pv.version) == ("0.2.0", "1")
