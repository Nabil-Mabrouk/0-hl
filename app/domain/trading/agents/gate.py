"""RUG CHECK — market-quality gate, CEX variant (ADR-004, spec 06-agents/03).

Each criterion is PASS / FAIL / UNKNOWN with value, threshold and source.
Any FAIL or UNKNOWN ⇒ REJECT.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from app.domain.trading.agents.sim import qty_within
from app.domain.trading.config_schemas import RiskPolicyConfig
from app.domain.trading.market.book import OrderBook
from app.domain.trading.market.state import InstrumentRules

PASS, FAIL, UNKNOWN = "PASS", "FAIL", "UNKNOWN"


@dataclass(frozen=True)
class Criterion:
    name: str
    result: str
    value: Any = None
    threshold: Any = None
    unit: str | None = None
    source: str = "kraken_spot"

    def as_json(self, observed_at: datetime) -> dict[str, Any]:
        def s(x):
            return format(x, "f") if isinstance(x, Decimal) else x
        return {"result": self.result, "value": s(self.value), "threshold": s(self.threshold),
                "unit": self.unit, "source": self.source, "observed_at": observed_at.isoformat()}


def evaluate_market_gate(
    *,
    rules: InstrumentRules | None,
    fresh: bool,
    book: OrderBook | None,
    policy: RiskPolicyConfig,
    price_change_5m: Decimal | None,
    volume_24h_usd: Decimal | None,
    last_exit_at: datetime | None,
    cooldown_seconds: int,
    now: datetime,
) -> tuple[str, list[Criterion]]:
    crit: list[Criterion] = []

    status = rules.status if rules else None
    crit.append(Criterion("pair_status", PASS if status == "online" else (UNKNOWN if status is None else FAIL),
                          status, "online"))
    crit.append(Criterion("data_freshness", PASS if fresh else FAIL, "LIVE" if fresh else "NOT_LIVE", "LIVE"))

    max_spread = policy.dec("max_spread_bps")
    spread = book.spread_bps() if (book and fresh) else None
    if spread is None or max_spread is None:
        crit.append(Criterion("spread", UNKNOWN, spread, max_spread, "bps"))
    else:
        crit.append(Criterion("spread", PASS if spread <= max_spread else FAIL, spread, max_spread, "bps"))

    slip = (policy.dec("max_slippage_pct") or Decimal(0)) / 100
    if not (book and fresh and rules and book.best_ask and book.best_bid):
        crit.append(Criterion("min_order_liquidity", UNKNOWN, None, None, "base"))
    else:
        ask0, bid0 = book.best_ask[0], book.best_bid[0]
        min_qty = max(rules.qty_min or Decimal(0), (rules.cost_min or Decimal(0)) / ask0)
        buy_ok = qty_within(book.top_asks(book.depth), limit=ask0 * (1 + slip), side="buy") >= min_qty
        sell_ok = qty_within(book.top_bids(book.depth), limit=bid0 * (1 - slip), side="sell") >= min_qty
        crit.append(Criterion("min_order_liquidity", PASS if buy_ok and sell_ok else FAIL,
                              min_qty, "fills within max_slippage_pct", "base"))

    max_move = policy.dec("max_abnormal_move_pct")
    if price_change_5m is None or max_move is None:
        crit.append(Criterion("abnormal_move", UNKNOWN, price_change_5m, max_move, "log_return"))
    else:
        bound = (1 + max_move / 100).ln()
        crit.append(Criterion("abnormal_move", PASS if abs(price_change_5m) <= bound else FAIL,
                              price_change_5m, bound, "log_return"))

    floor = policy.dec("min_volume_24h_usd")
    if floor is None:
        crit.append(Criterion("volume_floor", PASS, volume_24h_usd, None, "USD"))
    elif volume_24h_usd is None:
        crit.append(Criterion("volume_floor", UNKNOWN, None, floor, "USD"))
    else:
        crit.append(Criterion("volume_floor", PASS if volume_24h_usd >= floor else FAIL,
                              volume_24h_usd, floor, "USD"))

    in_cooldown = last_exit_at is not None and now - last_exit_at < timedelta(seconds=cooldown_seconds)
    crit.append(Criterion("cooldown", FAIL if in_cooldown else PASS,
                          last_exit_at.isoformat() if last_exit_at else None, cooldown_seconds, "s",
                          source="hl_position"))

    verdict = "PASS" if all(c.result == PASS for c in crit) else "REJECT"
    return verdict, crit
