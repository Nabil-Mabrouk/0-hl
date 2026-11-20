"""UI-00 — public landing statistics (03-ecrans/UI-00-accueil-public, T-UI00-01)."""

import httpx
import pytest
from fastapi import FastAPI

from app.core.database import get_db
from app.domain.routers import public_v1_router
from app.domain.trading.api import public

from .agent_fakes import NOW, Clock, add_instrument, add_minutes, engine, market_state, set_book
from app.domain.trading.seed import run_seed

pytestmark = pytest.mark.anyio


def _client(session_factory) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(public_v1_router)

    async def _db():
        async with session_factory() as s:
            yield s
    app.dependency_overrides[get_db] = _db
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_public_stats_without_token_and_without_sensitive_fields(session_factory):
    public._cache.update(at=0.0, data=None)
    async with session_factory() as db:
        await run_seed(db)
        await add_instrument(db)
        await add_minutes(db)
        await db.commit()
    clock, st = Clock(NOW), market_state()
    eng = engine(session_factory, st, clock)
    await eng.tick()                                                   # autostart + entry
    set_book(st, asks=((108.2, 50),), bids=((108, 50),))
    clock.advance(seconds=10)
    await eng.tick()                                                   # take profit
    async with _client(session_factory) as c:
        r = await c.get("/api/v1/public/stats")                        # no Authorization header
    assert r.status_code == 200 and "max-age=30" in r.headers["Cache-Control"]
    body = r.json()
    assert body["bots_total"] == 1 and body["bots_running"] == 1
    assert body["trades_total"] == 1 and body["open_positions"] == 0
    assert body["evaluations_24h"] >= 1 and body["market_data_days"] is not None
    assert set(body) == {"bots_total", "bots_running", "trades_total", "trades_24h", "open_positions",
                         "pairs_live", "pairs_subscribed", "worker_alive", "evaluations_24h",
                         "market_data_days", "last_trade_at", "generated_at"}
    assert "paper-kraken-01" not in r.text                             # no bot name, no amount


async def test_public_stats_are_cached(session_factory):
    public._cache.update(at=0.0, data=None)
    async with session_factory() as db:
        await run_seed(db)
        await db.commit()
    async with _client(session_factory) as c:
        first = (await c.get("/api/v1/public/stats")).json()
        async with session_factory() as db:
            from app.domain.trading.models import Bot
            from app.domain.trading.enums import BotStatus
            bot = (await db.scalars(__import__("sqlalchemy").select(Bot))).one()
            bot.status = BotStatus.RUNNING
            await db.commit()
        again = (await c.get("/api/v1/public/stats")).json()
    assert first == again and first["bots_running"] == 0 and first["last_trade_at"] is None
    public._cache.update(at=0.0, data=None)
