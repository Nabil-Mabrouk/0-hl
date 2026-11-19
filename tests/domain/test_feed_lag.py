"""T-M1.2-14 — ADR-017: feed lag ⇒ STALE FEED_LAG, recovery, voluntary reconnection; light recv loop."""

import asyncio
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.domain.trading.market.service import MarketDataConfig, MarketDataService
from app.domain.trading.market.state import LIVE, STALE
from app.domain.trading.models import DomainEvent
from app.domain.trading.seed import run_seed

from .market_fakes import FakeConnector, FakeTransport, rest_client, wait_until, ws_session_lines

pytestmark = pytest.mark.anyio


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _first_book_time() -> datetime:
    for line in ws_session_lines():
        m = json.loads(line)
        if m.get("channel") == "book" and m.get("type") == "snapshot":
            return datetime.fromisoformat(m["data"][0]["timestamp"].replace("Z", "+00:00"))
    raise AssertionError("no book snapshot in fixture")


def _snapshot_lines() -> list[str]:
    """Fixture prefix up to (and including) both book snapshots — no later updates."""
    out, books = [], 0
    for line in ws_session_lines():
        m = json.loads(line)
        if m.get("channel") == "book" and m.get("type") == "update":
            continue
        out.append(line)
        if m.get("channel") == "book" and m.get("type") == "snapshot":
            books += 1
            if books == 2:
                break
    return out


def _echo_update(svc: MarketDataService, symbol: str, sent: datetime) -> dict:
    """A no-op book update (same best bid) with a valid checksum and a chosen Kraken timestamp."""
    book = svc.state.book(symbol)
    price, qty = book.best_bid
    return {"channel": "book", "type": "update", "data": [{
        "symbol": symbol, "bids": [{"price": float(price), "qty": float(qty)}], "asks": [],
        "checksum": book.checksum(), "timestamp": sent.isoformat().replace("+00:00", "Z")}]}


async def _service(session_factory, transports, clock):
    async with session_factory() as db:
        await run_seed(db)
    cfg = MarketDataConfig(watchdog_seconds=0.5, housekeeping_seconds=0.05, book_depth=25)
    svc = MarketDataService(session_factory, rest_client(), FakeConnector(transports), config=cfg, clock=clock)
    svc.ws.backoff_initial = 0.05
    await svc.refresh_universe()
    return svc


async def test_lagging_book_goes_stale_then_recovers(session_factory):
    clock = Clock(_first_book_time() + timedelta(milliseconds=300))
    transport = FakeTransport(_snapshot_lines())
    svc = await _service(session_factory, [transport], clock)
    stop = asyncio.Event()
    task = asyncio.create_task(svc.run(stop))
    await wait_until(lambda: svc.state.is_fresh("BTC/USD"))

    transport.push(_echo_update(svc, "BTC/USD", clock.now - timedelta(seconds=3)))
    await wait_until(lambda: svc.state.feed("BTC/USD").reason == "FEED_LAG")
    assert svc.state.feed("BTC/USD").state == STALE and svc.state.feed("BTC/USD").lag_ms == 3000
    assert svc.state.is_fresh("ETH/USD")                          # other pairs unaffected

    transport.push(_echo_update(svc, "BTC/USD", clock.now - timedelta(milliseconds=1500)))
    await asyncio.sleep(0.1)
    assert svc.state.feed("BTC/USD").state == STALE               # hysteresis: needs < 1 s
    transport.push(_echo_update(svc, "BTC/USD", clock.now - timedelta(milliseconds=200)))
    await wait_until(lambda: svc.state.feed("BTC/USD").state == LIVE)
    stop.set()
    await task
    async with session_factory() as db:
        stale = (await db.scalars(select(DomainEvent).where(DomainEvent.event_type == "DATA_STALE"))).all()
    assert [e.payload["reason"] for e in stale] == ["FEED_LAG"]


async def test_large_lag_forces_a_reconnection(session_factory):
    clock = Clock(_first_book_time() + timedelta(milliseconds=300))
    first = FakeTransport(_snapshot_lines())
    second = FakeTransport(_snapshot_lines())
    svc = await _service(session_factory, [first, second], clock)
    stop = asyncio.Event()
    task = asyncio.create_task(svc.run(stop))
    await wait_until(lambda: svc.state.is_fresh("BTC/USD"))
    first.push(_echo_update(svc, "BTC/USD", clock.now - timedelta(seconds=20)))
    await wait_until(lambda: svc.ws.connections == 2)
    assert first.closed
    await wait_until(lambda: svc.state.is_fresh("BTC/USD"))
    await wait_until(lambda: not svc._pending_events)  # noqa: SLF001
    stop.set()
    await task
    async with session_factory() as db:
        degraded = (await db.scalars(select(DomainEvent).where(DomainEvent.event_type == "PROVIDER_DEGRADED"))).all()
    assert [e.payload["reason"] for e in degraded] == ["FEED_LAG"]


async def test_recv_loop_counts_messages_and_stops_promptly(session_factory):
    clock = Clock(_first_book_time() + timedelta(milliseconds=300))
    transport = FakeTransport(_snapshot_lines())
    svc = await _service(session_factory, [transport], clock)
    stop = asyncio.Event()
    task = asyncio.create_task(svc.run(stop))
    await wait_until(lambda: svc.state.is_fresh("BTC/USD"))
    messages, busy = svc.ws.take_stats()
    assert messages >= len(_snapshot_lines()) - 1 and busy > 0
    assert svc.ws.take_stats() == (0, 0.0)                       # reset after reading
    loop = asyncio.get_running_loop()
    started = loop.time()
    stop.set()
    await task
    assert loop.time() - started < svc.config.watchdog_seconds   # recv() unblocked by close
    assert transport.closed
