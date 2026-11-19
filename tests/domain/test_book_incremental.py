"""ADR-018 — incremental book: same checksum and levels as the naive algorithm; precision change."""

import random
import zlib
from decimal import Decimal

from app.domain.trading.market.book import OrderBook

D = Decimal


def _naive_checksum(bids: dict, asks: dict, pp: int, qp: int) -> int:
    def f(v: Decimal, p: int) -> str:
        return format(v, f".{p}f").replace(".", "").lstrip("0")
    a = sorted(asks)[:10]
    b = sorted(bids, reverse=True)[:10]
    s = "".join(f(p, pp) + f(asks[p], qp) for p in a) + "".join(f(p, pp) + f(bids[p], qp) for p in b)
    return zlib.crc32(s.encode()) & 0xFFFFFFFF


def test_random_updates_match_the_naive_algorithm():
    rng = random.Random(42)
    book = OrderBook("X/USD", depth=10, price_precision=2, qty_precision=4)
    bids: dict = {}
    asks: dict = {}
    for step in range(3000):
        snapshot = step % 500 == 0
        if snapshot:
            bids, asks = {}, {}
        bl, al = [], []
        for _ in range(rng.randint(1, 4)):
            side, store, lst = rng.choice([("b", bids, bl), ("a", asks, al)])
            base = 100 if side == "b" else 101
            price = D(base) + (D(rng.randint(-150, 150)) / 100) * (1 if side == "a" else -1)
            qty = D(0) if (store and rng.random() < 0.3) else D(rng.randint(1, 50000)) / 10000
            if qty == 0 and store:
                price = rng.choice(list(store))
            lst.append({"price": price, "qty": qty})
            if qty == 0:
                store.pop(price, None)
            else:
                store[price] = qty
        book.apply("snapshot" if snapshot else "update", bl, al)
        bids = dict(sorted(bids.items(), reverse=True)[:10])
        asks = dict(sorted(asks.items())[:10])
        assert book.checksum() == _naive_checksum(bids, asks, 2, 4), step
        assert book.top_bids(10) == sorted(bids.items(), reverse=True)
        assert book.top_asks(10) == sorted(asks.items())
        if bids:
            assert book.best_bid == max(bids.items())
        if asks:
            assert book.best_ask == min(asks.items())


def test_precision_change_invalidates_cached_text():
    book = OrderBook("X/USD", depth=10, price_precision=2, qty_precision=4)
    book.apply("snapshot", [{"price": D("99.5"), "qty": D("1.25")}], [{"price": D("100.5"), "qty": D("2")}])
    before = book.checksum()
    book.price_precision = 3
    fresh = OrderBook("X/USD", depth=10, price_precision=3, qty_precision=4)
    fresh.apply("snapshot", [{"price": D("99.5"), "qty": D("1.25")}], [{"price": D("100.5"), "qty": D("2")}])
    assert book.checksum() == fresh.checksum() != before
