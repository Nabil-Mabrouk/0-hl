"""Trade aggregation per UTC minute with coverage tracking (spec 07-data/03 « Agrégats de trades »).

A `live` minute is produced only if the trade feed of that symbol was
subscribed and the connection alive for the **whole** minute; then a minute
without trades is a real zero. A minute with a disconnection is a gap
(never written, read as UNKNOWN by features).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

ZERO = Decimal(0)
MINUTE = timedelta(minutes=1)
CLOSE_GRACE = timedelta(seconds=2)


def floor_minute(ts: datetime) -> datetime:
    return ts.astimezone(timezone.utc).replace(second=0, microsecond=0)


@dataclass
class MinuteBucket:
    notional: Decimal = ZERO
    buy_notional: Decimal = ZERO
    sell_notional: Decimal = ZERO
    buy_count: int = 0
    sell_count: int = 0
    count: int = 0
    volume: Decimal = ZERO
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    close: Decimal | None = None

    def add(self, price: Decimal, qty: Decimal, side: str) -> None:
        notional = price * qty
        self.notional += notional
        self.volume += qty
        self.count += 1
        if side == "buy":
            self.buy_notional += notional
            self.buy_count += 1
        else:
            self.sell_notional += notional
            self.sell_count += 1
        if self.open is None:
            self.open = price
        self.high = price if self.high is None else max(self.high, price)
        self.low = price if self.low is None else min(self.low, price)
        self.close = price


@dataclass(frozen=True)
class MinuteRow:
    instrument_id: str
    minute_start: datetime
    source: str
    notional_usd: Decimal
    buy_notional_usd: Decimal | None
    sell_notional_usd: Decimal | None
    buy_count: int | None
    sell_count: int | None
    trade_count: int
    volume_base: Decimal
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None


@dataclass
class _SymbolState:
    instrument_id: str
    buckets: dict[datetime, MinuteBucket] = field(default_factory=dict)
    # closed coverage intervals [start, end) plus the open one (start, None)
    intervals: list[tuple[datetime, datetime | None]] = field(default_factory=list)
    next_minute: datetime | None = None  # first minute not yet flushed
    last_trade_id: int | None = None
    first_live_minute: datetime | None = None


class MinuteAggregator:
    def __init__(self) -> None:
        self._symbols: dict[str, _SymbolState] = {}

    def _state(self, symbol: str, instrument_id: str) -> _SymbolState:
        st = self._symbols.get(symbol)
        if st is None:
            st = _SymbolState(instrument_id=instrument_id)
            self._symbols[symbol] = st
        return st

    # -- coverage ----------------------------------------------------------- #
    def coverage_started(self, symbol: str, instrument_id: str, at: datetime) -> None:
        st = self._state(symbol, instrument_id)
        if st.intervals and st.intervals[-1][1] is None:
            return
        st.intervals.append((at, None))
        if st.next_minute is None:
            # first minute fully covered starts at the next minute boundary
            start = floor_minute(at)
            st.next_minute = start if start == at else start + MINUTE

    def coverage_stopped(self, symbol: str, at: datetime) -> None:
        st = self._symbols.get(symbol)
        if st and st.intervals and st.intervals[-1][1] is None:
            st.intervals[-1] = (st.intervals[-1][0], at)

    def coverage_stopped_all(self, at: datetime) -> None:
        for symbol in list(self._symbols):
            self.coverage_stopped(symbol, at)

    def _covered(self, st: _SymbolState, start: datetime, end: datetime) -> bool:
        for a, b in st.intervals:
            if a <= start and (b is None or b >= end):
                return True
        return False

    def first_live_minute(self, symbol: str) -> datetime | None:
        st = self._symbols.get(symbol)
        return st.first_live_minute if st else None

    # -- trades ------------------------------------------------------------- #
    def on_trade(
        self,
        symbol: str,
        instrument_id: str,
        *,
        trade_id: int,
        price: Decimal,
        qty: Decimal,
        side: str,
        timestamp: datetime,
    ) -> bool:
        """Add a trade; returns False for a duplicate (already seen trade_id)."""
        st = self._state(symbol, instrument_id)
        if st.last_trade_id is not None and trade_id <= st.last_trade_id:
            return False
        st.last_trade_id = trade_id
        st.buckets.setdefault(floor_minute(timestamp), MinuteBucket()).add(price, qty, side)
        return True

    # -- flush -------------------------------------------------------------- #
    def flush(self, now: datetime) -> list[MinuteRow]:
        """Close every minute that ended before `now - grace`; emit covered ones."""
        rows: list[MinuteRow] = []
        limit = floor_minute(now - CLOSE_GRACE)  # minutes strictly before `limit` are closed
        for st in self._symbols.values():
            if st.next_minute is None:
                continue
            minute = st.next_minute
            while minute < limit:
                end = minute + MINUTE
                bucket = st.buckets.pop(minute, None)
                if self._covered(st, minute, end):
                    b = bucket or MinuteBucket()
                    rows.append(
                        MinuteRow(
                            instrument_id=st.instrument_id, minute_start=minute, source="live",
                            notional_usd=b.notional, buy_notional_usd=b.buy_notional,
                            sell_notional_usd=b.sell_notional, buy_count=b.buy_count,
                            sell_count=b.sell_count, trade_count=b.count, volume_base=b.volume,
                            open=b.open, high=b.high, low=b.low, close=b.close,
                        )
                    )
                    if st.first_live_minute is None:
                        st.first_live_minute = minute
                minute = end
            st.next_minute = minute
            # drop stale buckets and closed intervals that can no longer matter
            for key in [k for k in st.buckets if k < minute]:
                del st.buckets[key]
            st.intervals = [(a, b) for a, b in st.intervals if b is None or b > minute]
        return rows


def backfill_rows(
    instrument_id: str, ohlc_entries: list[list[Any]], *, before: datetime | None
) -> list[MinuteRow]:
    """Kraken OHLC 1 min → backfill rows. The last entry (uncommitted candle) is dropped;
    only minutes strictly before `before` (first live minute) are kept. Buy/sell side is
    unknown in OHLC: NULL, never zero."""
    rows: list[MinuteRow] = []
    for entry in ohlc_entries[:-1]:
        ts, o, h, low, c, vwap, volume, count = entry[:8]
        minute = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        if before is not None and minute >= before:
            continue
        vol = Decimal(str(volume))
        trades = int(count)
        rows.append(
            MinuteRow(
                instrument_id=instrument_id, minute_start=minute, source="backfill",
                notional_usd=vol * Decimal(str(vwap)), buy_notional_usd=None,
                sell_notional_usd=None, buy_count=None, sell_count=None, trade_count=trades,
                volume_base=vol,
                open=Decimal(str(o)) if trades else None,
                high=Decimal(str(h)) if trades else None,
                low=Decimal(str(low)) if trades else None,
                close=Decimal(str(c)) if trades else None,
            )
        )
    return rows
