"""MarketDataService — Kraken Spot feed orchestration (spec 07-data/03).

Runs inside the chassis worker (ADR-007): REST universe refresh, one WS v2
connection, in-memory books/feed state, minute aggregates, periodic
persistence (feed status every 5 s) and transition-only events. Provider
failures never escape `run()`; they become STALE states, events and retries.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.trading.config_schemas import validate_strategy_config
from app.domain.trading.enums import BotStatus, PositionStatus
from app.domain.trading.errors import ProviderUnavailable
from app.domain.trading.events import append_event
from app.domain.trading.market.aggregates import MinuteAggregator, MinuteRow, backfill_rows
from app.domain.trading.market.book import OrderBook
from app.domain.trading.market.kraken_rest import KrakenRestClient
from app.domain.trading.market.kraken_ws import KrakenWsClient, WsTransport
from app.domain.trading.market.state import LIVE, STALE, InstrumentRules, MarketState
from app.domain.trading.market.universe import (
    VENUE,
    PairCandidate,
    instrument_id,
    select_universe,
)
from app.domain.trading.models import (
    Bot,
    FeedStatus,
    Instrument,
    Position,
    StrategyVersion,
    TradeMinute,
)
from app.domain.trading.types import utcnow

log = logging.getLogger("hl.market")

CONNECTION_FEED_ID = f"{VENUE}:__connection__"
ACTIVE_BOT_STATUSES = (BotStatus.READY, BotStatus.RUNNING, BotStatus.PAUSED, BotStatus.DEGRADED)
SUPERVISED_POSITION_STATUSES = (
    PositionStatus.PENDING, PositionStatus.OPEN, PositionStatus.REDUCING,
    PositionStatus.RECONCILIATION_REQUIRED,
)


@dataclass
class MarketDataConfig:
    book_depth: int = 25
    watchdog_seconds: float = 5.0
    housekeeping_seconds: float = 5.0
    universe_retry_seconds: float = 60.0
    default_universe_refresh_seconds: int = 3600
    retention_days: int = 7
    backfill: bool = True
    shutdown_grace_seconds: float = 20.0


@dataclass(frozen=True)
class UniverseParams:
    top_n: int
    min_volume_24h_usd: Decimal | None
    refresh_seconds: int


class MarketDataService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        rest: KrakenRestClient,
        connect: Callable[[], Awaitable[WsTransport]],
        *,
        config: MarketDataConfig | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._sessions = session_factory
        self._rest = rest
        self.config = config or MarketDataConfig()
        self._now = clock
        self.state = MarketState()
        self.aggregator = MinuteAggregator()
        self.wanted: set[str] = set()
        self._pending_events: list[dict[str, Any]] = []
        self._pending_status: dict[str, str] = {}
        self._provider_degraded = False
        self._last_purge: datetime | None = None
        self._backfilled: set[str] = set()
        self.ws = KrakenWsClient(
            connect,
            on_connected=self._on_connected,
            on_message=self.handle_message,
            on_disconnected=self._on_disconnected,
            watchdog_seconds=self.config.watchdog_seconds,
        )

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #
    async def run(self, stop: asyncio.Event) -> None:
        tasks = [
            asyncio.create_task(self.ws.run(stop), name="hl-kraken-ws"),
            asyncio.create_task(self._housekeeping_loop(stop), name="hl-market-housekeeping"),
            asyncio.create_task(self._universe_loop(stop), name="hl-market-universe"),
        ]
        try:
            await stop.wait()
        finally:
            # Cooperative shutdown: every loop watches `stop` and ends its current
            # step (never cancel a task in the middle of a DB write); cancel only
            # what is still running after the grace delay.
            _, pending = await asyncio.wait(tasks, timeout=self.config.shutdown_grace_seconds)
            for t in pending:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.state.connection_alive = False
            self.state.connection_reason = "WORKER_STOPPED"
            self.aggregator.coverage_stopped_all(self._now())
            await self.persist()

    # ------------------------------------------------------------------ #
    # events / transitions (queued in memory, written by housekeeping)
    # ------------------------------------------------------------------ #
    def _event(self, event_type: str, aggregate_id: str, payload: dict[str, Any]) -> None:
        self._pending_events.append(
            dict(event_type=event_type, aggregate_type="market_feed", aggregate_id=aggregate_id,
                 payload=payload, source="worker:market", occurred_at=self._now())
        )

    def _set_feed(self, symbol: str, state: str, reason: str | None) -> None:
        info = self.state.feed(symbol)
        previous = info.state
        info.state, info.reason = state, reason
        if previous == state:
            return
        # Transition-only events: a pair that was never LIVE (UNKNOWN, awaiting its
        # first snapshot) does not produce DATA_STALE noise at startup.
        iid = instrument_id(symbol)
        if state == STALE and previous == LIVE:
            self._event("DATA_STALE", iid, {"instrument_id": iid, "reason": reason})
        elif state == LIVE and previous == STALE:
            self._event("DATA_FRESH", iid, {"instrument_id": iid})

    # ------------------------------------------------------------------ #
    # websocket callbacks
    # ------------------------------------------------------------------ #
    async def _on_connected(self) -> None:
        self.state.connection_alive = True
        self.state.connection_reason = None
        self.state.last_message_at = self._now()
        if self._provider_degraded:
            self._provider_degraded = False
            self._event("PROVIDER_RECOVERED", CONNECTION_FEED_ID, {"venue": VENUE})
        await self.ws.send({"method": "subscribe", "params": {"channel": "instrument"}})
        await self._subscribe(sorted(self.wanted))

    async def _on_disconnected(self, reason: str) -> None:
        now = self._now()
        self.state.connection_alive = False
        self.state.connection_reason = reason
        self.aggregator.coverage_stopped_all(now)
        for symbol, book in self.state.books.items():
            book.reset()
        for symbol in self.wanted:
            self._set_feed(symbol, STALE, reason)
        if not self._provider_degraded:
            self._provider_degraded = True
            self._event("PROVIDER_DEGRADED", CONNECTION_FEED_ID, {"venue": VENUE, "reason": reason})

    async def _subscribe(self, symbols: list[str]) -> None:
        if not symbols or not self.ws.connected:
            return
        await self.ws.send({"method": "subscribe", "params": {
            "channel": "book", "symbol": symbols, "depth": self.config.book_depth}})
        await self.ws.send({"method": "subscribe", "params": {
            "channel": "trade", "symbol": symbols, "snapshot": False}})

    async def _unsubscribe(self, symbols: list[str]) -> None:
        now = self._now()
        for s in symbols:
            self.aggregator.coverage_stopped(s, now)
            self.state.books.pop(s, None)
            self.state.feeds.pop(s, None)
        if not symbols or not self.ws.connected:
            return
        await self.ws.send({"method": "unsubscribe", "params": {
            "channel": "book", "symbol": symbols, "depth": self.config.book_depth}})
        await self.ws.send({"method": "unsubscribe", "params": {"channel": "trade", "symbol": symbols}})

    async def _resync_book(self, symbol: str) -> None:
        self.state.feed(symbol).resync_count += 1
        if not self.ws.connected:
            return
        params = {"channel": "book", "symbol": [symbol], "depth": self.config.book_depth}
        await self.ws.send({"method": "unsubscribe", "params": params})
        await self.ws.send({"method": "subscribe", "params": params})

    async def handle_message(self, msg: dict[str, Any]) -> None:
        now = self._now()
        self.state.last_message_at = now
        if "method" in msg:
            self._handle_ack(msg, now)
            return
        channel = msg.get("channel")
        if channel == "book":
            await self._handle_book(msg, now)
        elif channel == "trade":
            self._handle_trades(msg, now)
        elif channel == "instrument":
            self._handle_instrument(msg)
        elif channel == "status":
            self._handle_status(msg)

    def _handle_ack(self, msg: dict[str, Any], now: datetime) -> None:
        result = msg.get("result") or {}
        symbol = result.get("symbol")
        channel = result.get("channel")
        if not msg.get("success"):
            log.warning("kraken ws %s failed: %s", msg.get("method"), msg.get("error"))
            if symbol and channel == "book":
                self._set_feed(symbol, STALE, "SUBSCRIBE_FAILED")
            return
        if channel == "trade" and symbol:
            if msg.get("method") == "subscribe" and symbol in self.wanted:
                self.aggregator.coverage_started(symbol, instrument_id(symbol), now)
            elif msg.get("method") == "unsubscribe":
                self.aggregator.coverage_stopped(symbol, now)

    def _handle_status(self, msg: dict[str, Any]) -> None:
        data = msg.get("data") or [{}]
        system = data[0].get("system")
        self.state.system_status = system
        if system and system != "online":
            for symbol in self.wanted:
                self._set_feed(symbol, STALE, "VENUE_MAINTENANCE")

    def _handle_instrument(self, msg: dict[str, Any]) -> None:
        data = msg.get("data") or {}
        for pair in data.get("pairs", []):
            symbol = pair.get("symbol")
            if symbol not in self.wanted and symbol not in self.state.rules:
                continue
            rules = self.state.rules.get(symbol)
            status = str(pair.get("status"))
            if rules is None:
                rules = InstrumentRules(
                    symbol=symbol, instrument_id=instrument_id(symbol), status=status,
                    price_precision=int(pair["price_precision"]),
                    qty_precision=int(pair["qty_precision"]),
                )
                self.state.rules[symbol] = rules
            elif rules.status != status:
                iid = rules.instrument_id
                self._event("INSTRUMENT_STATUS_CHANGED", iid,
                            {"instrument_id": iid, "from": rules.status, "to": status})
                self._pending_status[symbol] = status
            rules.status = status
            rules.price_precision = int(pair["price_precision"])
            rules.qty_precision = int(pair["qty_precision"])
            rules.price_increment = pair.get("price_increment", rules.price_increment)
            rules.qty_min = pair.get("qty_min", rules.qty_min)
            rules.cost_min = pair.get("cost_min", rules.cost_min)
            book = self.state.books.get(symbol)
            if book is not None:
                book.price_precision = rules.price_precision
                book.qty_precision = rules.qty_precision

    async def _handle_book(self, msg: dict[str, Any], now: datetime) -> None:
        msg_type = msg.get("type")
        for item in msg.get("data", []):
            symbol = item.get("symbol")
            if symbol not in self.wanted:
                continue
            rules = self.state.rules.get(symbol)
            if rules is None:
                self._set_feed(symbol, STALE, "NO_INSTRUMENT_RULES")
                continue
            book = self.state.books.get(symbol)
            if book is None:
                book = OrderBook(symbol, depth=self.config.book_depth,
                                 price_precision=rules.price_precision,
                                 qty_precision=rules.qty_precision)
                self.state.books[symbol] = book
            if msg_type == "update" and not book.has_snapshot:
                continue  # wait for the snapshot after a (re)subscription
            book.apply(msg_type, item.get("bids", []), item.get("asks", []))
            info = self.state.feed(symbol)
            info.last_book_change_at = now
            if book.verify(int(item["checksum"])):
                info.last_checksum_ok_at = now
                if self.state.connection_alive and self.state.system_status in (None, "online"):
                    self._set_feed(symbol, LIVE, None)
            else:
                self._set_feed(symbol, STALE, "CHECKSUM_MISMATCH")
                book.reset()
                await self._resync_book(symbol)

    def _handle_trades(self, msg: dict[str, Any], now: datetime) -> None:
        for t in msg.get("data", []):
            symbol = t.get("symbol")
            if symbol not in self.wanted:
                continue
            ts = datetime.fromisoformat(str(t["timestamp"]).replace("Z", "+00:00"))
            if self.aggregator.on_trade(
                symbol, instrument_id(symbol), trade_id=int(t["trade_id"]), price=t["price"],
                qty=t["qty"], side=str(t["side"]), timestamp=ts,
            ):
                self.state.feed(symbol).last_trade_at = ts

    # ------------------------------------------------------------------ #
    # universe
    # ------------------------------------------------------------------ #
    async def _universe_params(self, db: AsyncSession) -> list[UniverseParams]:
        rows = await db.execute(
            select(StrategyVersion.config, StrategyVersion.status)
            .join(Bot, Bot.strategy_version_id == StrategyVersion.id)
            .where(Bot.status.in_(ACTIVE_BOT_STATUSES))
        )
        params: list[UniverseParams] = []
        for config, status in rows:
            parsed = validate_strategy_config({**config, "status": status.value})
            u = parsed.universe
            if "kraken_spot" not in u.venues:
                continue
            params.append(UniverseParams(
                top_n=u.top_n_by_volume or 40,
                min_volume_24h_usd=Decimal(u.min_volume_24h_usd) if u.min_volume_24h_usd else None,
                refresh_seconds=u.refresh_seconds or self.config.default_universe_refresh_seconds,
            ))
        return params

    async def _supervised_symbols(self, db: AsyncSession) -> set[str]:
        rows = await db.scalars(
            select(Position.instrument_id).where(Position.status.in_(SUPERVISED_POSITION_STATUSES))
        )
        return {iid.split(":", 1)[1] for iid in rows.all() if iid.startswith(f"{VENUE}:")}

    async def refresh_universe(self, stop: asyncio.Event | None = None) -> float:
        """Recompute the universe; returns the delay before the next refresh."""
        async with self._sessions() as db:
            params = await self._universe_params(db)
            supervised = await self._supervised_symbols(db)
        selected: set[str] = set()
        candidates: list[PairCandidate] = []
        refresh = float(self.config.default_universe_refresh_seconds)
        if params:
            pairs = await self._rest.asset_pairs()
            tickers = await self._rest.ticker()
            for p in params:
                sel = select_universe(pairs, tickers, top_n=p.top_n,
                                      min_volume_24h_usd=p.min_volume_24h_usd)
                candidates = sel.candidates
                selected |= set(sel.selected)
                refresh = min(refresh, float(p.refresh_seconds))
        await self._persist_universe(candidates, selected)
        new_wanted = selected | supervised
        added = sorted(new_wanted - self.wanted)
        removed = sorted(self.wanted - new_wanted)
        for c in candidates:
            if c.symbol in new_wanted and c.symbol not in self.state.rules:
                self.state.rules[c.symbol] = InstrumentRules(
                    symbol=c.symbol, instrument_id=c.instrument_id, status=c.status,
                    price_precision=c.price_precision, qty_precision=c.qty_precision,
                    price_increment=c.price_increment, qty_min=c.qty_min, cost_min=c.cost_min,
                )
        self.wanted = new_wanted
        for s in added:
            info = self.state.feed(s)
            info.state, info.reason = "UNKNOWN", "AWAITING_SNAPSHOT"
        if added or removed:
            self._event("UNIVERSE_UPDATED", f"{VENUE}:universe",
                        {"added": added, "removed": removed, "size": len(new_wanted)})
        await self._unsubscribe(removed)
        await self._subscribe(added)
        if self.config.backfill:
            await self._backfill(added, stop)
        return refresh

    async def _persist_universe(self, candidates: list[PairCandidate], selected: set[str]) -> None:
        if not candidates:
            return
        now = self._now()
        async with self._sessions() as db:
            existing = {
                i.id: i for i in (await db.scalars(
                    select(Instrument).where(Instrument.venue == VENUE))).all()
            }
            for c in candidates:
                inst = existing.get(c.instrument_id)
                if inst is None:
                    inst = Instrument(id=c.instrument_id, venue=VENUE, symbol=c.symbol)
                    db.add(inst)
                inst.rest_name = c.symbol
                inst.altname = c.altname
                inst.base, inst.quote = c.base, c.quote
                inst.pair_decimals, inst.lot_decimals = c.price_precision, c.qty_precision
                inst.tick_size, inst.ordermin, inst.costmin = c.price_increment, c.qty_min, c.cost_min
                inst.status = self.state.rules[c.symbol].status if c.symbol in self.state.rules else c.status
                inst.in_universe = c.symbol in selected
                inst.volume_24h_usd = c.volume_24h_usd
                inst.updated_at = now
            await db.commit()

    async def _backfill(self, symbols: list[str], stop: asyncio.Event | None = None) -> None:
        for symbol in symbols:
            if stop is not None and stop.is_set():
                return  # remaining pairs are backfilled at the next start
            if symbol in self._backfilled:
                continue
            try:
                entries = await self._rest.ohlc(symbol, interval=1)
            except ProviderUnavailable as exc:
                log.warning("backfill %s failed: %s", symbol, exc)
                continue
            before = self.aggregator.first_live_minute(symbol) or self._now()
            await self._write_minutes(backfill_rows(instrument_id(symbol), entries, before=before))
            self._backfilled.add(symbol)

    async def _universe_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                delay = await self.refresh_universe(stop)
            except ProviderUnavailable as exc:
                log.warning("universe refresh failed: %s", exc)
                if not self._provider_degraded:
                    self._provider_degraded = True
                    self._event("PROVIDER_DEGRADED", CONNECTION_FEED_ID,
                                {"venue": VENUE, "reason": "REST_UNAVAILABLE"})
                delay = self.config.universe_retry_seconds
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    # ------------------------------------------------------------------ #
    # persistence
    # ------------------------------------------------------------------ #
    async def _write_minutes(self, rows: list[MinuteRow]) -> int:
        if not rows:
            return 0
        written = 0
        async with self._sessions() as db:
            by_instr: dict[tuple[str, str], list[MinuteRow]] = {}
            for r in rows:
                by_instr.setdefault((r.instrument_id, r.source), []).append(r)
            for (iid, source), group in by_instr.items():
                # backfill never overwrites (nor duplicates) a live minute
                blocking = {"live", "backfill"} if source == "backfill" else {"live"}
                existing = set((await db.scalars(
                    select(TradeMinute.minute_start).where(
                        TradeMinute.instrument_id == iid,
                        TradeMinute.source.in_(blocking),
                        TradeMinute.minute_start >= min(r.minute_start for r in group),
                        TradeMinute.minute_start <= max(r.minute_start for r in group),
                    ))).all())
                for r in group:
                    if r.minute_start in existing:
                        continue
                    db.add(TradeMinute(**r.__dict__))
                    written += 1
            await db.commit()
        return written

    async def persist(self) -> None:
        """Flush minutes, queued events/status changes and feed status (one pass)."""
        now = self._now()
        await self._write_minutes(self.aggregator.flush(now))
        events, self._pending_events = self._pending_events, []
        statuses, self._pending_status = self._pending_status, {}
        async with self._sessions() as db:
            for ev in events:
                await append_event(db, **ev)
            for symbol, status in statuses.items():
                inst = await db.get(Instrument, instrument_id(symbol))
                if inst is not None:
                    inst.status = status
                    inst.updated_at = now
            await self._persist_feed_status(db, now)
            if self._last_purge is None or now - self._last_purge > timedelta(days=1):
                await db.execute(delete(TradeMinute).where(
                    TradeMinute.minute_start < now - timedelta(days=self.config.retention_days)))
                self._last_purge = now
            await db.commit()

    async def _persist_feed_status(self, db: AsyncSession, now: datetime) -> None:
        rows: dict[str, dict[str, Any]] = {
            CONNECTION_FEED_ID: dict(
                state=LIVE if self.state.connection_alive else STALE,
                reason=self.state.connection_reason,
                last_message_at=self.state.last_message_at,
            )
        }
        for symbol in self.wanted:
            info = self.state.feed(symbol)
            alive = self.state.connection_alive
            rows[instrument_id(symbol)] = dict(
                state=info.state if alive else STALE,
                reason=info.reason if alive else self.state.connection_reason,
                last_message_at=self.state.last_message_at,
                last_book_change_at=info.last_book_change_at, last_trade_at=info.last_trade_at,
                last_checksum_ok_at=info.last_checksum_ok_at, resync_count=info.resync_count,
            )
        existing = {f.instrument_id: f for f in (await db.scalars(select(FeedStatus))).all()}
        for iid, values in rows.items():
            row = existing.get(iid)
            if row is None:
                row = FeedStatus(instrument_id=iid, resync_count=0)
                db.add(row)
            for key, value in values.items():
                setattr(row, key, value)
            row.updated_at = now
        for iid, row in existing.items():
            if iid not in rows and row.state != "UNKNOWN":
                row.state, row.reason, row.updated_at = "UNKNOWN", "NOT_SUBSCRIBED", now

    async def _housekeeping_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.config.housekeeping_seconds)
            except asyncio.TimeoutError:
                pass
            if stop.is_set():
                return
            try:
                await self.persist()
            except Exception:  # noqa: BLE001 — DB hiccup: retry at next tick
                log.exception("market housekeeping failed")
