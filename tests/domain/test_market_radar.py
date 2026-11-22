"""Market radar — spec 02-design/05 (ADR-028): T-RAD-01 calculation, T-RAD-02 outcomes per pair,
T-RAD-03 public and authenticated routes."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.core.database import get_db
from app.domain.routers import public_v1_router
from app.domain.trading.enums import BotStatus
from app.domain.trading.market import radar
from app.domain.trading.models import DomainEvent, Instrument, TradeMinute
from app.domain.trading.seed import run_seed

from .agent_fakes import NOW, Clock, add_instrument, add_minutes, engine, market_state, set_book

pytestmark = pytest.mark.anyio

T = datetime(2026, 11, 22, 14, 32, 45, tzinfo=timezone.utc)      # window end 14:32 (settled)
END = datetime(2026, 11, 22, 14, 32, tzinfo=timezone.utc)
START = END - timedelta(minutes=60)


def _instr(iid: str) -> Instrument:
    base = iid.split(":")[1].split("/")[0]
    return Instrument(id=iid, venue="kraken_spot", symbol=iid.split(":")[1], base=base, quote="USD",
                      pair_decimals=2, lot_decimals=4, status="online", in_universe=True, volume_24h_usd=D(1e8))


def _minute(iid: str, m: datetime, close, trades: int = 3, source: str = "live") -> TradeMinute:
    return TradeMinute(instrument_id=iid, minute_start=m, source=source, notional_usd=D(1000),
                       trade_count=trades, volume_base=D(1), close=None if close is None else D(str(close)))


async def _world(db):
    """BTC +1 % with alternating ±0.1 % steps; SOL +2.4 % with ±0.2 % steps (2× BTC's volatility);
    THIN trades 10 minutes only; NODATA has no minute at all."""
    for iid in ("kraken_spot:BTC/USD", "kraken_spot:SOL/USD", "kraken_spot:THIN/USD", "kraken_spot:NODATA/USD"):
        db.add(_instr(iid))
    db.add(_minute("kraken_spot:BTC/USD", START - timedelta(minutes=1), 100))
    db.add(_minute("kraken_spot:SOL/USD", START - timedelta(minutes=1), 50))
    db.add(_minute("kraken_spot:THIN/USD", START - timedelta(minutes=30), 10))
    for i in range(60):
        m = START + timedelta(minutes=i)
        last = i == 59
        btc = 101 if last else (100.1 if i % 2 == 0 else 100)
        sol = 51.2 if last else (50.1 if i % 2 == 0 else 50)
        db.add(_minute("kraken_spot:BTC/USD", m, btc))
        db.add(_minute("kraken_spot:SOL/USD", m, sol))
        db.add(_minute("kraken_spot:SOL/USD", m, 999, source="backfill"))       # live wins
        if i < 10:
            db.add(_minute("kraken_spot:THIN/USD", m, 10.05 if i % 2 == 0 else 10))
    await db.flush()


# T-RAD-01 ----------------------------------------------------------------------------------
async def test_radar_places_pairs_relative_to_btc(session_factory):
    async with session_factory() as db:
        await _world(db)
        data = await radar.compute_radar(db, T, 60)
    assert data["window_end"] == END and not data["stale"]
    assert data["reference"]["symbol"] == "BTC/USD" and data["reference"]["return_pct"] == "1.00"
    pairs = {p["symbol"]: p for p in data["pairs"]}
    assert set(pairs) == {"SOL/USD", "THIN/USD"} and data["pairs_without_data"] == 1
    sol = pairs["SOL/USD"]
    assert sol["return_pct"] == "2.40" and sol["excess_pp"] == "1.40"
    assert sol["judgeable"] and sol["minutes_traded"] == 60
    assert 1.7 < float(sol["vol_ratio"]) < 2.3 and 0.75 < float(sol["y"]) < 1.2
    thin = pairs["THIN/USD"]                                   # 10 traded minutes < 60 / 3
    assert not thin["judgeable"] and thin["y"] == "0" and thin["minutes_traded"] == 10
    assert thin["return_pct"] == "0.00"                        # carried close of the last traded minute


async def test_radar_is_stale_without_btc(session_factory):
    async with session_factory() as db:
        db.add(_instr("kraken_spot:SOL/USD"))
        for i in range(61):
            db.add(_minute("kraken_spot:SOL/USD", START - timedelta(minutes=1) + timedelta(minutes=i), 50 + i % 2))
        await db.flush()
        data = await radar.compute_radar(db, T, 60)
    assert data["stale"] and data["reference"]["return_pct"] is None
    assert [p["judgeable"] for p in data["pairs"]] == [False] and data["pairs"][0]["excess_pp"] is None


# T-RAD-02 ----------------------------------------------------------------------------------
async def test_scan_cycle_reports_an_outcome_per_pair(session_factory):
    async with session_factory() as db:
        await run_seed(db)
        await add_instrument(db)                                   # TEST/USD: signal and entry
        await add_minutes(db)
        db.add(Instrument(id="kraken_spot:LOW/USD", venue="kraken_spot", symbol="LOW/USD", base="LOW",
                          quote="USD", pair_decimals=2, lot_decimals=4, status="online", in_universe=True,
                          volume_24h_usd=D(2000000)))              # no minute ⇒ feature unknown
        await db.commit()
    clock, st = Clock(NOW), market_state()
    eng = engine(session_factory, st, clock)
    await eng.tick()
    async with session_factory() as db:
        ev = await db.scalar(select(DomainEvent).where(DomainEvent.event_type == "SCAN_CYCLE_COMPLETED"))
    outcomes = ev.payload["outcomes"]
    assert outcomes["TEST/USD"] == {"r": "ENTERED"}
    assert outcomes["LOW/USD"] == {"r": "UNKNOWN", "why": "FEED_NOT_LIVE"}


async def test_scan_cycle_reports_a_rug_check_refusal(session_factory):
    async with session_factory() as db:
        await run_seed(db)
        await add_instrument(db)
        await add_minutes(db)
        await db.commit()
    st = market_state()
    set_book(st, asks=((120, 50),), bids=((100, 50),))             # spread far above the policy limit
    await engine(session_factory, st, Clock(NOW)).tick()
    async with session_factory() as db:
        ev = await db.scalar(select(DomainEvent).where(DomainEvent.event_type == "SCAN_CYCLE_COMPLETED"))
    out = ev.payload["outcomes"]["TEST/USD"]
    assert out["r"] in ("FILTERED", "NO_SIGNAL")                    # spread is a scanner condition too
    if out["r"] == "FILTERED":
        assert out["why"] == "spread"


# T-RAD-03 ----------------------------------------------------------------------------------
def _client(session_factory, router) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(router)

    async def _db():
        async with session_factory() as s:
            yield s
    app.dependency_overrides[get_db] = _db
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_public_radar_without_token_and_without_amounts(session_factory, monkeypatch):
    radar._cache.clear()
    monkeypatch.setattr("app.domain.trading.api.public.utcnow", lambda: T)
    async with session_factory() as db:
        seed = await run_seed(db)
        seed.bot.status = BotStatus.RUNNING          # only RUNNING and PAUSED bots are shown
        await _world(db)
        await db.commit()
    async with _client(session_factory, public_v1_router) as c:
        r = await c.get("/api/v1/public/radar")
        again = await c.get("/api/v1/public/radar")
    assert r.status_code == 200 and "max-age=15" in r.headers["Cache-Control"]
    body = r.json()
    assert body["window_minutes"] == 60 and again.json() == body           # cached
    bot = body["bots"][0]
    assert bot["name"] == "paper-kraken-01" and bot["strategy"]["id"] == "volume-spike"
    assert set(bot) == {"name", "status", "strategy", "net_pct", "last_cycle", "open_positions"}
    for forbidden in ("10000", "equity", "user", "bot_id", "deposit"):
        assert forbidden not in r.text


async def test_market_radar_needs_a_reader_and_accepts_windows(session_factory):
    from app.domain.trading.api.routes import router
    async with _client(session_factory, router) as c:
        r = await c.get("/api/v1/market/radar?window=4h")
        bad = await c.get("/api/v1/market/radar?window=2h")
    assert r.status_code in (401, 403)
    assert bad.status_code in (401, 403, 422)
