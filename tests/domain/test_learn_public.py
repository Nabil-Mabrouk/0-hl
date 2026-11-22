"""Learn — public strategy data (03-ecrans/UI-17 « API publique », T-L01 / T-L02)."""

import re

import httpx
import pytest
from fastapi import FastAPI

from app.core.database import get_db
from app.domain.routers import public_v1_router
from app.domain.trading import learn
from app.domain.trading.analytics import compute_stats, trade_rows
from app.domain.trading.seed import run_seed

from .agent_fakes import NOW, Clock, add_instrument, add_minutes, engine, market_state, set_book

pytestmark = pytest.mark.anyio


def _client(session_factory) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(public_v1_router)

    async def _db():
        async with session_factory() as s:
            yield s
    app.dependency_overrides[get_db] = _db
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def _one_closed_trade(session_factory):
    async with session_factory() as db:
        seed = await run_seed(db)
        await add_instrument(db)
        await add_minutes(db)
        await db.commit()
    clock, st = Clock(NOW), market_state()
    eng = engine(session_factory, st, clock)
    await eng.tick()                                                   # autostart + entry
    set_book(st, asks=((108.2, 50),), bids=((108, 50),))
    clock.advance(seconds=10)
    await eng.tick()                                                   # take profit
    return seed


async def test_public_strategies_without_token_and_without_amounts(session_factory):
    learn._cache.clear()
    seed = await _one_closed_trade(session_factory)
    async with _client(session_factory) as c:
        lst = await c.get("/api/v1/public/strategies")
        one = await c.get("/api/v1/public/strategies/volume-spike")
        missing = await c.get("/api/v1/public/strategies/nope")
    assert lst.status_code == 200 and "max-age=60" in lst.headers["Cache-Control"]
    item = lst.json()["items"][0]
    assert item["id"] == "volume-spike" and item["bot"]["name"] == "paper-kraken-01"
    assert item["bot"]["trades"] == 1
    body = one.json()
    assert one.status_code == 200 and missing.status_code == 404
    assert "risk_policy_id" not in body["config"] and body["config"]["risk_per_trade_pct"] == "0.5"
    assert body["config"]["entry"]["conditions"] and body["config_version"] == "0.2.0"
    assert body["trades"][0]["symbol"] == "TEST/USD" and body["trades"][0]["exit_reason"]
    assert [v["version"] for v in body["versions"]] == ["0.2.0"] and body["versions"][0]["reason"]
    for forbidden in (str(seed.bot.id), "user:", "cost_basis", "realized_pnl", "equity", "capital"):
        assert forbidden not in one.text and forbidden not in lst.text
    assert not re.search(r"(?<![\d.])10000(\.0+)?(?![\d.])", one.text)      # the paper capital


async def test_public_strategy_stats_match_the_stats_screen(session_factory):
    learn._cache.clear()
    seed = await _one_closed_trade(session_factory)
    async with session_factory() as db:
        rows = await trade_rows(db, bot_id=seed.bot.id)
        expected = compute_stats(rows)
        data = await learn.strategy_detail(db, "volume-spike", NOW)
    s = data["stats"]
    assert s["trades"] == expected["trades"] == 1 and s["wins"] == expected["wins"]
    assert s["win_rate_pct"] == "100.00"
    assert s["best_pct"] == s["worst_pct"] == learn._q2(rows[0].net_pct)
    assert float(s["best_pct"]) > 0 and data["trades"][0]["result_pct"] == s["best_pct"]
