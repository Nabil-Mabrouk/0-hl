"""Order book in Decimal with Kraken WS v2 checksum (spec 07-data/03 « Faits vérifiés », ADR-018).

Checksum: CRC32 (unsigned) over the 10 best asks (ascending) then the 10 best
bids (descending); each level = price then qty, formatted with the pair's
`price_precision` / `qty_precision`, decimal point removed, leading zeros
stripped. Verified on 4 613 real messages (100 % match).

ADR-018 (incremental): prices of each side are kept sorted continuously (binary
search insert/remove, no full sort per message) and the checksum text of each
level is computed once, when the level is set, then cached. A precision change
invalidates the cache.
"""

from __future__ import annotations

import zlib
from bisect import bisect_left, insort
from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any

ZERO = Decimal(0)
BPS = Decimal(10000)
CHECKSUM_LEVELS = 10


def _fmt(value: Decimal, precision: int) -> str:
    return format(value, f".{precision}f").replace(".", "").lstrip("0")


class _Side:
    """One side of the book: price → qty, sorted prices (ascending), cached level text."""

    __slots__ = ("levels", "prices", "text")

    def __init__(self) -> None:
        self.levels: dict[Decimal, Decimal] = {}
        self.prices: list[Decimal] = []
        self.text: dict[Decimal, str] = {}

    def clear(self) -> None:
        self.levels.clear()
        self.prices.clear()
        self.text.clear()

    def remove(self, price: Decimal) -> None:
        if self.levels.pop(price, None) is not None:
            i = bisect_left(self.prices, price)
            if i < len(self.prices) and self.prices[i] == price:
                del self.prices[i]
            self.text.pop(price, None)

    def set(self, price: Decimal, qty: Decimal, pp: int, qp: int) -> None:
        if price not in self.levels:
            insort(self.prices, price)
        self.levels[price] = qty
        self.text[price] = _fmt(price, pp) + _fmt(qty, qp)


class OrderBook:
    def __init__(self, symbol: str, *, depth: int, price_precision: int, qty_precision: int):
        self.symbol = symbol
        self.depth = depth
        self._pp = price_precision
        self._qp = qty_precision
        self._bids = _Side()
        self._asks = _Side()
        self.has_snapshot = False

    # -- precision (instrument channel may update it): invalidates the text cache
    @property
    def price_precision(self) -> int:
        return self._pp

    @price_precision.setter
    def price_precision(self, value: int) -> None:
        if value != self._pp:
            self._pp = value
            self._rebuild_text()

    @property
    def qty_precision(self) -> int:
        return self._qp

    @qty_precision.setter
    def qty_precision(self, value: int) -> None:
        if value != self._qp:
            self._qp = value
            self._rebuild_text()

    def _rebuild_text(self) -> None:
        for side in (self._bids, self._asks):
            side.text = {p: _fmt(p, self._pp) + _fmt(q, self._qp) for p, q in side.levels.items()}

    # -- read-only views kept for callers and tests
    @property
    def bids(self) -> dict[Decimal, Decimal]:
        return self._bids.levels

    @property
    def asks(self) -> dict[Decimal, Decimal]:
        return self._asks.levels

    def reset(self) -> None:
        self._bids.clear()
        self._asks.clear()
        self.has_snapshot = False

    def _apply_side(self, side: _Side, levels: Iterable[Mapping[str, Any]]) -> None:
        for level in levels:
            price, qty = level["price"], level["qty"]
            if not isinstance(price, Decimal) or not isinstance(qty, Decimal):
                raise TypeError("book levels must be Decimal (parse with parse_float=Decimal)")
            if qty == ZERO:
                side.remove(price)
            else:
                side.set(price, qty, self._pp, self._qp)

    def apply(self, msg_type: str, bids: Iterable[Mapping], asks: Iterable[Mapping]) -> None:
        if msg_type == "snapshot":
            self._bids.clear()
            self._asks.clear()
            self.has_snapshot = True
        self._apply_side(self._bids, bids)
        self._apply_side(self._asks, asks)
        self._truncate()

    def _truncate(self) -> None:
        while len(self._bids.prices) > self.depth:   # keep the highest bids
            self._bids.remove(self._bids.prices[0])
        while len(self._asks.prices) > self.depth:   # keep the lowest asks
            self._asks.remove(self._asks.prices[-1])

    def top_asks(self, n: int) -> list[tuple[Decimal, Decimal]]:
        lv = self._asks.levels
        return [(p, lv[p]) for p in self._asks.prices[:n]]

    def top_bids(self, n: int) -> list[tuple[Decimal, Decimal]]:
        lv = self._bids.levels
        return [(p, lv[p]) for p in reversed(self._bids.prices[-n:])] if n > 0 else []

    def checksum(self) -> int:
        asks, bids = self._asks, self._bids
        parts = [asks.text[p] for p in asks.prices[:CHECKSUM_LEVELS]]
        parts += [bids.text[p] for p in reversed(bids.prices[-CHECKSUM_LEVELS:])]
        return zlib.crc32("".join(parts).encode("ascii")) & 0xFFFFFFFF

    def verify(self, expected: int) -> bool:
        return self.checksum() == expected

    @property
    def best_bid(self) -> tuple[Decimal, Decimal] | None:
        if not self._bids.prices:
            return None
        p = self._bids.prices[-1]
        return p, self._bids.levels[p]

    @property
    def best_ask(self) -> tuple[Decimal, Decimal] | None:
        if not self._asks.prices:
            return None
        p = self._asks.prices[0]
        return p, self._asks.levels[p]

    def mid(self) -> Decimal | None:
        bid, ask = self.best_bid, self.best_ask
        if bid is None or ask is None:
            return None
        return (bid[0] + ask[0]) / 2

    def spread_bps(self) -> Decimal | None:
        bid, ask = self.best_bid, self.best_ask
        mid = self.mid()
        if bid is None or ask is None or not mid:
            return None
        return (ask[0] - bid[0]) / mid * BPS
