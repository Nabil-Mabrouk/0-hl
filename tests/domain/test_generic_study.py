"""Generic study of a strategy version and the breadth_24h market feature (09-recherche/08:
T-R41..T-R47; 06-agents/03 « breadth_24h »)."""

import json
import math
import random
from array import array
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.trading.agents.features import (
    HISTORY_MINUTES,
    MinuteData,
    Feature,
    evaluate_conditions,
    market_view,
    scanner_features,
)
from app.domain.trading.research import strat02_study as s02
from app.domain.trading.research import study as st
from app.domain.trading.research.strat01_study import Bar, PairData

D = Decimal
T0 = datetime(2026, 11, 18, 0, 0, tzinfo=timezone.utc)
M = timedelta(minutes=1)
A, B = "kraken_spot:AAA/USD", "kraken_spot:BBB/USD"
EXAMPLES = Path(__file__).resolve().parents[2] / "docs/GROK_BOT_TERMINAL_BIBLE_V2_COMPLETE/grok-bot-bible-v2/examples"


def _example(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def _cfg(**exit_over) -> st.StrategyConfig:
    raw = _example("strategy-breakout-1h.json")
    raw["exit"] = {"initial_stop_pct": "2", **exit_over}
    raw["cooldown_seconds"] = 0
    return st.parse_config(raw)


def _table(n: int, pairs: list[str], features: dict[str, dict[str, list[float]]] | None = None,
           closes: Decimal = D(100)) -> st.FeatureTable:
    t = st.FeatureTable(T0, n)
    for iid in pairs:
        t.values[iid] = {nm: array("d", vals) for nm, vals in (features or {}).get(iid, {}).items()}
        t.ref_close[iid] = [closes] * n
        t.spread_assumed[iid] = bytearray(n)
    return t


def _flat(n: int, close: Decimal = D(100)) -> PairData:
    pd = PairData()
    for i in range(n):
        pd.minutes[T0 + M * i] = MinuteData(D(30000), D(15000), close, "live", 20, close, close)
        pd.bars[T0 + M * i] = Bar(close, close, close, close)
    return pd


# breadth_24h -------------------------------------------------------------------------------
def test_breadth_is_the_share_of_rankable_pairs_up_over_24h():
    trends = {f"kraken_spot:P{i:02d}/USD": Feature("trend_24h", D("0.01") if i < 15 else D("-0.01"),
                                                    "log_return") for i in range(25)}
    view = market_view(trends, Feature("btc_change_3h", D(0), "log_return"))
    assert view.common["breadth_24h"].value == D(15) / D(25)
    few = dict(list(trends.items())[:19])
    assert market_view(few, Feature("btc_change_3h", D(0), "log_return")).common["breadth_24h"].reason == "TOO_FEW_PAIRS"


# T-R41 -------------------------------------------------------------------------------------
def _random_pair(seed: int, n: int) -> PairData:
    rnd = random.Random(seed)
    pd = PairData()
    px = D(100)
    for i in range(n):
        spike = i % 97 in (0, 1, 2, 3, 4)
        step = D(rnd.randint(-30, 30 + (40 if spike else 0))) / D(10000)
        o, px = px, px * (1 + step)
        hi, lo = max(o, px) * D("1.001"), min(o, px) * D("0.999")
        notional = D(60000 if spike else rnd.randint(4000, 12000))
        pd.minutes[T0 + M * i] = MinuteData(notional, notional / 2, px, "live", 20, hi, lo)
        pd.bars[T0 + M * i] = Bar(o, hi, lo, px)
    return pd


def test_entry_verdict_is_the_scanner_verdict():
    n = 900
    series = {A: _random_pair(1, n)}
    cfg = st.parse_config(_example("strategy-breakout-1h.json"))
    start, end = T0 + M * (HISTORY_MINUTES + 1), T0 + M * (n - 1)
    spreads = {(A, start + M * i): D(i % 50) for i in range(0, 600, 2)}
    table = st.build_features(series, [A], st.needed_features([cfg], None), spreads, start, end)
    seen = set()
    for i in range(table.n):
        m = table.at(i)
        feats = scanner_features(series[A].minutes, m)
        s = spreads.get((A, m))
        feats["spread_bps"] = Feature("spread_bps", s, "bps") if s is not None else Feature(
            "spread_bps", D(0), "bps")                           # no snapshot ⇒ assumed true
        expected, _ = evaluate_conditions(cfg.entry.model_dump(), feats)
        got = st.block_verdict(table, A, i, cfg.entry)
        assert got == expected, (m, got, expected)
        seen.add(got)
    assert {"PASS", "FAIL"} <= seen


# T-R42 -------------------------------------------------------------------------------------
def test_exit_order_and_unknown_never_sells():
    n = 50
    bars = {T0 + M * i: Bar(D(100), D(100), D(100), D(100)) for i in range(n)}
    cond = {"operator": "OR", "conditions": [{"feature": "trend_4h", "op": "gte", "threshold": "0",
                                              "unit": "log_return", "window_seconds": 14400}]}
    gap = [math.nan] * n
    gap[6] = 0.01                                                # PASS at minute 6 ⇒ exit at close of 5
    t = _table(n, [A], {A: {"trend_4h": gap}})
    cfg = _cfg(max_hold_seconds=1200, conditions=cond)
    tr = st.simulate_exit(t, A, bars, 0, cfg, T0 + M * n)
    assert (tr.reason, tr.closed_at) == ("EXIT_CONDITION", T0 + M * 6)

    unknown = _table(n, [A], {A: {"trend_4h": [math.nan] * n}})
    assert st.simulate_exit(unknown, A, bars, 0, cfg, T0 + M * n).reason == "TIME"

    crash = dict(bars)
    crash[T0 + M * 5] = Bar(D(100), D(100), D(90), D(95))         # stop and condition on the same minute
    assert st.simulate_exit(t, A, crash, 0, cfg, T0 + M * n).reason == "STOP"

    no_hold = _cfg(conditions=cond)
    tr = st.simulate_exit(unknown, A, bars, 0, no_hold, T0 + M * n)
    assert tr.reason == "OPEN_AT_END" and tr.excluded


# T-R43 -------------------------------------------------------------------------------------
def test_grid_product_invalid_path_and_cap():
    base = _example("strategy-pullback.json")
    grid = _example("grid-pullback.json")
    variants = st.expand_grid(base, grid)
    assert len(variants) == 2 * 3 * 3 * 3 * 2 * 2 == 216
    assert {v[1]["exit"]["max_hold_seconds"] for v in variants} == {21600, 43200}
    with pytest.raises(st.StudyError, match="inexistant"):
        st.expand_grid(base, {"entry.conditions[42].threshold": ["1"]})
    with pytest.raises(st.StudyError, match="au plus 500"):
        st.expand_grid(base, {"exit.initial_stop_pct": [str(i) for i in range(1, 30)],
                              "exit.max_hold_seconds": list(range(60, 60 * 20, 60))})
    _, bad = st.expand_grid(base, {"exit.initial_stop_pct": ["abc"]})[0]
    with pytest.raises(st.StudyError, match="invalide"):
        st.parse_config(bad, "variante")


def test_features_are_computed_once_for_all_variants(monkeypatch):
    calls = []
    real = st.scanner_features
    monkeypatch.setattr(st, "scanner_features", lambda mins, m: calls.append(m) or real(mins, m))
    n = 400
    series = {A: _flat(n), B: _flat(n)}
    base = st.parse_config(_example("strategy-breakout-1h.json"))
    raw = base.model_dump(exclude_none=True)
    variants = [(lab, st.parse_config(c)) for lab, c in
                st.expand_grid(raw, {"exit.initial_stop_pct": ["1", "2", "3"]})]
    start, end = T0 + M * 300, T0 + M * (n - 1)
    st.run_study(series, [A, B], base, variants, {}, start, end)
    assert len(calls) == 2 * (int((end - start) / M) + 1)


# T-R44 -------------------------------------------------------------------------------------
def test_max_open_positions_and_candidate_order():
    n = 30
    series = {A: _flat(n), B: _flat(n)}
    t = _table(n, [A, B], {A: {"rs_rank_24h": [5.0] * n}, B: {"rs_rank_24h": [1.0] * n}})
    cfg = _cfg(max_hold_seconds=600)
    sigs = {A: [0], B: [0]}
    alpha = st.run_portfolio(t, series, sigs, cfg, T0 + M * n, 1, lambda iid, i: (0, 0.0, iid))
    assert [tr.iid for tr in alpha] == [A]

    def by_rank(iid, i):
        return (0, t.values[iid]["rs_rank_24h"][i], iid)
    assert [tr.iid for tr in st.run_portfolio(t, series, sigs, cfg, T0 + M * n, 1, by_rank)] == [B]
    # the slot frees at the exit: B enters once A has left
    later = st.run_portfolio(t, series, {A: [0], B: [0, 12]}, cfg, T0 + M * n, 1,
                             lambda iid, i: (0, 0.0, iid))
    assert [(tr.iid, tr.at) for tr in later] == [(A, T0), (B, T0 + M * 12)]
    assert len(st.run_portfolio(t, series, sigs, cfg, T0 + M * n, None, by_rank)) == 2


# T-R45 -------------------------------------------------------------------------------------
def test_spread_from_snapshot_else_assumed_and_counted():
    n = 400
    series = {A: _flat(n)}
    start = T0 + M * 300
    spreads = {(A, start): D(50), (A, start + M): D(10)}
    t = st.build_features(series, [A], {"spread_bps"}, spreads, start, start + M * 2)
    cond = st.parse_config(_example("strategy-breakout-1h.json")).entry.conditions[-1]   # spread ≤ 30
    assert [st.condition_result(t, A, i, cond) for i in range(3)] == ["FAIL", "PASS", "PASS"]
    assert list(t.spread_assumed[A]) == [0, 0, 1]


# T-R46 -------------------------------------------------------------------------------------
def test_pass_rates_and_not_judgeable():
    n = 4
    t = _table(n, [A], {A: {"window_notional_usd": [30000.0, 10000.0, math.nan, 30000.0]}})
    t.spread_assumed[A] = bytearray([1] * n)                     # no snapshot at all
    entry = st.parse_config(_example("strategy-breakout-1h.json")).entry
    rates = st.pass_rates(t, [A], entry)
    assert rates[0]["evaluable"] == 4
    assert (rates[0]["pass_pct"], rates[0]["fail_pct"], rates[0]["unknown_pct"]) == ("50.0", "25.0", "25.0")
    assert rates[-1]["pass_pct"] == "100.0"                     # spread assumed without snapshots

    series = {A: _flat(400)}
    base = st.parse_config(_example("strategy-breakout-1h.json"))
    rep = st.run_study(series, [A], base, [], {}, T0 + M * 300, T0 + M * 399,
                       not_judgeable="3.0 jours de données, 28 requis")
    assert rep.verdict["judgeable"] is False and rep.verdict["launch"] is False
    assert "NON JUGEABLE" in rep.as_text()
    assert rep.as_json()["pass_rates"] and rep.as_json()["reproducibility"]["config_sha256"]


# T-R47 -------------------------------------------------------------------------------------
def test_reproduces_the_strat02_study_for_the_same_version():
    n = 2400
    series = {iid: _random_pair(seed, n) for seed, iid in enumerate([A, B, "kraken_spot:CCC/USD"])}
    start, end = T0 + M * (HISTORY_MINUTES + 1), T0 + M * (n - 1)
    ref = s02.run_study(series, start, end).current
    cfg = st.parse_config(_example("strategy-breakout-1h.json"))
    got = st.run_study(series, sorted(series), cfg, [], {}, start, end).current

    def key(trades):
        return sorted((t.iid, t.at, t.exit, t.reason) for t in trades)
    assert key(ref.first + ref.second), "the synthetic data must produce trades"
    assert key(got.first) == key(ref.first) and key(got.second) == key(ref.second)


# inputs from the database -------------------------------------------------------------------
@pytest.mark.anyio
async def test_load_inputs_validates_version_and_variants(db):
    from app.domain.trading.seed import run_seed

    seed = await run_seed(db)
    await db.commit()
    sv = seed.strategy_version
    base, variants = await st.load_inputs(db, strategy=f"{sv.strategy_id}@{sv.version}",
                                          file_config=None, grid={"exit.initial_stop_pct": ["1", "2"]})
    assert base.strategy_id == sv.strategy_id and len(variants) == 2
    raw = _example("strategy-pullback.json")
    base, _ = await st.load_inputs(db, strategy=None, file_config=raw, grid=None)
    assert base.strategy_id == "pullback"
    with pytest.raises(st.StudyError, match="incohérente"):
        await st.load_inputs(db, strategy=None, file_config=raw,
                             grid={"entry.conditions[0].feature": ["nope"]})
    with pytest.raises(st.StudyError, match="introuvable"):
        await st.load_inputs(db, strategy="nope@9", file_config=None, grid=None)
    assert await st._policy_max_open(db, base.risk_policy_id) == seed.policy_version.config.get("max_open_positions")
