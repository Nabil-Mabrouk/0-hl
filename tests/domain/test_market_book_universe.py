"""T-M1.2-01/03/04 — book checksum on the real recorded session, Decimal parsing, universe."""

import json
from decimal import Decimal

import pytest

from app.domain.trading.market.book import OrderBook
from app.domain.trading.market.kraken_ws import parse_message
from app.domain.trading.market.universe import EXCLUDED_BASES, select_universe, volume_24h_usd

from .market_fakes import rest_fixture, ws_session_lines


def _iter_floats(obj):
    if isinstance(obj, float):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_floats(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_floats(v)


def test_parse_never_produces_float():
    for line in ws_session_lines():
        assert list(_iter_floats(parse_message(line))) == []
    book = next(parse_message(l) for l in ws_session_lines() if l.startswith('{"channel":"book"'))
    assert isinstance(book["data"][0]["bids"][0]["price"], Decimal)


def test_checksum_matches_every_real_message():
    precisions, books = {}, {}
    checked = 0
    for line in ws_session_lines():
        msg = parse_message(line)
        if msg.get("channel") == "instrument" and msg.get("type") == "snapshot":
            precisions = {p["symbol"]: (p["price_precision"], p["qty_precision"])
                          for p in msg["data"]["pairs"]}
        if msg.get("channel") != "book":
            continue
        for item in msg["data"]:
            s = item["symbol"]
            pp, qp = precisions[s]
            book = books.setdefault(s, OrderBook(s, depth=25, price_precision=pp, qty_precision=qp))
            book.apply(msg["type"], item["bids"], item["asks"])
            assert book.verify(item["checksum"]), f"checksum mismatch on {s}"
            checked += 1
    assert checked > 500
    btc = books["BTC/USD"]
    assert len(btc.bids) <= 25 and len(btc.asks) <= 25
    assert btc.best_bid[0] < btc.best_ask[0]
    assert btc.spread_bps() is not None and btc.spread_bps() >= 0


def test_book_rejects_float_levels():
    book = OrderBook("X/USD", depth=10, price_precision=2, qty_precision=8)
    with pytest.raises(TypeError):
        book.apply("snapshot", [{"price": 1.5, "qty": Decimal("1")}], [])


def test_universe_selection_on_real_fixtures():
    pairs = rest_fixture("rest_asset_pairs.json")["result"]
    tickers = rest_fixture("rest_ticker.json")["result"]
    sel = select_universe(pairs, tickers, top_n=40, min_volume_24h_usd=Decimal("1000000"))
    assert set(sel.selected) <= {"BTC/USD", "ETH/USD", "SOL/USD"}
    assert {"BTC/USD", "ETH/USD"} <= set(sel.selected)
    for excluded in ("USDT/USD", "USDC/USD", "EUR/USD", "BTC/EUR", "ACA/USD", "AB/USD"):
        assert excluded not in sel.selected
    vols = [volume_24h_usd(tickers[s]) for s in sel.selected]
    assert vols == sorted(vols, reverse=True)
    # USD candidates (incl. excluded ones) are all evaluated for persistence; BTC/EUR is not USD
    assert {c.symbol for c in sel.candidates} == {s for s, p in pairs.items() if p["quote"] == "USD"}
    top1 = select_universe(pairs, tickers, top_n=1, min_volume_24h_usd=None)
    assert top1.selected == sel.selected[:1]
    assert "USDT" in EXCLUDED_BASES and "EUR" in EXCLUDED_BASES


def test_volume_is_base_volume_times_vwap():
    t = {"v": ["1", "2.5"], "p": ["10", "100"]}
    assert volume_24h_usd(t) == Decimal("250")
    assert volume_24h_usd(None) is None
