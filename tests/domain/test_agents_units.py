"""T-M1.3-01..05 — features, conditions, market gate, sizing, book sweep (pure functions)."""

from datetime import timedelta
from decimal import Decimal

import pytest

from app.domain.trading.agents.features import evaluate_conditions, load_minutes, window_features
from app.domain.trading.agents.gate import evaluate_market_gate
from app.domain.trading.agents.sim import PAPER_TAKER_FEE, floor_qty, liquidity_cap_notional, sweep
from app.domain.trading.agents.sizing import SizingInputs, size_position
from app.domain.trading.config_schemas import RiskPolicyConfig
from app.domain.trading.market.universe import EXCLUDED_BASES

from .agent_fakes import IID, SYMBOL, T_WINDOW_END, add_instrument, add_minutes, market_state
from .factories import policy_config, strategy_config

D = Decimal
POLICY = RiskPolicyConfig.model_validate(policy_config())
ENTRY = strategy_config()["entry"]


# ---------------------------------------------------------------- features
@pytest.mark.anyio
async def test_features_on_synthetic_minutes(db):
    await add_instrument(db)
    await add_minutes(db)
    minutes = await load_minutes(db, IID, start=T_WINDOW_END - timedelta(minutes=70), end=T_WINDOW_END)
    f = window_features(minutes, T_WINDOW_END)
    assert f["volume_spike"].value == D(5)  # 100000 / median(20000)
    assert f["window_notional_usd"].value == D(100000) and f["window_trade_count"].value == D(50)
    assert f["buy_notional_share"].value == D("0.7")
    assert f["price_change_5m"].value == (D(101) / D(100)).ln()
    assert not f["volume_spike"].uses_backfill


@pytest.mark.anyio
async def test_incomplete_window_or_short_history_is_unknown(db):
    await add_instrument(db)
    await add_minutes(db, skip_current=2)
    minutes = await load_minutes(db, IID, start=T_WINDOW_END - timedelta(minutes=70), end=T_WINDOW_END)
    f = window_features(minutes, T_WINDOW_END)
    assert f["volume_spike"].status == "UNKNOWN" and f["volume_spike"].reason == "CURRENT_WINDOW_INCOMPLETE"
    assert f["buy_notional_share"].value is None


@pytest.mark.anyio
async def test_insufficient_history_and_backfill_flag(db):
    await add_instrument(db)
    await add_minutes(db, history=20)  # only 4 baseline windows
    minutes = await load_minutes(db, IID, start=T_WINDOW_END - timedelta(minutes=70), end=T_WINDOW_END)
    assert window_features(minutes, T_WINDOW_END)["volume_spike"].reason == "INSUFFICIENT_HISTORY"


def test_conditions_unknown_never_pass():
    from app.domain.trading.agents.features import Feature, UNKNOWN as U
    ok = {"volume_spike": Feature("volume_spike", D(5), "ratio"),
          "buy_notional_share": Feature("buy_notional_share", D("0.7"), "fraction"),
          "price_change_5m": Feature("price_change_5m", D("0.01"), "log_return"),
          "spread_bps": Feature("spread_bps", D(10), "bps"),
          "window_notional_usd": Feature("window_notional_usd", D(100000), "USD"),
          "window_trade_count": Feature("window_trade_count", D(50), "count")}
    assert evaluate_conditions(ENTRY, ok)[0] == "PASS"
    unknown = dict(ok, spread_bps=Feature("spread_bps", None, "bps", status=U))
    assert evaluate_conditions(ENTRY, unknown)[0] == "UNKNOWN"
    thin = dict(ok, window_notional_usd=Feature("window_notional_usd", D(2124), "USD"))
    assert evaluate_conditions(ENTRY, thin)[0] == "FAIL"          # ADR-014 activity floor
    few = dict(ok, window_trade_count=Feature("window_trade_count", D(11), "count"))
    assert evaluate_conditions(ENTRY, few)[0] == "FAIL"
    fail = dict(ok, volume_spike=Feature("volume_spike", D("3.9"), "ratio"))
    assert evaluate_conditions(ENTRY, fail)[0] == "FAIL"
    assert evaluate_conditions(ENTRY, dict(fail, spread_bps=unknown["spread_bps"]))[0] == "FAIL"


def test_gold_tokens_excluded():
    assert {"PAXG", "XAUT"} <= EXCLUDED_BASES


# ---------------------------------------------------------------- gate
def _gate(state, **kw):
    args = dict(rules=state.rules.get(SYMBOL), fresh=state.is_fresh(SYMBOL), book=state.book(SYMBOL),
                policy=POLICY, price_change_5m=D("0.01"), volume_24h_usd=D("5e7"), last_exit_at=None,
                cooldown_seconds=1800, now=T_WINDOW_END)
    args.update(kw)
    return evaluate_market_gate(**args)


def test_gate_pass_and_each_failure():
    st = market_state()
    verdict, crit = _gate(st)
    assert verdict == "PASS" and all(c.result == "PASS" for c in crit)
    failing = {
        "data_freshness": _gate(market_state(fresh=False)),                       # QA-01
        "pair_status": _gate(market_state(status="cancel_only")),                 # QA-16
        "spread": _gate(market_state(asks=((101, 50),), bids=((100, 50),))),       # ~99 bps
        "abnormal_move": _gate(st, price_change_5m=D("0.2")),
        "volume_floor": _gate(st, volume_24h_usd=D(10)),
        "cooldown": _gate(st, last_exit_at=T_WINDOW_END - timedelta(minutes=10)),
        "min_order_liquidity": _gate(market_state(asks=((101, "0.0001"),), bids=((100.9, "0.0001"),))),
    }
    for name, (verdict, crit) in failing.items():
        assert verdict == "REJECT", name
        assert {c.name: c.result for c in crit}[name] in ("FAIL", "UNKNOWN"), name


# ---------------------------------------------------------------- sweep
def test_sweep_weighted_average_limit_and_partial():
    asks = [(D(100), D(1)), (D("100.5"), D(2)), (D(102), D(5))]
    res = sweep(asks, D(2), limit=D(101), side="buy")
    assert res.filled_qty == D(2) and res.avg_price == (D(100) + D("100.5")) / 2
    partial = sweep(asks, D(10), limit=D(101), side="buy")      # limit stops at 100.5
    assert partial.filled_qty == D(3) and len(partial.levels) == 2
    bids = [(D(99), D(1)), (D(98), D(1))]
    assert sweep(bids, D(5), limit=D("98.5"), side="sell").filled_qty == D(1)
    assert floor_qty(D("1.23456789"), 4) == D("1.2345")


def test_liquidity_cap_uses_both_sides():
    asks = [(D(100), D(10)), (D(101), D(10))]
    bids = [(D("99.9"), D(3)), (D(90), D(100))]
    assert liquidity_cap_notional(asks, bids, D("0.005")) == D(3) * D(100)


# ---------------------------------------------------------------- sizing
def _inputs(**kw):
    st = market_state()
    book = st.book(SYMBOL)
    base = dict(equity=D(10000), cash_free=D(10000), open_positions=0, open_exposure_cost=D(0),
                asks=book.top_asks(25), bids=book.top_bids(25), qty_precision=4, qty_min=D("0.001"),
                cost_min=D("0.5"), policy=POLICY, initial_stop_pct=D(3), taker_fee=PAPER_TAKER_FEE)
    base.update(kw)
    return SizingInputs(**base)


def test_sizing_formula_numbers():
    res = size_position(_inputs())
    S = D("0.03") + 2 * PAPER_TAKER_FEE
    expected_notional = D(10000) * D("0.005") / S
    assert res.approved and res.limits_applied["binding"] == "notional_risk"
    assert res.risk_budget == D(50)
    assert res.quantity == floor_qty(expected_notional / D(101), 4)
    assert res.notional_approved <= expected_notional


def test_closer_stop_increases_size_but_budget_holds():
    wide, tight = size_position(_inputs(initial_stop_pct=D(3))), size_position(_inputs(initial_stop_pct=D(1)))
    assert tight.quantity > wide.quantity  # QA-03
    for r, stop in ((wide, D("0.03")), (tight, D("0.01"))):
        loss_at_stop = r.notional_approved * (stop + 2 * PAPER_TAKER_FEE)
        assert loss_at_stop <= r.risk_budget


@pytest.mark.parametrize("kw,reason", [
    (dict(open_positions=5), "MAX_OPEN_POSITIONS"),
    (dict(daily_loss=D("0.031")), "MAX_DAILY_LOSS"),
    (dict(drawdown=D("0.2")), "MAX_DRAWDOWN"),
    (dict(equity=None), "EQUITY_UNKNOWN"),
])
def test_sizing_pre_blocks(kw, reason):
    res = size_position(_inputs(**kw))
    assert not res.approved and reason in res.rejection_reasons


def test_sizing_binding_limits_and_illiquidity():
    st = market_state(asks=((101, "0.5"), (110, 50)), bids=((100.9, 50),))
    thin = size_position(_inputs(asks=st.book(SYMBOL).top_asks(25)))
    assert thin.approved and thin.limits_applied["binding"] == "liquidity_cap"   # capped, QA-04
    assert thin.quantity <= D("0.5")
    no_cash = size_position(_inputs(cash_free=D(0)))
    assert not no_cash.approved and no_cash.rejection_reasons == ["NO_ROOM_CASH_AVAILABLE"]
    capped = size_position(_inputs(policy=RiskPolicyConfig.model_validate(
        policy_config(max_token_exposure_pct="5"))))
    assert capped.limits_applied["binding"] == "max_token_exposure"
