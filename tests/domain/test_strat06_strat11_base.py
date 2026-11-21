"""ADR-024 base (S6.0): STRAT-06/STRAT-11 features, market view, exit conditions (05-strategies/04,
06-agents/03, T-R21, T-R31, T-M2.3-07)."""

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.domain.trading.agents.features import (
    BTC_IID,
    MinuteData,
    OK,
    Feature,
    change_feature,
    market_view,
    reversion_features,
)
from app.domain.trading.config_schemas import Entry, EntryCondition, StrategyConfig
from app.domain.trading.enums import PositionStatus
from app.domain.trading.models import DomainEvent, Position, RiskPolicy, TradeMinute
from app.domain.trading.seed import run_seed
from app.domain.trading.versioning import strategy_coherence_errors

from .agent_fakes import IID, T_WINDOW_END, Clock, add_instrument, add_minutes, engine, market_state

pytestmark = pytest.mark.anyio
D = Decimal
T0 = datetime(2026, 11, 18, 0, 0, tzinfo=timezone.utc)
M = timedelta(minutes=1)
EXAMPLES = Path("docs/GROK_BOT_TERMINAL_BIBLE_V2_COMPLETE/grok-bot-bible-v2/examples")


def _md(close, high=None, source="live"):
    return MinuteData(D(10000), D(5000), close, source, 10, close if high is None else high, close)


# T-R21 -------------------------------------------------------------------------------------
def test_reversion_features_values_and_unknowns():
    end = T0 + M * 2000
    mins = {end - M * k: _md(D(100)) for k in range(1, 250)}
    mins[end - M * 100] = _md(D(100), high=D(110))            # 3 h high
    mins[end - M * 1440] = _md(D(80))                         # 24 h reference (narrow read)
    mins[end - M] = _md(D(99))
    f = reversion_features(mins, end)
    assert f["drop_from_high_3h"].value == (D(99) / D(110)).ln()
    assert f["gap_to_mean_4h"].value == (D(99) / D(100)).ln()
    assert f["trend_24h"].value == (D(99) / D(80)).ln()

    sparse = {k: v for k, v in mins.items() if not (end - M * 40 <= k < end - M)}   # 39 minutes missing
    g = reversion_features(sparse, end)
    assert g["drop_from_high_3h"].reason == "INSUFFICIENT_HISTORY"                 # 141 < 162
    assert g["gap_to_mean_4h"].reason == "INSUFFICIENT_HISTORY"                    # 201 < 216
    del mins[end - M * 1440]
    assert reversion_features(mins, end)["trend_24h"].reason == "INSUFFICIENT_HISTORY"
    mins[end - M] = _md(D(99), source="backfill")
    assert {x.reason for x in reversion_features(mins, end).values()} == {"LAST_MINUTE_NOT_LIVE"}


# T-R31 -------------------------------------------------------------------------------------
def _trend(v):
    return Feature("trend_24h", D(str(v)), "log_return")


def test_market_view_rank_rs_and_btc():
    trends = {f"kraken_spot:P{i:02d}/USD": _trend(i / 100) for i in range(24)}
    trends["kraken_spot:TIE/USD"] = _trend("0.23")                      # ties with P23
    trends[BTC_IID] = _trend("0.05")
    trends["kraken_spot:UNK/USD"] = Feature("trend_24h", None, "log_return", status="UNKNOWN",
                                            reason="INSUFFICIENT_HISTORY")
    view = market_view(trends, Feature("btc_change_3h", D("-0.01"), "log_return"))
    top = view.for_pair("kraken_spot:P23/USD")
    assert top["rs_rank_24h"].value == 1 and view.for_pair("kraken_spot:TIE/USD")["rs_rank_24h"].value == 1
    assert view.for_pair("kraken_spot:P22/USD")["rs_rank_24h"].value == 3
    assert top["rs_24h"].value == D("0.23") - D("0.05")
    assert top["btc_trend_24h"].value == D("0.05") and top["btc_change_3h"].value == D("-0.01")
    assert view.for_pair("kraken_spot:UNK/USD")["rs_rank_24h"].status == "UNKNOWN"
    assert view.for_pair("kraken_spot:OTHER/USD")["rs_rank_24h"].reason == "NOT_IN_UNIVERSE"


def test_market_view_needs_20_pairs_and_btc():
    few = {f"kraken_spot:P{i}/USD": _trend(i / 100) for i in range(5)}
    view = market_view(few, Feature("btc_change_3h", None, "log_return", status="UNKNOWN", reason="X"))
    p = view.for_pair("kraken_spot:P1/USD")
    assert p["rs_rank_24h"].reason == "TOO_FEW_PAIRS"
    assert p["btc_trend_24h"].reason == "BTC_NOT_IN_UNIVERSE" and p["rs_24h"].status == "UNKNOWN"
    assert p["btc_change_3h"].status == "UNKNOWN"


def test_change_feature_tolerance():
    end = T0 + M * 500
    mins = {end - M: _md(D(110)), end - M * 190: _md(D(100))}
    assert change_feature("btc_change_3h", mins, end, 180).value == (D(110) / D(100)).ln()
    assert change_feature("btc_change_3h", {end - M: _md(D(110))}, end, 180).status != OK


# T-M2.3-07 : coherence and examples ---------------------------------------------------------
def _cfg(path: str) -> StrategyConfig:
    return StrategyConfig.model_validate(json.loads((EXAMPLES / path).read_text(encoding="utf-8")))


async def test_examples_are_coherent_and_exit_features_are_checked(db):
    await run_seed(db)
    db.add(RiskPolicy(id="rotation", name="rotation"))
    await db.flush()
    for name in ("strategy-rebound.json", "strategy-rotation.json"):
        assert await strategy_coherence_errors(db, _cfg(name)) == [], name
    raw = json.loads((EXAMPLES / "strategy-rebound.json").read_text(encoding="utf-8"))
    raw["exit"]["conditions"]["conditions"][0]["feature"] = "nope"
    bad = StrategyConfig.model_validate(raw)
    errors = await strategy_coherence_errors(db, bad)
    assert errors and errors[0]["path"] == "exit.conditions.conditions[0].feature"


def test_version_without_exit_conditions_keeps_its_stored_form():
    from app.domain.trading.config_schemas import stored_strategy_config
    cfg = _cfg("strategy-volume-spike.json")
    assert "conditions" not in stored_strategy_config(cfg)["exit"]


# T-M2.3-07 : engine -------------------------------------------------------------------------
async def _open_position(session_factory):
    async with session_factory() as db:
        seed = await run_seed(db)
        await add_instrument(db)
        await add_minutes(db)
        await db.commit()
    clock = Clock(T_WINDOW_END + timedelta(seconds=40))
    eng = engine(session_factory, market_state(), clock)
    await eng.tick()
    async with session_factory() as db:
        pos = await db.scalar(select(Position).where(Position.bot_id == seed.bot.id))
        # one more closed live minute so the next window is complete
        db.add(TradeMinute(instrument_id=IID, minute_start=T_WINDOW_END, source="live",
                           notional_usd=D(20000), buy_notional_usd=D(14000), sell_notional_usd=D(6000),
                           buy_count=1, sell_count=1, trade_count=10, volume_base=D(10),
                           open=D(101), high=D(101), low=D(101), close=D(101)))
        await db.commit()
    assert pos is not None and pos.status is PositionStatus.OPEN
    return seed.bot.strategy_version_id, pos.id, clock, eng


def _with_exit(eng, version_id, feature, op, threshold):
    strat = eng._configs[version_id]  # noqa: SLF001
    cond = EntryCondition(feature=feature, op=op, threshold=threshold, unit="log_return", window_seconds=300)
    eng._configs[version_id] = strat.model_copy(update={  # noqa: SLF001
        "exit": strat.exit.model_copy(update={"conditions": Entry(operator="OR", conditions=[cond])})})


async def test_exit_condition_pass_closes_the_position(session_factory):
    version_id, pos_id, clock, eng = await _open_position(session_factory)
    _with_exit(eng, version_id, "price_change_5m", "gt", "-1")
    clock.advance(seconds=60)                                 # next window, settled
    await eng.tick()
    async with session_factory() as db:
        pos = await db.get(Position, pos_id)
        assert pos.status is PositionStatus.CLOSED and pos.exit_reason == "EXIT_CONDITION"
        ev = await db.scalar(select(DomainEvent).where(DomainEvent.event_type == "EXIT_CONDITION_MET"))
        assert ev is not None and ev.payload["conditions"][0]["result"] == "PASS"


async def test_exit_condition_unknown_never_sells(session_factory):
    version_id, pos_id, clock, eng = await _open_position(session_factory)
    _with_exit(eng, version_id, "trend_24h", "gt", "-1")      # no 24 h history ⇒ UNKNOWN
    clock.advance(seconds=65)
    await eng.tick()
    async with session_factory() as db:
        assert (await db.get(Position, pos_id)).status is PositionStatus.OPEN
