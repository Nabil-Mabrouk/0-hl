"""T-M1.2-02/08/09/10/11 — MarketDataService end to end, network-free."""

import asyncio
import json

import pytest
from sqlalchemy import select

from app.domain.trading.market.service import CONNECTION_FEED_ID, MarketDataConfig, MarketDataService
from app.domain.trading.market.state import LIVE, STALE
from app.domain.trading.models import DomainEvent, FeedStatus, Instrument, TradeMinute
from app.domain.trading.seed import run_seed

from .market_fakes import FakeConnector, FakeTransport, rest_client, wait_until, ws_session_lines

pytestmark = pytest.mark.anyio

FAST = MarketDataConfig(watchdog_seconds=0.3, housekeeping_seconds=0.1, universe_retry_seconds=0.2,
                        backfill=True)


async def _service(session_factory, transports, *, rest_fail=False) -> MarketDataService:
    async with session_factory() as db:
        await run_seed(db)
    svc = MarketDataService(session_factory, rest_client(fail=rest_fail),
                            FakeConnector(transports), config=FAST)
    svc.ws.backoff_initial = 0.05
    return svc


async def _events(session_factory, event_type=None):
    async with session_factory() as db:
        stmt = select(DomainEvent).order_by(DomainEvent.position)
        if event_type:
            stmt = stmt.where(DomainEvent.event_type == event_type)
        return list((await db.scalars(stmt)).all())


async def _run(svc):
    stop = asyncio.Event()
    task = asyncio.create_task(svc.run(stop))
    return stop, task


async def test_universe_persisted_and_books_go_live(session_factory):
    svc = await _service(session_factory, [FakeTransport(ws_session_lines())])
    await svc.refresh_universe()
    assert {"BTC/USD", "ETH/USD"} <= svc.wanted
    stop, task = await _run(svc)
    await wait_until(lambda: svc.state.is_fresh("BTC/USD") and svc.state.is_fresh("ETH/USD"))
    sent = svc.ws._transport and svc.ws._transport.sent  # noqa: SLF001
    channels = {(m["method"], m["params"]["channel"]) for m in sent}
    assert ("subscribe", "book") in channels and ("subscribe", "trade") in channels
    trade_sub = next(m for m in sent if m["params"]["channel"] == "trade")
    assert trade_sub["params"]["snapshot"] is False
    stop.set()
    await task
    async with session_factory() as db:
        inst = await db.get(Instrument, "kraken_spot:BTC/USD")
        assert inst.in_universe and inst.pair_decimals == 1
        usdt = await db.get(Instrument, "kraken_spot:USDT/USD")
        assert usdt is not None and not usdt.in_universe
        backfill = (await db.scalars(select(TradeMinute).where(TradeMinute.source == "backfill"))).all()
        assert backfill  # OHLC backfill written
        conn = await db.get(FeedStatus, CONNECTION_FEED_ID)
        assert conn.state == STALE and conn.reason == "WORKER_STOPPED"
    assert not await _events(session_factory, "DATA_STALE")  # no startup noise


async def test_checksum_mismatch_triggers_stale_and_resync(session_factory):
    lines = ws_session_lines()
    transport = FakeTransport(lines)
    svc = await _service(session_factory, [transport])
    await svc.refresh_universe()
    stop, task = await _run(svc)
    await wait_until(lambda: svc.state.is_fresh("BTC/USD"))
    await wait_until(lambda: transport.queue.empty())
    transport.push({"channel": "book", "type": "update", "data": [{
        "symbol": "BTC/USD", "bids": [{"price": 1.0, "qty": 1.0}], "asks": [],
        "checksum": 1, "timestamp": "2026-11-18T06:15:00.000000Z"}]})
    await wait_until(lambda: svc.state.feed("BTC/USD").state == STALE)
    assert svc.state.feed("BTC/USD").reason == "CHECKSUM_MISMATCH"
    assert svc.state.feed("BTC/USD").resync_count == 1
    resync = [m for m in transport.sent if m["params"].get("symbol") == ["BTC/USD"]]
    assert [m["method"] for m in resync] == ["unsubscribe", "subscribe"]
    assert svc.state.is_fresh("ETH/USD")  # other pairs unaffected
    stop.set()
    await task
    stale = await _events(session_factory, "DATA_STALE")
    assert len(stale) == 1 and stale[0].payload["reason"] == "CHECKSUM_MISMATCH"


async def test_watchdog_reconnects_and_recovers(session_factory):
    lines = ws_session_lines()
    first = FakeTransport(lines[:80], then="hang")   # silence -> watchdog
    second = FakeTransport(lines, then="hang")
    svc = await _service(session_factory, [first, second])
    await svc.refresh_universe()
    stop, task = await _run(svc)
    await wait_until(lambda: svc.state.is_fresh("BTC/USD"))
    await wait_until(lambda: svc.ws.connections == 2, timeout=5)
    await wait_until(lambda: svc.state.is_fresh("BTC/USD") and svc.state.is_fresh("ETH/USD"))
    await wait_until(lambda: not svc._pending_events, timeout=2)  # noqa: SLF001
    stop.set()
    await task
    types = [e.event_type for e in await _events(session_factory)]
    assert "PROVIDER_DEGRADED" in types and "PROVIDER_RECOVERED" in types
    assert "DATA_STALE" in types and "DATA_FRESH" in types
    degraded = await _events(session_factory, "PROVIDER_DEGRADED")
    assert degraded[0].payload["reason"] == "WATCHDOG_TIMEOUT"


async def test_instrument_status_change_is_persisted(session_factory):
    transport = FakeTransport(ws_session_lines())
    svc = await _service(session_factory, [transport])
    await svc.refresh_universe()
    stop, task = await _run(svc)
    await wait_until(lambda: svc.state.is_fresh("BTC/USD"))
    snap = next(m for m in map(json.loads, ws_session_lines())
                if m.get("channel") == "instrument" and m.get("type") == "snapshot")
    pair = next(p for p in snap["data"]["pairs"] if p["symbol"] == "BTC/USD")
    transport.push({"channel": "instrument", "type": "update",
                    "data": {"assets": [], "pairs": [{**pair, "status": "cancel_only"}]}})
    await wait_until(lambda: not svc.state.is_tradable("BTC/USD"))
    stop.set()
    await task
    async with session_factory() as db:
        assert (await db.get(Instrument, "kraken_spot:BTC/USD")).status == "cancel_only"
    ev = await _events(session_factory, "INSTRUMENT_STATUS_CHANGED")
    assert ev and ev[0].payload == {"instrument_id": "kraken_spot:BTC/USD",
                                    "from": "online", "to": "cancel_only"}


async def test_provider_unreachable_never_raises(session_factory):
    """T-M1.2-10: REST 503 and WS connect failures -> degraded, retries, clean stop."""
    svc = await _service(session_factory, [], rest_fail=True)
    stop, task = await _run(svc)
    await asyncio.sleep(0.6)
    assert not task.done()
    stop.set()
    await asyncio.wait_for(task, timeout=3)
    assert task.exception() is None
    degraded = await _events(session_factory, "PROVIDER_DEGRADED")
    assert len(degraded) == 1


async def test_feed_status_rows_match_memory(session_factory):
    svc = await _service(session_factory, [FakeTransport(ws_session_lines())])
    await svc.refresh_universe()
    stop, task = await _run(svc)
    await wait_until(lambda: svc.state.is_fresh("BTC/USD"))
    await svc.persist()
    async with session_factory() as db:
        btc = await db.get(FeedStatus, "kraken_spot:BTC/USD")
        conn = await db.get(FeedStatus, CONNECTION_FEED_ID)
        assert btc.state == LIVE and btc.last_checksum_ok_at is not None
        assert conn.state == LIVE
    stop.set()
    await task
