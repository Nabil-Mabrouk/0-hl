"""M2.1 — book snapshots and long retention (07-data/03 « Instantanés de carnet », T-M2.1-01..03)."""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.domain.trading.market.service import MarketDataService
from app.domain.trading.market.state import FeedInfo
from app.domain.trading.models import BookSnapshot, TradeMinute

from .agent_fakes import IID, NOW, SYMBOL, Clock, market_state
from .market_fakes import FakeConnector, rest_client

pytestmark = pytest.mark.anyio
D = Decimal


def _service(session_factory, clock, **state_kw) -> MarketDataService:
    svc = MarketDataService(session_factory, rest_client(), FakeConnector([]), clock=clock)
    svc.state = market_state(**state_kw)
    svc.wanted = {SYMBOL}
    return svc


async def _snapshots(session_factory):
    async with session_factory() as db:
        return list((await db.scalars(select(BookSnapshot).order_by(BookSnapshot.minute_start))).all())


async def test_one_snapshot_per_live_pair_and_minute(session_factory):
    clock = Clock(NOW.replace(second=1))
    svc = _service(session_factory, clock)
    await svc.persist()
    clock.advance(seconds=5)
    await svc.persist()                                   # same minute: nothing more
    rows = await _snapshots(session_factory)
    assert len(rows) == 1
    s = rows[0]
    assert s.instrument_id == IID and s.minute_start == NOW.replace(second=0)
    assert s.best_bid == D("100.9") and s.best_ask == D("101")
    assert s.bids == [["100.9", "50"], ["100.8", "50"]] and s.asks == [["101", "50"], ["101.1", "50"]]
    assert s.spread_bps == (D("0.1") / D("100.95")) * 10000
    clock.advance(minutes=1)
    await svc.persist()
    assert len(await _snapshots(session_factory)) == 2


async def test_restart_within_the_same_minute_does_not_duplicate(session_factory):
    clock = Clock(NOW.replace(second=1))
    await _service(session_factory, clock).persist()
    clock.advance(seconds=10)
    await _service(session_factory, clock).persist()      # new worker, same minute
    assert len(await _snapshots(session_factory)) == 1


async def test_no_snapshot_for_stale_pair_or_one_sided_book(session_factory):
    clock = Clock(NOW)
    svc = _service(session_factory, clock, fresh=False)
    await svc.persist()
    assert await _snapshots(session_factory) == []
    clock.advance(minutes=1)
    svc.state.feeds[SYMBOL] = FeedInfo(state="LIVE", reason=None)
    svc.state.books[SYMBOL].apply("snapshot", [], [{"price": D(101), "qty": D(1)}])
    await svc.persist()
    assert await _snapshots(session_factory) == []


async def test_purge_keeps_180_days_of_minutes_and_90_of_snapshots(session_factory):
    old_min, keep_min = NOW - timedelta(days=181), NOW - timedelta(days=179)
    old_snap, keep_snap = NOW - timedelta(days=91), NOW - timedelta(days=89)
    async with session_factory() as db:
        for m in (old_min, keep_min):
            db.add(TradeMinute(instrument_id=IID, minute_start=m, source="live", notional_usd=D(1),
                               trade_count=1, volume_base=D(1)))
        for m in (old_snap, keep_snap):
            db.add(BookSnapshot(instrument_id=IID, minute_start=m, captured_at=m, best_bid=D(1),
                                best_ask=D(2), spread_bps=D(1), bids=[], asks=[]))
        await db.commit()
    await _service(session_factory, Clock(NOW), fresh=False).persist()
    async with session_factory() as db:
        mins = (await db.scalars(select(TradeMinute.minute_start))).all()
        snaps = (await db.scalars(select(BookSnapshot.minute_start))).all()
    assert mins == [keep_min] and snaps == [keep_snap]
