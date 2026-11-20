"""M2.2 backtest (09-recherche/04, T-M2.2-01..07)."""

import json
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select

from app.core.database import Base
from app.domain.trading.models import BookSnapshot, Position, StrategyVersion, TradeMinute
from app.domain.trading.research import backtest as bt
from app.domain.trading.research.backtest import BacktestError, build_backtest
from app.domain.trading.seed import STRATEGY_FILE, run_seed

from .agent_fakes import NOW, IID, T_WINDOW_END, Clock, add_instrument, add_minutes, market_state, set_book
from .agent_fakes import engine as live_engine

pytestmark = pytest.mark.anyio
D = Decimal
M = timedelta(minutes=1)
START, END = T_WINDOW_END, T_WINDOW_END + M * 15
ENTRY_BOOK = dict(asks=[("101", "50"), ("101.1", "50")], bids=[("100.9", "50"), ("100.8", "50")])
TP_BOOK = dict(asks=[("108.2", "50")], bids=[("108", "50")])


async def _prod(session_factory, *, skip_snapshot_at=None):
    """A « production » base: seed + one pair with a spike window ending at START and books."""
    async with session_factory() as db:
        await run_seed(db)
        await add_instrument(db)
        await add_minutes(db)
        for i in range(15):
            m = START + M * i
            if m == skip_snapshot_at:
                continue
            book = ENTRY_BOOK if i == 0 else TP_BOOK
            db.add(BookSnapshot(instrument_id=IID, minute_start=m, captured_at=m + timedelta(seconds=2),
                                best_bid=D(book["bids"][0][0]), best_ask=D(book["asks"][0][0]),
                                spread_bps=D(1), bids=[list(x) for x in book["bids"]],
                                asks=[list(x) for x in book["asks"]]))
        await db.commit()


async def _run(session_factory, **kw):
    args: dict[str, Any] = dict(strategy_ref="volume-spike@0.2.0", policy_ref=None, start=START, end=END,
                pairs=None, capital=D(10000), split=D("0.6"), fee_multiplier=D(1))
    return await build_backtest(session_factory, **(args | kw))


async def _row_counts(session_factory):
    async with session_factory() as db:
        return {t.name: await db.scalar(select(func.count()).select_from(t))
                for t in Base.metadata.sorted_tables if t.name.startswith("hl_")}


# T-M2.2-01 ---------------------------------------------------------------------------------
async def test_engine_never_sees_a_future_minute(session_factory, monkeypatch):
    await _prod(session_factory)
    seen = []

    class Spy(bt.DecisionEngine):
        async def tick(self, stop=None):
            now = self._now()
            floor = now.replace(second=0, microsecond=0)
            everything = await self._load_minutes(None, IID, start=floor - M * 200, end=floor + M * 200)
            seen.append((now, max(everything)))
            await super().tick(stop)

    monkeypatch.setattr(bt, "DecisionEngine", Spy)
    await _run(session_factory)
    assert len(seen) == 15
    for now, latest in seen:
        assert latest is not None and latest < now.replace(second=0, microsecond=0)


# T-M2.2-02 ---------------------------------------------------------------------------------
async def test_same_trade_as_the_live_engine(session_factory):
    await _prod(session_factory)
    report = await _run(session_factory)
    assert report.overall.trades == 1
    (trade,) = report.trades
    assert trade.exit_reason == "TAKE_PROFIT" and trade.instrument_id == IID

    # the M1.3 engine on the same minutes and books (tests/domain/test_recette « traded world »)
    eng, live = bt.sandbox_sessions()
    try:
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with live() as db:
            await run_seed(db)
            await add_instrument(db)
            await add_minutes(db)
            await db.commit()
        clock, st = Clock(NOW), market_state()
        e = live_engine(live, st, clock)
        await e.tick()
        set_book(st, asks=((108.2, 50),), bids=((108, 50),))
        clock.advance(seconds=60)
        await e.tick()
        async with live() as db:
            (p,) = (await db.scalars(select(Position))).all()
    finally:
        await eng.dispose()
    assert p.exit_reason == trade.exit_reason
    assert p.avg_entry_price == trade.entry_price and trade.entry_notional > 0
    assert p.realized_pnl_net == trade.realized_pnl_net and p.fees_paid == trade.fees_paid


# T-M2.2-03 ---------------------------------------------------------------------------------
async def test_two_runs_give_the_same_report(session_factory):
    await _prod(session_factory)
    a, b = await _run(session_factory), await _run(session_factory)
    assert json.dumps(a.as_json()) == json.dumps(b.as_json())
    assert a.header["dataset_sha256"] == b.header["dataset_sha256"]
    text = a.as_text()
    assert "écarts connus avec le bot" in text and "référence sans filtre" in text


# T-M2.2-04 ---------------------------------------------------------------------------------
async def test_missing_snapshot_means_stale_and_no_entry(session_factory):
    await _prod(session_factory, skip_snapshot_at=START)
    report = await _run(session_factory)
    assert report.overall.trades == 0 and report.trades == []


# T-M2.2-05 ---------------------------------------------------------------------------------
async def test_draft_strategy_files(session_factory, tmp_path):
    await _prod(session_factory)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"strategy_id": "x"}), encoding="utf-8")
    with pytest.raises(BacktestError, match="invalide"):
        await _run(session_factory, strategy_ref=str(bad))
    draft = json.loads(STRATEGY_FILE.read_text(encoding="utf-8"))
    draft["version"] = "0.9.0-draft"
    for c in draft["entry"]["conditions"]:
        if c["feature"] == "volume_spike":
            c["threshold"] = "50"                          # nothing passes any more
    good = tmp_path / "draft.json"
    good.write_text(json.dumps(draft), encoding="utf-8")
    report = await _run(session_factory, strategy_ref=str(good))
    assert report.overall.trades == 0 and "0.9.0-draft" in report.header["strategy"]
    async with session_factory() as db:                   # never published in production
        assert await db.scalar(select(StrategyVersion).where(StrategyVersion.version == "0.9.0-draft")) is None


# T-M2.2-06 ---------------------------------------------------------------------------------
async def test_fee_stress_and_time_split(session_factory):
    await _prod(session_factory)
    base = await _run(session_factory)
    stressed = await _run(session_factory, fee_multiplier=D(2))
    assert stressed.header["taker_fee_pct"] == "0.8"
    for report, rate in ((base, D("0.004")), (stressed, D("0.008"))):
        (t,) = report.trades
        assert t.fees_paid == (t.entry_notional + t.exit_notional) * rate
    assert base.overall.as_json()["mean_net_pct"] is not None
    assert stressed.overall.net < base.overall.net
    early = await _run(session_factory, split=D(0))       # cut at the start: all in part 2
    assert early.first.trades == 0 and early.second.trades == 1
    late = await _run(session_factory, split=D(1))
    assert late.first.trades == 1 and late.second.trades == 0


# T-M2.2-07 ---------------------------------------------------------------------------------
async def test_production_database_is_unchanged(session_factory):
    await _prod(session_factory)
    before = await _row_counts(session_factory)
    await _run(session_factory)
    assert await _row_counts(session_factory) == before


async def test_no_snapshot_at_all_is_a_clear_error(session_factory):
    async with session_factory() as db:
        await run_seed(db)
    with pytest.raises(BacktestError, match="hl_book_snapshot"):
        await _run(session_factory, start=None, end=None)
