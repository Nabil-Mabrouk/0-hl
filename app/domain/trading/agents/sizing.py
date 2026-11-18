"""SIZER — risk-based position sizing (spec 06-agents/03 « SIZER », 05-strategies/03).

notional_approved = min(notional_risk, Q, cash_available, liquidity_cap, portfolio_room)
with notional_risk = (E × r) / max(S, s_min). Pure function, Decimal only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.domain.trading.agents.sim import floor_qty, liquidity_cap_notional
from app.domain.trading.config_schemas import RiskPolicyConfig

ZERO = Decimal(0)
S_MIN = Decimal("0.005")


@dataclass(frozen=True)
class SizingInputs:
    equity: Decimal | None
    cash_free: Decimal
    open_positions: int
    open_exposure_cost: Decimal
    asks: list[tuple[Decimal, Decimal]]
    bids: list[tuple[Decimal, Decimal]]
    qty_precision: int
    qty_min: Decimal | None
    cost_min: Decimal | None
    policy: RiskPolicyConfig
    initial_stop_pct: Decimal
    taker_fee: Decimal
    daily_loss: Decimal | None = None   # fraction (0.02 = −2 %), None = unknown
    drawdown: Decimal | None = None     # fraction


@dataclass
class SizingResult:
    approved: bool
    notional_requested: Decimal | None = None
    notional_approved: Decimal | None = None
    quantity: Decimal | None = None
    reference_price: Decimal | None = None
    stop_price: Decimal | None = None
    risk_budget: Decimal | None = None
    limits_applied: dict[str, Any] = field(default_factory=dict)
    rejection_reasons: list[str] = field(default_factory=list)


def _pct(policy: RiskPolicyConfig, name: str) -> Decimal | None:
    v = policy.dec(name)
    return None if v is None else v / 100


def size_position(inp: SizingInputs) -> SizingResult:
    p = inp.policy
    res = SizingResult(approved=False)
    reasons = res.rejection_reasons

    # pre-blocks (no computation)
    if p.kill_switch:
        reasons.append("KILL_SWITCH")
    if p.max_open_positions is not None and inp.open_positions >= p.max_open_positions:
        reasons.append("MAX_OPEN_POSITIONS")
    max_daily = _pct(p, "max_daily_loss_pct")
    if max_daily is not None and inp.daily_loss is not None and inp.daily_loss >= max_daily:
        reasons.append("MAX_DAILY_LOSS")
    max_dd = _pct(p, "max_drawdown_pct")
    if max_dd is not None and inp.drawdown is not None and inp.drawdown >= max_dd:
        reasons.append("MAX_DRAWDOWN")
    if inp.equity is None or inp.equity <= ZERO:
        reasons.append("EQUITY_UNKNOWN")
    if not inp.asks or not inp.bids:
        reasons.append("EMPTY_BOOK")
    if reasons:
        return res

    E = inp.equity  # type: ignore[assignment]
    ask0 = inp.asks[0][0]
    r = _pct(p, "max_trade_risk_pct") or ZERO
    stop = inp.initial_stop_pct / 100
    S = max(stop + 2 * inp.taker_fee, S_MIN)
    risk_budget = E * r
    notional_risk = risk_budget / S
    limits = {
        "notional_risk": notional_risk,
        "max_token_exposure": E * (_pct(p, "max_token_exposure_pct") or ZERO),
        "cash_available": inp.cash_free / (1 + inp.taker_fee),
        "liquidity_cap": liquidity_cap_notional(inp.asks, inp.bids, _pct(p, "max_slippage_pct") or ZERO),
        "portfolio_room": E * (_pct(p, "max_portfolio_exposure_pct") or ZERO) - inp.open_exposure_cost,
    }
    binding = min(limits, key=lambda k: limits[k])
    notional = max(limits[binding], ZERO)
    qty = floor_qty(notional / ask0, inp.qty_precision)

    res.notional_requested = notional_risk
    res.reference_price = ask0
    res.stop_price = ask0 * (1 - stop)
    res.risk_budget = risk_budget
    res.limits_applied = {k: format(v, "f") for k, v in limits.items()} | {
        "binding": binding, "S": format(S, "f"), "risk_fraction": format(r, "f")}

    if qty <= ZERO or (inp.qty_min is not None and qty < inp.qty_min):
        reasons.append("BELOW_MIN_QTY" if limits[binding] > ZERO else f"NO_ROOM_{binding.upper()}")
        return res
    if inp.cost_min is not None and qty * ask0 < inp.cost_min:
        reasons.append("BELOW_MIN_COST")
        return res

    res.approved = True
    res.quantity = qty
    res.notional_approved = qty * ask0
    return res
