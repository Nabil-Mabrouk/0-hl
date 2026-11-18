"""Kraken WebSocket v2 client: injectable transport, watchdog, reconnection (spec 07-data/03).

Messages are parsed with `parse_float=Decimal`: no float ever reaches the domain.
No message for `watchdog_seconds` (heartbeats included) ⇒ connection considered
dead ⇒ reconnect with exponential backoff (1, 2, 4… max 60 s + jitter).
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any, Protocol

log = logging.getLogger("hl.market.ws")

WS_URL = "wss://ws.kraken.com/v2"


class WsTransport(Protocol):
    async def send(self, text: str) -> None: ...
    async def recv(self) -> str: ...
    async def close(self) -> None: ...


class WebsocketsTransport:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def send(self, text: str) -> None:
        await self._conn.send(text)

    async def recv(self) -> str:
        data = await self._conn.recv()
        return data if isinstance(data, str) else data.decode("utf-8")

    async def close(self) -> None:
        await self._conn.close()


def websockets_connector(url: str = WS_URL) -> Callable[[], Awaitable[WsTransport]]:
    async def connect() -> WsTransport:
        import websockets

        conn = await websockets.connect(url, max_size=2**24, ping_interval=20, ping_timeout=20)
        return WebsocketsTransport(conn)

    return connect


class WatchdogTimeout(Exception):
    pass


def parse_message(text: str) -> dict[str, Any]:
    return json.loads(text, parse_float=Decimal)


async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass


class KrakenWsClient:
    def __init__(
        self,
        connect: Callable[[], Awaitable[WsTransport]],
        *,
        on_connected: Callable[[], Awaitable[None]],
        on_message: Callable[[dict[str, Any]], Awaitable[None]],
        on_disconnected: Callable[[str], Awaitable[None]],
        watchdog_seconds: float = 5.0,
        backoff_initial: float = 1.0,
        backoff_max: float = 60.0,
        connect_timeout: float = 15.0,
    ) -> None:
        self._connect = connect
        self._on_connected = on_connected
        self._on_message = on_message
        self._on_disconnected = on_disconnected
        self.watchdog_seconds = watchdog_seconds
        self.backoff_initial = backoff_initial
        self.backoff_max = backoff_max
        self.connect_timeout = connect_timeout
        self._transport: WsTransport | None = None
        self.connections = 0

    @property
    def connected(self) -> bool:
        return self._transport is not None

    async def send(self, payload: dict[str, Any]) -> bool:
        if self._transport is None:
            return False
        try:
            await self._transport.send(json.dumps(payload))
            return True
        except Exception as exc:  # noqa: BLE001 — a send failure surfaces via recv/watchdog
            log.warning("kraken ws send failed: %s", exc)
            return False

    async def _recv_loop(self, transport: WsTransport, stop: asyncio.Event) -> None:
        stop_wait = asyncio.ensure_future(stop.wait())
        try:
            while not stop.is_set():
                recv = asyncio.ensure_future(transport.recv())
                done, _ = await asyncio.wait(
                    {recv, stop_wait}, timeout=self.watchdog_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if recv not in done:
                    recv.cancel()
                    if stop_wait in done:
                        return
                    raise WatchdogTimeout()
                text = recv.result()
                try:
                    msg = parse_message(text)
                except ValueError:
                    log.warning("kraken ws: invalid JSON dropped")
                    continue
                try:
                    await self._on_message(msg)
                except Exception:  # noqa: BLE001 — one bad message must not kill the feed
                    log.exception("kraken ws: message handler failed")
        finally:
            stop_wait.cancel()

    async def run(self, stop: asyncio.Event) -> None:
        backoff = self.backoff_initial
        while not stop.is_set():
            try:
                transport = await asyncio.wait_for(self._connect(), timeout=self.connect_timeout)
            except Exception as exc:  # noqa: BLE001
                log.warning("kraken ws connect failed: %s", exc)
                await self._on_disconnected("CONNECT_FAILED")
                await _sleep_or_stop(stop, backoff + random.uniform(0, backoff / 2))
                backoff = min(backoff * 2, self.backoff_max)
                continue

            self._transport = transport
            self.connections += 1
            reason = "CONNECTION_LOST"
            try:
                await self._on_connected()
                backoff = self.backoff_initial
                await self._recv_loop(transport, stop)
            except WatchdogTimeout:
                reason = "WATCHDOG_TIMEOUT"
            except Exception as exc:  # noqa: BLE001
                log.warning("kraken ws connection lost: %s", exc)
            finally:
                self._transport = None
                try:
                    await transport.close()
                except Exception:  # noqa: BLE001
                    pass
            if stop.is_set():
                return
            await self._on_disconnected(reason)
            await _sleep_or_stop(stop, backoff + random.uniform(0, backoff / 2))
            backoff = min(backoff * 2, self.backoff_max)
