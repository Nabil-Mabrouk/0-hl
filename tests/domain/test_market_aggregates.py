"""T-M1.2-05/06/07 — minute aggregation, coverage gaps, OHLC backfill."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.domain.trading.market.aggregates import MinuteAggregator, backfill_rows

from .market_fakes import rest_fixture

D = Decimal
T0 = datetime(2026, 11, 18, 10, 0, 0, tzinfo=timezone.utc)
IID = "kraken_spot:BTC/USD"


def _trade(agg, tid, price, qty, side, at):
    return agg.on_trade("BTC/USD", IID, trade_id=tid, price=D(price), qty=D(qty), side=side,
                        timestamp=at)


def test_buy_sell_notional_and_minute_bounds_and_dedup():
    agg = MinuteAggregator()
    agg.coverage_started("BTC/USD", IID, T0)
    assert _trade(agg, 1, "100", "2", "buy", T0 + timedelta(seconds=5))
    assert _trade(agg, 2, "101", "1", "sell", T0 + timedelta(seconds=59, microseconds=999))
    assert not _trade(agg, 2, "999", "9", "buy", T0 + timedelta(seconds=30))  # duplicate id
    assert _trade(agg, 3, "102", "1", "buy", T0 + timedelta(minutes=1))  # next minute
    rows = agg.flush(T0 + timedelta(minutes=2, seconds=3))
    assert [r.minute_start for r in rows] == [T0, T0 + timedelta(minutes=1)]
    m0 = rows[0]
    assert m0.notional_usd == D("301") and m0.buy_notional_usd == D("200")
    assert m0.sell_notional_usd == D("101") and (m0.buy_count, m0.sell_count) == (1, 1)
    assert (m0.open, m0.high, m0.low, m0.close) == (D("100"), D("101"), D("100"), D("101"))
    assert rows[1].trade_count == 1


def test_current_minute_is_never_flushed_and_grace_applies():
    agg = MinuteAggregator()
    agg.coverage_started("BTC/USD", IID, T0)
    _trade(agg, 1, "100", "1", "buy", T0 + timedelta(seconds=10))
    assert agg.flush(T0 + timedelta(seconds=59)) == []
    assert agg.flush(T0 + timedelta(minutes=1, seconds=1)) == []  # within 2 s grace
    assert len(agg.flush(T0 + timedelta(minutes=1, seconds=3))) == 1


def test_covered_quiet_minute_is_zero_and_gap_is_not_written():
    agg = MinuteAggregator()
    agg.coverage_started("BTC/USD", IID, T0 + timedelta(seconds=20))  # mid-minute start
    rows = agg.flush(T0 + timedelta(minutes=3, seconds=5))
    # minute 10:00 only partly covered -> not written; 10:01 and 10:02 quiet but covered -> zeros
    assert [r.minute_start for r in rows] == [T0 + timedelta(minutes=1), T0 + timedelta(minutes=2)]
    assert rows[0].notional_usd == 0 and rows[0].trade_count == 0 and rows[0].close is None
    # disconnection during 10:03 -> gap; reconnect at 10:04:30 -> 10:05 covered again
    agg.coverage_stopped("BTC/USD", T0 + timedelta(minutes=3, seconds=40))
    agg.coverage_started("BTC/USD", IID, T0 + timedelta(minutes=4, seconds=30))
    rows = agg.flush(T0 + timedelta(minutes=6, seconds=5))
    assert [r.minute_start for r in rows] == [T0 + timedelta(minutes=5)]
    assert agg.first_live_minute("BTC/USD") == T0 + timedelta(minutes=1)


def test_backfill_drops_uncommitted_candle_and_respects_live_boundary():
    entries = rest_fixture("rest_ohlc_btc_usd_1m.json")["result"]["BTC/USD"]
    rows = backfill_rows(IID, entries, before=None)
    assert len(rows) == len(entries) - 1  # last (uncommitted) candle dropped
    r = rows[0]
    assert r.source == "backfill" and r.buy_notional_usd is None and r.buy_count is None
    ts, o, h, l, c, vwap, vol, count = entries[0]
    assert r.notional_usd == D(str(vol)) * D(str(vwap))
    boundary = rows[10].minute_start
    cut = backfill_rows(IID, entries, before=boundary)
    assert all(x.minute_start < boundary for x in cut) and len(cut) == 10
