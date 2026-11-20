"""Offline STRAT-01 study (09-recherche/03, T-R01..T-R04)."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.domain.trading.agents.features import MinuteData
from app.domain.trading.models import Instrument, TradeMinute
from app.domain.trading.research.strat01_study import (
    FEE,
    SLIPPAGE,
    Bar,
    ExitRule,
    Row,
    Stats,
    Trade,
    build_study,
    compute_signals,
    rank,
    run_pair,
    simulate_exit,
    split_at,
)

D = Decimal
T0 = datetime(2026, 11, 18, 0, 0, tzinfo=timezone.utc)
M = timedelta(minutes=1)
IID = "kraken_spot:TEST/USD"


def _minutes(n: int, spike_at: int | None = None, close=D(100)) -> dict[datetime, MinuteData]:
    out = {}
    for i in range(n):
        hot = spike_at is not None and spike_at - 5 <= i < spike_at
        notional = D(40000) if hot else D(4000)
        out[T0 + M * i] = MinuteData(notional, notional * D("0.7") if hot else notional / 2,
                                     close + (D(1) if hot else 0), "live", 20 if hot else 2)
    return out


def _flat_bars(start: datetime, n: int, px=D(100)) -> dict[datetime, Bar]:
    return {start + M * i: Bar(px, px, px, px) for i in range(n)}


# T-R01 -------------------------------------------------------------------------------------
def test_signal_ignores_data_after_the_decision():
    mins = _minutes(120, spike_at=100)
    at = T0 + M * 100
    before = [s for s in compute_signals(mins, at, at)]
    assert before and before[0].spike is not None and before[0].spike > 4
    later = dict(mins)
    for i in range(100, 120):                                  # minute m itself and after
        later[T0 + M * i] = MinuteData(D(1), D(0), D(1), "live", 1)
    assert compute_signals(later, at, at) == before


# T-R02 -------------------------------------------------------------------------------------
def test_stop_wins_when_stop_and_target_share_a_minute():
    at = T0
    bars = {at: Bar(D(100), D(200), D(50), D(100))}
    tr = simulate_exit(IID, bars, at, D(100), ExitRule(5, D(2), D(1)))
    entry = D(100) * (1 + SLIPPAGE)
    assert tr.reason == "STOP" and tr.entry == entry
    assert tr.exit == entry * (1 - D("0.02")) * (1 - SLIPPAGE)


def test_time_exit_at_close_with_fees():
    bars = _flat_bars(T0, 5)
    bars[T0 + M * 4] = Bar(D(100), D(101), D(100), D("100.5"))
    bars[T0 + M * 3] = Bar(None, None, None, None)             # no trade: keeps last price
    tr = simulate_exit(IID, bars, T0, D(100), ExitRule(5, D(3), None))
    assert tr.reason == "TIME" and tr.exit == D("100.5") and tr.closed_at == T0 + M * 5
    assert tr.net == (D("100.5") * (1 - FEE)) / (D(100) * (1 + SLIPPAGE) * (1 + FEE)) - 1
    assert tr.gross > tr.net


def test_take_profit_in_r_multiples():
    bars = _flat_bars(T0, 3)
    bars[T0 + M] = Bar(D(100), D(103), D("99.5"), D(102))
    tr = simulate_exit(IID, bars, T0, D(100), ExitRule(15, D(1), D(2)))
    assert tr.reason == "TAKE_PROFIT" and tr.exit == D(100) * (1 + SLIPPAGE) * D("1.02")


# T-R03 -------------------------------------------------------------------------------------
def test_gap_during_hold_excludes_the_trade():
    bars = _flat_bars(T0, 10)
    del bars[T0 + M * 2]
    tr = simulate_exit(IID, bars, T0, D(100), ExitRule(5, D(3), None))
    assert tr.excluded and tr.reason == "EXCLUDED"
    st = Stats.of([tr])
    assert st.trades == 0 and st.excluded == 1


def test_one_position_per_pair_and_cooldown():
    from app.domain.trading.research.strat01_study import Signal
    bars = _flat_bars(T0, 200)
    sig = lambda i: Signal(T0 + M * i, D(100), D(5), D("0.7"), D("0.01"), D(60000), 50)  # noqa: E731
    trades = run_pair(IID, bars, [sig(0), sig(3), sig(34), sig(35), sig(80)],
                      ExitRule(5, D(3), None), T0 + M * 200)
    # 0 → closes at 5, cooldown to 35: 3 (open) and 34 (cooldown) skipped
    assert [t.at for t in trades] == [T0, T0 + M * 35, T0 + M * 80]


# T-R04 -------------------------------------------------------------------------------------
def _trade(at_min: int, net_gain: str) -> Trade:
    entry = D(100)
    exit_ = entry * (1 + D(net_gain))
    return Trade(IID, T0 + M * at_min, entry, exit_, "TIME", T0 + M * (at_min + 1))


def test_split_and_rank_on_first_part_only():
    start, end = T0, T0 + M * 100
    cut = split_at(start, end)
    assert cut == T0 + M * 60
    ex = ExitRule(5, D(1), None)
    good_first = Row(None, ex, Stats.of([_trade(i, "0.05") for i in range(20)]),
                     Stats.of([_trade(70, "-0.05")]))
    good_second = Row(None, ex, Stats.of([_trade(i, "0.0") for i in range(20)]),
                      Stats.of([_trade(70, "0.5")] * 30))
    too_few = Row(None, ex, Stats.of([_trade(i, "0.5") for i in range(5)]), Stats())
    ranked = rank([good_second, too_few, good_first])
    assert ranked == [good_first, good_second]


@pytest.mark.anyio
async def test_build_study_end_to_end_is_read_only(session_factory):
    async with session_factory() as db:
        db.add(Instrument(id=IID, venue="kraken_spot", symbol="TEST/USD", base="TEST", quote="USD",
                          pair_decimals=2, lot_decimals=4, status="online", in_universe=True))
        for i in range(300):
            hot = i % 97 in range(90, 95)
            n = D(40000) if hot else D(4000)
            px = D(100) + (D(1) if hot else 0)
            db.add(TradeMinute(instrument_id=IID, minute_start=T0 + M * i, source="live",
                               notional_usd=n, buy_notional_usd=n * D("0.7") if hot else n / 2,
                               sell_notional_usd=n / 2, buy_count=1, sell_count=1,
                               trade_count=60 if hot else 2, volume_base=D(1),
                               open=px, high=px, low=px, close=px))
        await db.commit()
    report = await build_study(session_factory, days=1, now=T0 + M * 300)
    assert report.pairs == 1 and report.variants_tested == 24 * 36
    assert report.evaluable_windows > 0
    assert report.current.label.startswith("s≥4 b≥0.60 n≥50k t≥40 | H=60m stop=3% tp=2R")
    text = report.as_text()
    assert "STRAT-01 v0.2.0" in text and "Référence" in text
    j = report.as_json()
    assert j["variants_tested"] == 864 and "baseline_v0_2_0_exits" in j
    async with session_factory() as db:
        from sqlalchemy import func, select
        assert await db.scalar(select(func.count()).select_from(TradeMinute)) == 300

