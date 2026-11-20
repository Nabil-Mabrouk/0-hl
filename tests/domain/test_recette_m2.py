"""M2.6 — end-to-end path and M2 acceptance report (12-tests/03-recette-m2, T-M2.6-01..06)."""

import json
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, update

from app.core.database import Base
from app.domain.trading import lifecycle as lc
from app.domain.trading.agents.commands import create_command
from app.domain.trading.enums import CommandType
from app.domain.trading.models import (
    AuditEvent,
    BookSnapshot,
    LedgerEntry,
    LedgerTransaction,
    Position,
    StrategyVersion,
    TradeMinute,
)
from app.domain.trading.recette_m2 import build_report_m2
from app.domain.trading.research.backtest import build_backtest
from app.domain.trading.seed import run_seed

from .agent_fakes import IID, NOW, Clock, add_instrument, add_minutes, engine, market_state, set_book
from .test_backtest import START, _prod
from .test_versions_m2 import _h, w  # noqa: F401  (fixture)

pytestmark = pytest.mark.anyio
D = Decimal


def _check(report, cid):
    return next(c for c in report.checks if c.id == cid)


async def _counts(session_factory):
    async with session_factory() as db:
        return {t.name: await db.scalar(select(func.count()).select_from(t))
                for t in Base.metadata.sorted_tables if t.name.startswith("hl_")}


# T-M2.6-01 ---------------------------------------------------------------------------------
async def test_m2_path_end_to_end_without_code(w, session_factory):  # noqa: F811
    await _prod(session_factory)                                       # instrument, minutes, book snapshots
    # 1. draft from STRAT-01, change a parameter, publish
    v3 = await w.duplicate("strategies", w.sv0, "0.3.0")
    v3 = await w.edit("strategies", v3, cooldown_seconds=3600)
    pub = await w.c.post(f"/api/v1/strategies/versions/{v3['id']}/publish",
                         json={"reason": "hypothèse : un cooldown plus long évite les rachats perdants"}, headers=w.a())
    assert pub.status_code == 200
    # 2. backtest twice: same data fingerprint, same report
    args = dict(strategy_ref="volume-spike@0.3.0", policy_ref=None, start=START, end=START + timedelta(minutes=15),
                pairs=None, capital=D(10000), split=D("0.6"), fee_multiplier=D(1))
    a, b = await build_backtest(session_factory, **args), await build_backtest(session_factory, **args)
    assert json.dumps(a.as_json()) == json.dumps(b.as_json()) and a.overall.trades == 1
    # 3. dedicated bot next to the seed bot
    created = await w.c.post("/api/v1/bots", headers=w.a(), json={
        "name": "paper-v3", "strategy_version_id": v3["id"], "risk_policy_version_id": w.pv0,
        "capital_usd": "10000", "reason": "gate M2 : comparaison v0.3.0 / v0.2.0"})
    assert created.status_code == 201
    start = await w.c.post(f"/api/v1/bots/{created.json()['id']}/commands", json={"command": "START"},
                           headers=w.a(**{"Idempotency-Key": "k-start-v3"}))
    assert start.status_code == 202
    # 4. engine on the same market: both bots enter, then take profit
    await w.engine.tick()
    set_book(w.engine.market, asks=((108.2, 50),), bids=((108, 50),))
    w.clock.advance(seconds=10)
    await w.engine.tick()
    # 5. trades carry their version; stats compare both versions
    obs = _h(w.observer.id)
    trades = (await w.c.get("/api/v1/trades", headers=obs)).json()["items"]
    assert sorted(t["strategy"]["version"] for t in trades) == ["0.2.0", "0.3.0"]
    stats = (await w.c.get("/api/v1/stats", headers=obs)).json()
    assert [v["version"] for v in stats["by_version"]] == ["0.2.0", "0.3.0"]
    # 6. audits with actor and reason; the M2 report passes
    async with session_factory() as db:
        acts = {a.action: a for a in (await db.scalars(select(AuditEvent))).all()}
    assert acts["STRATEGY_PUBLISHED"].reason.startswith("hypothèse")
    assert acts["BOT_CREATED_BY_OPERATOR"].reason.startswith("gate M2")
    report = await build_report_m2(session_factory, now=w.clock.now)
    assert report.passed, report.as_text()


# T-M2.6-02 ---------------------------------------------------------------------------------
async def test_tampered_version_config_is_blocking(session_factory):
    async with session_factory() as db:
        seed = await run_seed(db)
        await db.execute(update(StrategyVersion).where(StrategyVersion.id == seed.strategy_version.id)
                         .values(config={**seed.strategy_version.config, "cooldown_seconds": 1}))
        await db.commit()
    report = await build_report_m2(session_factory)
    assert _check(report, "R2-01").status == "KO" and not report.passed


# T-M2.6-03 ---------------------------------------------------------------------------------
async def test_books_and_isolation_are_checked_per_bot(session_factory):
    async with session_factory() as db:
        seed = await run_seed(db)
        await add_instrument(db)
        await add_minutes(db)
        other = await lc.create_paper_bot(db, name="other", strategy_version=seed.strategy_version,
                                          risk_policy_version=seed.policy_version, capital=D(1000),
                                          actor="user:1", reason="t")
        await db.commit()
    clock, st = Clock(NOW), market_state()
    await engine(session_factory, st, clock).tick()                    # seed bot opens a position
    assert (await build_report_m2(session_factory, now=clock.now)).passed

    async with session_factory() as db:                                # unbalanced entry on `other`
        tx = (await db.scalars(select(LedgerTransaction).where(LedgerTransaction.bot_id == other.id))).first()
        db.add(LedgerEntry(transaction_id=tx.id, bot_id=other.id, account_code="cash", asset="USD", amount=D(1)))
        await db.commit()
    r = await build_report_m2(session_factory, now=clock.now)
    ko = _check(r, "R2-04")
    assert ko.status == "KO" and all(d.startswith("other") for d in ko.details)
    assert ko.data["paper-kraken-01"]["ledger"] == "OK"

    async with session_factory() as db:                                # position moved to another bot
        await db.execute(update(Position).values(bot_id=other.id))
        await db.commit()
    assert _check(await build_report_m2(session_factory, now=clock.now), "R2-05").status == "KO"


# T-M2.6-04 ---------------------------------------------------------------------------------
async def test_unexplained_change_is_blocking(session_factory):
    async with session_factory() as db:
        seed = await run_seed(db)
        db.add(AuditEvent(actor="user:1", action="BOT_REBOUND", entity_type="bot", entity_id=str(seed.bot.id),
                          reason="", diff={}))
        await db.commit()
    assert _check(await build_report_m2(session_factory), "R2-06").status == "KO"


# T-M2.6-05 ---------------------------------------------------------------------------------
async def test_history_and_gate_eligibility(session_factory):
    async with session_factory() as db:
        await run_seed(db)
        await db.commit()
    empty = _check(await build_report_m2(session_factory, now=NOW), "R2-07")
    assert empty.data["snapshot_days"] is None and empty.data["gate_eligible_from"] is None
    assert "gate M2 possible à partir du —" in empty.details[-1]

    async with session_factory() as db:
        await add_instrument(db)
        t0 = NOW - timedelta(days=10)
        for m in (t0, NOW):
            db.add(TradeMinute(instrument_id=IID, minute_start=m, source="live", notional_usd=D(1),
                               trade_count=1, volume_base=D(1)))
            db.add(BookSnapshot(instrument_id=IID, minute_start=m, captured_at=m, best_bid=D(1),
                                best_ask=D(2), spread_bps=D(1), bids=[["1", "1"]], asks=[["2", "1"]]))
        await db.commit()
    hist = _check(await build_report_m2(session_factory, now=NOW), "R2-07")
    assert hist.data["snapshot_days"] == D(10) and hist.data["minutes_days"] == D(10)
    assert hist.data["gate_eligible_from"] == t0 + timedelta(days=28)
    assert "(atteint)" not in hist.details[-1]


# T-M2.6-06 ---------------------------------------------------------------------------------
async def test_report_is_read_only(session_factory):
    async with session_factory() as db:
        seed = await run_seed(db)
        await add_instrument(db)
        await add_minutes(db)
        await create_command(db, bot_id=seed.bot.id, command=CommandType.START, actor="user:1",
                             idempotency_key=f"s-{uuid.uuid4()}", reason="t")
        await db.commit()
    before = await _counts(session_factory)
    report = await build_report_m2(session_factory)
    assert await _counts(session_factory) == before
    assert "CRITÈRES BLOQUANTS M2" in report.as_text() and report.as_json()["checks"][0]["id"] == "R2-01"
