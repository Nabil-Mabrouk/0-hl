"""In-memory market state shared with the agents (spec 07-data/03 « État du flux »)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from app.domain.trading.market.book import OrderBook

LIVE = "LIVE"
STALE = "STALE"
UNKNOWN = "UNKNOWN"


@dataclass
class InstrumentRules:
    symbol: str
    instrument_id: str
    status: str
    price_precision: int
    qty_precision: int
    price_increment: Decimal | None = None
    qty_min: Decimal | None = None
    cost_min: Decimal | None = None


@dataclass
class FeedInfo:
    state: str = UNKNOWN
    reason: str | None = "NOT_SUBSCRIBED"
    last_book_change_at: datetime | None = None
    last_trade_at: datetime | None = None
    last_checksum_ok_at: datetime | None = None
    resync_count: int = 0


@dataclass
class MarketState:
    rules: dict[str, InstrumentRules] = field(default_factory=dict)
    books: dict[str, OrderBook] = field(default_factory=dict)
    feeds: dict[str, FeedInfo] = field(default_factory=dict)
    connection_alive: bool = False
    connection_reason: str | None = "NOT_CONNECTED"
    last_message_at: datetime | None = None
    system_status: str | None = None

    def feed(self, symbol: str) -> FeedInfo:
        return self.feeds.setdefault(symbol, FeedInfo())

    def is_fresh(self, symbol: str) -> bool:
        """Fresh = live connection, snapshot received, last checksum valid (07-data/03)."""
        info = self.feeds.get(symbol)
        return bool(self.connection_alive and info and info.state == LIVE)

    def is_tradable(self, symbol: str) -> bool:
        rules = self.rules.get(symbol)
        return bool(rules and rules.status == "online")

    def book(self, symbol: str) -> OrderBook | None:
        return self.books.get(symbol)
