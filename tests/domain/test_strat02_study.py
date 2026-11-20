"""STRAT-02 « Breakout 1h » features and offline study (09-recherche/05, T-R11..T-R14)."""

from datetime import datetime, timedelta, timezone

import pytest
from decimal import Decimal

from app.domain.trading.agents.features import (
    FEATURE_CATALOGUE,
    MinuteData,
    breakout_features,
    scanner_features,
)
from app.domain.trading.research.strat01_study import Bar, ExitRule, Trade
from app.domain.trading.research.strat02_study import (
    EntryRule,
    Row,
    Signal,
    baseline_signals,
    compute_signals,
    run_pair,
    verdict,
)

pytestmark = pytest.mark.anyio
D = Decimal
T0 = datetime(2026, 11, 18, 0, 0, tzinfo=timezone.utc)
M = timedelta(minutes=1)
IID = "kraken_spot:TEST/USD"


def _md(close, high=None, low=None, notional=D(10000), source="live"):
    return MinuteData(notional, notional / 2, close, source, 10,
                      close if high is None else high, close if low is None else low)


def _series(n: int, close=D(100)) -> dict[datetime, MinuteData]:
    return {T0 + M * i: _md(close) for i in range(n)}


# T-R11 -------------------------------------------------------------------------------------
def test_breakout_range_and_trend_values():
    mins = _series(300, D(100))
    end = T0 + M * 300
    for k in range(2, 62):                                    # range [t−61, t−1): 99..101
        mins[end - M * k] = _md(D(100), high=D(101), low=D(99))
    mins[end - M * 240] = _md(D(90))                          # trend reference
    mins[end - M] = _md(D(102))                               # last minute breaks out
    f = breakout_features(mins, end)
    assert f["breakout_1h"].value == (D(102) / D(101)).ln()
    assert f["range_1h"].value == (D(101) / D(99)).ln()
    assert f["trend_4h"].value == (D(102) / D(90)).ln()
    assert all(f[n].status == "OK" for n in ("breakout_1h", "range_1h", "trend_4h"))


def test_breakout_unknown_cases():
    end = T0 + M * 300
    mins = _series(300)
    for k in range(2, 9):                                     # 7 of 60 missing ⇒ 53 < 54
        del mins[end - M * k]
    f = breakout_features(mins, end)
    assert f["breakout_1h"].reason == f["range_1h"].reason == "INSUFFICIENT_HISTORY"

    mins = _series(300)
    for k in range(240, 251):                                 # no close at t−240 ± 10
        del mins[end - M * k]
    assert breakout_features(mins, end)["trend_4h"].reason == "INSUFFICIENT_HISTORY"
    mins[end - M * 250] = _md(D(100))                         # 10 minutes earlier is accepted
    assert breakout_features(mins, end)["trend_4h"].status == "OK"

    mins = _series(300)
    mins[end - M] = _md(D(100), source="backfill")
    assert {f.reason for f in breakout_features(mins, end).values()} == {"LAST_MINUTE_NOT_LIVE"}


def test_catalogue_and_scanner_expose_the_new_features():
    for name in ("breakout_1h", "range_1h", "trend_4h"):
        assert FEATURE_CATALOGUE[name][0] == "log_return"
    feats = scanner_features(_series(300), T0 + M * 300)
    assert {"volume_spike", "breakout_1h", "range_1h", "trend_4h"} <= set(feats)


# T-R12 -------------------------------------------------------------------------------------
def test_signal_ignores_data_after_the_decision():
    mins = _series(400)
    at = T0 + M * 300
    before = compute_signals(mins, at, at)
    assert len(before) == 1
    later = dict(mins)
    for i in range(300, 400):
        later[T0 + M * i] = _md(D(1), notional=D(1))
    assert compute_signals(later, at, at) == before


# T-R13 -------------------------------------------------------------------------------------
def _sig(at):
    return Signal(at=at, ref_close=D(100), spike=D(3), notional=D(30000), breakout=D("0.004"),
                  range_1h=D("0.01"), trend=D("0.02"))


def test_cooldown_of_four_hours_and_one_position_per_pair():
    bars = {T0 + M * i: Bar(D(100), D(100), D(100), D(100)) for i in range(2000)}
    rule = ExitRule(60, D(2), D(2))
    sigs = [_sig(T0 + M * i) for i in range(0, 1000, 10)]
    trades = run_pair(IID, bars, sigs, rule, T0 + M * 2000)
    # each trade holds 60 min then waits 4 h: next entry ≥ 300 min later
    gaps = [(b.at - a.at) for a, b in zip(trades, trades[1:])]
    assert trades and all(g >= timedelta(minutes=300) for g in gaps)


def test_entry_rule_and_baseline():
    rule = EntryRule(D(2), True, D("0.005"))
    assert rule.accepts(_sig(T0))
    assert not rule.accepts(Signal(T0, D(100), D(3), D(30000), D("0.006"), D("0.01"), D("0.02")))  # chased
    assert not rule.accepts(Signal(T0, D(100), D(3), D(30000), D("0.004"), D("0.06"), D("0.02")))  # range
    assert not rule.accepts(Signal(T0, D(100), D(3), D(30000), D("0.004"), D("0.01"), None))      # trend
    assert EntryRule(D(2), False, D("0.005")).accepts(
        Signal(T0, D(100), D(3), D(30000), D("0.004"), D("0.01"), None))
    assert not rule.accepts(Signal(T0, D(100), D(3), D(20000), D("0.004"), D("0.01"), D("0.02")))  # notional
    hours = baseline_signals([_sig(T0 + M * i) for i in range(180)])
    assert [s.at.minute for s in hours] == [0, 0, 0]


# T-R14 -------------------------------------------------------------------------------------
def _trades(n: int, pct: str, at=T0) -> list[Trade]:
    exit_px = D(100) * (1 + D(pct) / 100)
    return [Trade(IID, at + M * i, D(100), exit_px, "TIME", at + M * (i + 60)) for i in range(n)]


def test_verdict_uses_the_five_criteria_on_part_two():
    ex = ExitRule(360, D("2.5"), D(2))
    good = Row(EntryRule(D(2), True, D("0.01")), ex, _trades(12, "3"), _trades(15, "3") + _trades(10, "-1"))
    base = Row(None, ex, _trades(50, "0"), _trades(50, "0"))
    v = verdict(good, base)
    assert v["launch"] is True and all(c["ok"] for c in v["criteria"])

    small = Row(good.entry, ex, good.first, _trades(10, "3"))
    v = verdict(small, base)
    assert v["launch"] is False and not v["criteria"][0]["ok"]

    bad = Row(good.entry, ex, good.first, _trades(25, "0.5"))     # gross > 0 but net < 0 after fees
    v = verdict(bad, base)
    assert v["launch"] is False and not v["criteria"][1]["ok"]
    assert verdict(None, None)["launch"] is False


# the example version is publishable as is, without code (05-strategies/04, ADR-023) ----------
async def test_example_strategy_is_coherent(db):
    import json
    from pathlib import Path

    from app.domain.trading.config_schemas import StrategyConfig
    from app.domain.trading.seed import run_seed
    from app.domain.trading.versioning import strategy_coherence_errors

    await run_seed(db)                                        # creates the paper-default policy
    path = Path("docs/GROK_BOT_TERMINAL_BIBLE_V2_COMPLETE/grok-bot-bible-v2/examples/strategy-breakout-1h.json")
    config = StrategyConfig.model_validate(json.loads(path.read_text(encoding="utf-8")))
    assert await strategy_coherence_errors(db, config) == []
