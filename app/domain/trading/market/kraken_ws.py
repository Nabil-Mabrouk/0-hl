"""Kraken WebSocket v2 client: injectable transport, watchdog, reconnection (spec 07-data/03).

Messages are parsed with `parse_float=Decimal`: no float ever reaches the domain.
No message for `watchdog_seconds` (heartbeats included) ⇒ connection considered
dead ⇒ reconnect with exponential backoff (1, 2, 4… max 60 s + jitter).
ADR-017: the receive loop creates no task per message (a timeout on recv() is the
watchdog), counts messages and processing time, and supports a voluntary
reconnection (`request_reconnect`) used when the feed lags behind.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
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
        self._reconnect_reason: str | None = None
        self.messages = 0
        self.processing_seconds = 0.0

    def take_stats(self) -> tuple[int, float]:
        """Messages received and processing time since the last call (then reset)."""
        stats = (self.messages, self.processing_seconds)
        self.messages, self.processing_seconds = 0, 0.0
        return stats

    async def request_reconnect(self, reason: str) -> None:
        """Close the current connection on purpose; `run()` reconnects with `reason`."""
        if self._transport is None or self._reconnect_reason is not None:
            return
        self._reconnect_reason = reason
        try:
            await self._transport.close()
        except Exception:  # noqa: BLE001
            pass

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
        # stop ⇒ close the transport, which unblocks recv(); no task per message (ADR-017)
        async def close_on_stop() -> None:
            await stop.wait()
            try:
                await transport.close()
            except Exception:  # noqa: BLE001
                pass

        closer = asyncio.create_task(close_on_stop())
        try:
            while not stop.is_set():
                try:
                    async with asyncio.timeout(self.watchdog_seconds):
                        text = await transport.recv()
                except TimeoutError:
                    if stop.is_set():
                        return
                    raise WatchdogTimeout() from None
                started = time.perf_counter()
                try:
                    msg = parse_message(text)
                except ValueError:
                    log.warning("kraken ws: invalid JSON dropped")
                    continue
                try:
                    await self._on_message(msg)
                except Exception:  # noqa: BLE001 — one bad message must not kill the feed
                    log.exception("kraken ws: message handler failed")
                self.messages += 1
                self.processing_seconds += time.perf_counter() - started
        except Exception:
            if stop.is_set():
                return
            raise
        finally:
            closer.cancel()

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
            self._reconnect_reason = None
            self.connections += 1
            reason = "CONNECTION_LOST"
            try:
                await self._on_connected()
                backoff = self.backoff_initial
                await self._recv_loop(transport, stop)
            except WatchdogTimeout:
                reason = "WATCHDOG_TIMEOUT"
            except Exception as exc:  # noqa: BLE001
                if self._reconnect_reason is None:
                    log.warning("kraken ws connection lost: %s", exc)
            finally:
                if self._reconnect_reason is not None:
                    reason = self._reconnect_reason
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
