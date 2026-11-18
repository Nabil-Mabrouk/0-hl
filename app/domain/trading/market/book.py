"""Order book in Decimal with Kraken WS v2 checksum (spec 07-data/03 « Faits vérifiés »).

Checksum: CRC32 (unsigned) over the 10 best asks (ascending) then the 10 best
bids (descending); each level = price then qty, formatted with the pair's
`price_precision` / `qty_precision`, decimal point removed, leading zeros
stripped. Verified on 4 613 real messages (100 % match).
"""

from __future__ import annotations

import zlib
from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any

ZERO = Decimal(0)
BPS = Decimal(10000)


def _fmt(value: Decimal, precision: int) -> str:
    text = format(value, f".{precision}f").replace(".", "").lstrip("0")
    return text


class OrderBook:
    def __init__(self, symbol: str, *, depth: int, price_precision: int, qty_precision: int):
        self.symbol = symbol
        self.depth = depth
        self.price_precision = price_precision
        self.qty_precision = qty_precision
        self.bids: dict[Decimal, Decimal] = {}
        self.asks: dict[Decimal, Decimal] = {}
        self.has_snapshot = False

    def reset(self) -> None:
        self.bids.clear()
        self.asks.clear()
        self.has_snapshot = False

    @staticmethod
    def _apply_side(side: dict[Decimal, Decimal], levels: Iterable[Mapping[str, Any]]) -> None:
        for level in levels:
            price, qty = level["price"], level["qty"]
            if not isinstance(price, Decimal) or not isinstance(qty, Decimal):
                raise TypeError("book levels must be Decimal (parse with parse_float=Decimal)")
            if qty == ZERO:
                side.pop(price, None)
            else:
                side[price] = qty

    def apply(self, msg_type: str, bids: Iterable[Mapping], asks: Iterable[Mapping]) -> None:
        if msg_type == "snapshot":
            self.bids.clear()
            self.asks.clear()
            self.has_snapshot = True
        self._apply_side(self.bids, bids)
        self._apply_side(self.asks, asks)
        self._truncate()

    def _truncate(self) -> None:
        if len(self.bids) > self.depth:
            keep = sorted(self.bids, reverse=True)[: self.depth]
            self.bids = {p: self.bids[p] for p in keep}
        if len(self.asks) > self.depth:
            keep = sorted(self.asks)[: self.depth]
            self.asks = {p: self.asks[p] for p in keep}

    def top_asks(self, n: int) -> list[tuple[Decimal, Decimal]]:
        return [(p, self.asks[p]) for p in sorted(self.asks)[:n]]

    def top_bids(self, n: int) -> list[tuple[Decimal, Decimal]]:
        return [(p, self.bids[p]) for p in sorted(self.bids, reverse=True)[:n]]

    def checksum(self) -> int:
        pp, qp = self.price_precision, self.qty_precision
        parts = [_fmt(p, pp) + _fmt(q, qp) for p, q in self.top_asks(10)]
        parts += [_fmt(p, pp) + _fmt(q, qp) for p, q in self.top_bids(10)]
        return zlib.crc32("".join(parts).encode("ascii")) & 0xFFFFFFFF

    def verify(self, expected: int) -> bool:
        return self.checksum() == expected

    @property
    def best_bid(self) -> tuple[Decimal, Decimal] | None:
        top = self.top_bids(1)
        return top[0] if top else None

    @property
    def best_ask(self) -> tuple[Decimal, Decimal] | None:
        top = self.top_asks(1)
        return top[0] if top else None

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
