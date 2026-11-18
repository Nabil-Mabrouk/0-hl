"""Paper execution against the real order book (spec 06-agents/03 « EXECUTOR paper »).

A sweep consumes book levels best-first up to a quantity, never beyond the
limit price (IOC behaviour). Slippage is expressed as a limit price relative
to the best level.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal

ZERO = Decimal(0)

# Paper venue parameters (06-agents/03, ADR-010).
PAPER_TAKER_FEE = Decimal("0.0040")
PAPER_LATENCY_SECONDS = 0.25
EXIT_FLOOR_SLIPPAGE = Decimal("0.05")


@dataclass(frozen=True)
class SweepResult:
    filled_qty: Decimal
    notional: Decimal
    levels: list[tuple[Decimal, Decimal]] = field(default_factory=list)

    @property
    def avg_price(self) -> Decimal | None:
        return self.notional / self.filled_qty if self.filled_qty > ZERO else None


def floor_qty(qty: Decimal, precision: int) -> Decimal:
    return qty.quantize(Decimal(1).scaleb(-precision), rounding=ROUND_DOWN)


def sweep(
    levels: list[tuple[Decimal, Decimal]], qty: Decimal, *, limit: Decimal, side: str
) -> SweepResult:
    """Consume `levels` (best first) for `qty`; buy: price ≤ limit, sell: price ≥ limit."""
    remaining = qty
    notional = ZERO
    used: list[tuple[Decimal, Decimal]] = []
    for price, available in levels:
        if remaining <= ZERO:
            break
        if (side == "buy" and price > limit) or (side == "sell" and price < limit):
            break
        take = min(available, remaining)
        used.append((price, take))
        notional += price * take
        remaining -= take
    return SweepResult(filled_qty=qty - remaining, notional=notional, levels=used)


def qty_within(levels: list[tuple[Decimal, Decimal]], *, limit: Decimal, side: str) -> Decimal:
    total = ZERO
    for price, available in levels:
        if (side == "buy" and price > limit) or (side == "sell" and price < limit):
            break
        total += available
    return total


def liquidity_cap_notional(
    asks: list[tuple[Decimal, Decimal]],
    bids: list[tuple[Decimal, Decimal]],
    max_slippage: Decimal,
) -> Decimal:
    """Largest notional whose buy (asks) AND resale (bids) both stay within `max_slippage`."""
    if not asks or not bids:
        return ZERO
    ask0, bid0 = asks[0][0], bids[0][0]
    buy_qty = qty_within(asks, limit=ask0 * (1 + max_slippage), side="buy")
    sell_qty = qty_within(bids, limit=bid0 * (1 - max_slippage), side="sell")
    return min(buy_qty, sell_qty) * ask0
