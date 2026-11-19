"""Network-free fakes for Kraken (ADR-012): recorded fixtures, fake WS transport, mocked REST."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from app.domain.trading.market.kraken_rest import KrakenRestClient

FIXTURES = Path(__file__).parent / "fixtures" / "kraken"


def ws_session_lines() -> list[str]:
    return (FIXTURES / "ws_session_btc_eth.jsonl").read_text(encoding="utf-8").splitlines()


def rest_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeTransport:
    """Replays messages; then either hangs (watchdog test) or raises (connection drop)."""

    def __init__(self, messages: list[str], *, then: str = "hang") -> None:
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        for m in messages:
            self.queue.put_nowait(m)
        self.then = then
        self.sent: list[dict] = []
        self.closed = False

    def push(self, message: dict | str) -> None:
        self.queue.put_nowait(message if isinstance(message, str) else json.dumps(message))

    async def send(self, text: str) -> None:
        self.sent.append(json.loads(text))

    async def recv(self) -> str:
        if self.closed or (self.queue.empty() and self.then == "close"):
            raise ConnectionError("fake connection closed")
        item = await self.queue.get()
        if item is None:  # closed while waiting, like a real WebSocket
            raise ConnectionError("fake connection closed")
        return item

    async def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.queue.put_nowait(None)  # type: ignore[arg-type]


class FakeConnector:
    def __init__(self, transports: list[FakeTransport]) -> None:
        self.transports = list(transports)
        self.used: list[FakeTransport] = []

    async def __call__(self) -> FakeTransport:
        if not self.transports:
            raise OSError("no more fake connections")
        t = self.transports.pop(0)
        self.used.append(t)
        return t


def rest_client(*, fail: bool = False) -> KrakenRestClient:
    pairs = rest_fixture("rest_asset_pairs.json")
    ticker = rest_fixture("rest_ticker.json")
    ohlc = rest_fixture("rest_ohlc_btc_usd_1m.json")

    def handler(request: httpx.Request) -> httpx.Response:
        if fail:
            return httpx.Response(503, text="unavailable")
        path = request.url.path
        if path.endswith("/AssetPairs"):
            return httpx.Response(200, json=pairs)
        if path.endswith("/Ticker"):
            return httpx.Response(200, json=ticker)
        if path.endswith("/OHLC"):
            pair = request.url.params["pair"]
            return httpx.Response(200, json={"error": [], "result": {
                pair: ohlc["result"]["BTC/USD"], "last": ohlc["result"]["last"]}})
        return httpx.Response(404)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return KrakenRestClient(http, min_interval=0.0)


async def wait_until(predicate, timeout: float = 5.0, step: float = 0.02) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(step)
