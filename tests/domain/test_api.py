"""T-M1.4-01..07, 09 — REST API /api/v1 (network-free, real chassis JWT + RBAC)."""

from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.core.auth.security import create_access_token
from app.core.database import get_db
from app.core.models import UserRole
from app.domain.routers import terminal_ws_router, trading_v1_router
from app.domain.trading.enums import CommandStatus, Environment, PositionStatus, TradingRole
from app.domain.trading.models import AuditEvent, Bot, DomainEvent, Position
from app.domain.trading.rbac import TradingPrincipal, active_roles, grant_role

from .agent_fakes import NOW, add_instrument, add_minutes, engine, market_state, set_book
from .agent_fakes import Clock
from .factories import make_user

pytestmark = pytest.mark.anyio
D = Decimal


def _app(session_factory) -> FastAPI:
    app = FastAPI()
    app.include_router(trading_v1_router)
    app.include_router(terminal_ws_router)

    async def _db():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_db] = _db
    return app


def _h(user_id: int, **extra) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id)}", **extra}


class World:
    """Seeded bot + one TP-closed trade + one open position, users with roles."""

    async def build(self, session_factory):
        from app.domain.trading.seed import run_seed

        async with session_factory() as db:
            seed = await run_seed(db)
            await add_instrument(db)
            await add_minutes(db)
            self.admin = await make_user(db, "admin@x.io", UserRole.admin)
            self.observer = await make_user(db, "obs@x.io")
            self.risk = await make_user(db, "risk@x.io")
            self.plain = await make_user(db, "plain@x.io")
            await db.commit()
        self.bot_id = seed.bot.id
        self.clock = Clock(NOW)
        self.state = market_state()
        self.engine = engine(session_factory, self.state, self.clock)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(session_factory)),
                                        base_url="http://t")
        assert (await self.client.get("/api/v1/me", headers=_h(self.admin.id))).status_code == 200
        async with session_factory() as db:
            granter = TradingPrincipal(self.admin.id, await active_roles(db, self.admin.id))
            await grant_role(db, granter=granter, user_id=self.observer.id, role=TradingRole.OBSERVER)
            await grant_role(db, granter=granter, user_id=self.risk.id, role=TradingRole.RISK_OFFICER)
            await db.commit()
        return self

    async def trade_cycle(self):
        await self.engine.tick()                                   # autostart + entry
        set_book(self.state, asks=((108.2, 50),), bids=((108, 50),))
        self.clock.advance(seconds=10)
        await self.engine.tick()                                   # TP exit


@pytest.fixture
async def world(session_factory):
    w = await World().build(session_factory)
    yield w
    await w.client.aclose()


def _no_floats(obj, path="$"):
    if isinstance(obj, float):
        raise AssertionError(f"float at {path}")
    if isinstance(obj, dict):
        for k, v in obj.items():
            _no_floats(v, f"{path}.{k}")
    if isinstance(obj, list):
        for i, v in enumerate(obj):
            _no_floats(v, f"{path}[{i}]")


async def test_auth_on_every_read_route(world):
    b = world.bot_id
    routes = ["/api/v1/me", "/api/v1/health", "/api/v1/bots", f"/api/v1/bots/{b}",
              "/api/v1/strategies", "/api/v1/risk-policies", f"/api/v1/terminal/snapshot?bot_id={b}",
              f"/api/v1/positions?bot_id={b}", f"/api/v1/trades?bot_id={b}",
              f"/api/v1/orders?bot_id={b}", f"/api/v1/equity?bot_id={b}", "/api/v1/events",
              "/api/v1/market/feed"]
    for r in routes:
        assert (await world.client.get(r)).status_code == 401, r
        assert (await world.client.get(r, headers=_h(world.plain.id))).status_code == 403, r
        ok = await world.client.get(r, headers=_h(world.observer.id))
        assert ok.status_code == 200, (r, ok.text)
        _no_floats(ok.json())


async def test_problem_format_and_correlation_id(world):
    corr = "6b0ce1a2-3f2e-4a6e-9b55-0d0a6c3a1a11"
    r = await world.client.get("/api/v1/bots/00000000-0000-0000-0000-000000000000",
                               headers=_h(world.observer.id, **{"X-Correlation-ID": corr}))
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["code"] == "NOT_FOUND" and body["correlation_id"] == corr
    assert r.headers["X-Correlation-ID"] == corr
    r2 = await world.client.get("/api/v1/bots", headers=_h(world.observer.id))
    assert len(r2.headers["X-Correlation-ID"]) == 36


async def test_commands_idempotency_roles_and_audit(world, session_factory):
    url = f"/api/v1/bots/{world.bot_id}/commands"
    body = {"command": "PAUSE_ENTRIES", "reason": "test"}
    missing = await world.client.post(url, json=body, headers=_h(world.admin.id))
    assert missing.status_code == 422 and missing.json()["code"] == "VALIDATION_FAILED"

    denied = await world.client.post(url, json=body, headers=_h(world.observer.id, **{"Idempotency-Key": "k-obs-0001"}))
    assert denied.status_code == 403
    first = await world.client.post(url, json=body, headers=_h(world.admin.id, **{"Idempotency-Key": "k-adm-0001"}))
    assert first.status_code == 202 and first.json()["status"] == "PENDING"
    again = await world.client.post(url, json=body, headers=_h(world.admin.id, **{"Idempotency-Key": "k-adm-0001"}))
    assert again.json()["command_id"] == first.json()["command_id"]
    clash = await world.client.post(url, json={"command": "STOP"},
                                    headers=_h(world.admin.id, **{"Idempotency-Key": "k-adm-0001"}))
    assert clash.status_code == 409

    risk_stop = await world.client.post(url, json={"command": "EMERGENCY_STOP"},
                                        headers=_h(world.risk.id, **{"Idempotency-Key": "k-risk-0001"}))
    assert risk_stop.status_code == 202
    risk_pause = await world.client.post(url, json=body, headers=_h(world.risk.id, **{"Idempotency-Key": "k-risk-0002"}))
    assert risk_pause.status_code == 403
    async with session_factory() as db:
        actions = set((await db.scalars(select(AuditEvent.action))).all())
    assert {"BOT_COMMAND_DENIED", "BOT_COMMAND_REQUESTED"} <= actions


async def test_command_processed_by_worker(world, session_factory):
    url = f"/api/v1/bots/{world.bot_id}/commands"
    await world.engine.tick()                                      # bot RUNNING (autostart)
    r = await world.client.post(url, json={"command": "PAUSE_ENTRIES"},
                                headers=_h(world.admin.id, **{"Idempotency-Key": "k-pause-001"}))
    cid = r.json()["command_id"]
    world.clock.advance(seconds=5)
    await world.engine.tick()
    done = (await world.client.get(f"/api/v1/commands/{cid}", headers=_h(world.observer.id))).json()
    assert done["status"] == CommandStatus.COMPLETED.value
    bot = (await world.client.get(f"/api/v1/bots/{world.bot_id}", headers=_h(world.observer.id)))
    assert bot.json()["status"] == "PAUSED" and bot.headers["ETag"] == f'"{bot.json()["row_version"]}"'

    async with session_factory() as db:                            # LIVE start is rejected
        from app.domain.trading.bots import create_bot, transition_bot
        from app.domain.trading.enums import BotStatus
        from app.domain.trading.models import RiskPolicyVersion, StrategyVersion
        seed_bot = await db.get(Bot, world.bot_id)
        live = await create_bot(db, name="live-1", environment=Environment.LIVE,
                                strategy_version=await db.get(StrategyVersion, seed_bot.strategy_version_id),
                                risk_policy_version=await db.get(RiskPolicyVersion, seed_bot.risk_policy_version_id),
                                actor="user:1")
        await transition_bot(db, live, BotStatus.READY, actor="user:1", reason="t")
        await db.commit()
    r = await world.client.post(f"/api/v1/bots/{live.id}/commands", json={"command": "START"},
                                headers=_h(world.admin.id, **{"Idempotency-Key": "k-live-0001"}))
    world.clock.advance(seconds=5)
    await world.engine.tick()
    rej = (await world.client.get(f"/api/v1/commands/{r.json()['command_id']}", headers=_h(world.observer.id))).json()
    assert rej["status"] == "REJECTED" and rej["rejection_code"] == "LIVE_NOT_APPROVED"


async def test_snapshot_equity_stats_agents_scan(world, session_factory):
    await world.trade_cycle()
    snap = (await world.client.get(f"/api/v1/terminal/snapshot?bot_id={world.bot_id}",
                                   headers=_h(world.observer.id))).json()
    _no_floats(snap)
    assert snap["bot"]["status"] == "RUNNING" and snap["session"]["uptime_seconds"] >= 0
    assert snap["stats"] == {"trades": 1, "wins": 1, "losses": 0, "breakeven": 0}
    trade = snap["recent_trades"][0]
    assert trade["exit_reason"] == "TAKE_PROFIT" and D(trade["r_multiple"]) > 1
    eq = snap["equity"]
    assert eq["quality"] == "OK" and D(eq["equity"]) == D(eq["cash"]) + D(eq["cash_reserved"])
    assert D(eq["realized_pnl_net"]) == D(trade["realized_pnl_net"])
    assert set(snap["agents"]) == {"SCANNER", "RUG_CHECK", "SIZER", "EXECUTOR", "LOGGER", "EQUITY"}
    assert snap["agents"]["EXECUTOR"]["last_event_type"] == "POSITION_CLOSED"
    assert snap["scan"]["evaluated"] == 1 and snap["scan"]["candidates"] == 1
    assert snap["position"] == max(e["position"] for e in snap["recent_events"])
    # M1.5 complement: config, owner capital, entry features of the trade
    assert snap["config"]["strategy_version"] == "0.2.0" and snap["config"]["max_slippage_pct"] == "0.5"
    assert D(snap["owner_capital"]) == D(10000)
    assert D(trade["entry_features"]["volume_spike"]) == D(5)
    assert D(trade["entry_features"]["window_notional_usd"]) == D(100000)
    # review 2026-11-19: entry amounts of a closed trade (cost basis is 0 after the sale)
    assert D(trade["cost_basis"]) == 0 and D(trade["entry_notional"]) > D(1000)
    assert D(trade["entry_qty"]) * D(trade["avg_entry_price"]) == D(trade["entry_notional"])
    assert snap["entry_blocks"] == []


async def test_open_position_marks_and_stale_quality(world, session_factory):
    await world.engine.tick()                                      # entry, position open
    async with session_factory() as db:
        pos = await db.scalar(select(Position).where(Position.status == PositionStatus.OPEN))
        assert pos.mark_value is not None and pos.mark_price == D("100.9")   # T-M1.4-09
    from app.domain.trading.api.snapshot import build_snapshot
    async with session_factory() as db:
        fresh = await build_snapshot(db, world.bot_id, NOW + timedelta(seconds=5))
        stale = await build_snapshot(db, world.bot_id, NOW + timedelta(minutes=5))
    assert fresh["equity"]["quality"] == "OK" and fresh["equity"]["equity"] is not None
    assert stale["equity"]["quality"] == "STALE" and stale["equity"]["equity"] is None
    assert fresh["open_positions"][0]["unrealized_pnl"] is not None


async def test_scan_cycle_event_counts_fails(session_factory):
    w = await World().build(session_factory)
    try:
        async with session_factory() as db:   # make the window fail on spike only
            from app.domain.trading.models import TradeMinute
            for m in (await db.scalars(select(TradeMinute))).all():
                if m.minute_start < NOW.replace(second=0) - timedelta(minutes=5):
                    m.notional_usd = D(30000)
            await db.commit()
        await w.engine.tick()
        async with session_factory() as db:
            ev = await db.scalar(select(DomainEvent).where(DomainEvent.event_type == "SCAN_CYCLE_COMPLETED"))
        assert ev.payload["evaluated"] == 1 and ev.payload["candidates"] == 0
        assert ev.payload["fails"] == {"volume_spike": 1}
    finally:
        await w.client.aclose()


async def test_events_pagination_and_trace(world):
    await world.trade_cycle()
    h = _h(world.observer.id)
    seen, after = [], 0
    while True:
        page = (await world.client.get(f"/api/v1/events?bot_id={world.bot_id}&after={after}&limit=7",
                                       headers=h)).json()
        if not page["items"]:
            break
        seen += [e["position"] for e in page["items"]]
        after = page["next_after"]
    assert seen == sorted(set(seen)) and len(seen) > 7
    closed = next(e for e in (await world.client.get(
        f"/api/v1/events?bot_id={world.bot_id}&types=POSITION_CLOSED", headers=h)).json()["items"])
    trace = (await world.client.get(f"/api/v1/events/trace/{closed['correlation_id']}", headers=h)).json()
    types = [e["event_type"] for e in trace["items"]]
    assert types[0] == "CANDIDATE_DETECTED" and types[-1] == "POSITION_CLOSED"


async def test_entry_blocks_daily_loss_and_paused(world, session_factory):
    """The terminal shows why entries are blocked (same rules as the SIZER)."""
    from app.domain.trading.models import EquitySnapshot
    await world.engine.tick()                                       # RUNNING, one open position
    async with session_factory() as db:
        db.add(EquitySnapshot(bot_id=world.bot_id, at=NOW.replace(hour=0, minute=1), cash=D(11000),
                              cash_reserved=D(0), positions_mark_value=D(0), equity=D(11000),
                              realized_pnl_net=D(0), unrealized_pnl=D(0), quality="OK"))
        await db.commit()
    from app.domain.trading.api.snapshot import build_snapshot
    async with session_factory() as db:
        snap = await build_snapshot(db, world.bot_id, NOW + timedelta(seconds=5))
    codes = {b["code"]: b for b in snap["entry_blocks"]}
    assert "MAX_DAILY_LOSS" in codes and codes["MAX_DAILY_LOSS"]["limit"] == D("0.03")
    assert codes["MAX_DAILY_LOSS"]["until"].hour == 0
    r = await world.client.post(f"/api/v1/bots/{world.bot_id}/commands", json={"command": "PAUSE_ENTRIES"},
                                headers=_h(world.admin.id, **{"Idempotency-Key": "k-pause-blk"}))
    assert r.status_code == 202
    world.clock.advance(seconds=5)
    await world.engine.tick()
    snap = (await world.client.get(f"/api/v1/terminal/snapshot?bot_id={world.bot_id}",
                                   headers=_h(world.observer.id))).json()
    _no_floats(snap)
    assert any(b["code"] == "BOT_NOT_RUNNING" and b["value"] == "PAUSED" for b in snap["entry_blocks"])

