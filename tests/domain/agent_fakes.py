"""Helpers for M1.3 agent tests: controllable clock, synthetic book/market state, minutes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.trading.agents.engine import DecisionEngine, EngineConfig
from app.domain.trading.market.book import OrderBook
from app.domain.trading.market.state import LIVE, FeedInfo, InstrumentRules, MarketState
from app.domain.trading.models import Instrument, TradeMinute

D = Decimal
SYMBOL = "TEST/USD"
IID = f"kraken_spot:{SYMBOL}"
T_WINDOW_END = datetime(2026, 11, 18, 10, 5, tzinfo=timezone.utc)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw) -> None:
        self.now += timedelta(**kw)


async def no_sleep(_s: float) -> None:
    return None


def levels(*pairs) -> list[dict]:
    return [{"price": D(str(p)), "qty": D(str(q))} for p, q in pairs]


def market_state(asks=((101, 50), (101.1, 50)), bids=((100.9, 50), (100.8, 50)),
                 status="online", fresh=True) -> MarketState:
    st = MarketState()
    st.rules[SYMBOL] = InstrumentRules(symbol=SYMBOL, instrument_id=IID, status=status,
                                       price_precision=2, qty_precision=4,
                                       qty_min=D("0.001"), cost_min=D("0.5"))
    book = OrderBook(SYMBOL, depth=25, price_precision=2, qty_precision=4)
    book.apply("snapshot", levels(*bids), levels(*asks))
    st.books[SYMBOL] = book
    st.feeds[SYMBOL] = FeedInfo(state=LIVE if fresh else "STALE", reason=None)
    st.connection_alive = True
    return st


def set_book(st: MarketState, asks, bids) -> None:
    st.books[SYMBOL].apply("snapshot", levels(*bids), levels(*asks))


async def add_instrument(db: AsyncSession, volume=D("50000000")) -> None:
    db.add(Instrument(id=IID, venue="kraken_spot", symbol=SYMBOL, base="TEST", quote="USD",
                      pair_decimals=2, lot_decimals=4, status="online", in_universe=True,
                      volume_24h_usd=volume, ordermin=D("0.001"), costmin=D("0.5")))
    await db.flush()


async def add_minutes(db: AsyncSession, *, window_end=T_WINDOW_END, baseline_per_min=D(4000),
                      current_per_min=D(20000), current_trades=10, buy_share=D("0.7"), prev_close=D(100),
                      last_close=D(101), history=70, current_source="live",
                      skip_current: int | None = None) -> None:
    """`history` minutes before the 5-minute window (baseline, live) + the window itself."""
    for i in range(history, 0, -1):
        m = window_end - timedelta(minutes=5 + i)
        db.add(TradeMinute(instrument_id=IID, minute_start=m, source="live",
                           notional_usd=baseline_per_min, buy_notional_usd=baseline_per_min / 2,
                           sell_notional_usd=baseline_per_min / 2, buy_count=1, sell_count=1,
                           trade_count=2, volume_base=D(2), open=prev_close, high=prev_close,
                           low=prev_close, close=prev_close))
    for i in range(5):
        if skip_current is not None and i == skip_current:
            continue
        m = window_end - timedelta(minutes=5 - i)
        close = last_close if i == 4 else prev_close
        db.add(TradeMinute(instrument_id=IID, minute_start=m, source=current_source,
                           notional_usd=current_per_min,
                           buy_notional_usd=None if current_source == "backfill" else current_per_min * buy_share,
                           sell_notional_usd=None if current_source == "backfill" else current_per_min * (1 - buy_share),
                           buy_count=1, sell_count=1, trade_count=current_trades, volume_base=D(10),
                           open=close, high=close, low=close, close=close))
    await db.flush()


def engine(session_factory, state, clock) -> DecisionEngine:
    return DecisionEngine(session_factory, state, clock=clock, sleep=no_sleep,
                          config=EngineConfig(snapshot_seconds=60, reconcile_seconds=300))
