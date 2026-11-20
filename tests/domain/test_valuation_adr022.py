"""ADR-022 — a position beyond the visible book depth is valued at the exit floor, never zero."""

from decimal import Decimal

from app.domain.trading.agents.equity import liquidation_breakdown, liquidation_value
from app.domain.trading.agents.sim import EXIT_FLOOR_SLIPPAGE, PAPER_TAKER_FEE

from .agent_fakes import IID, market_state

D = Decimal
FEE = 1 - PAPER_TAKER_FEE


def test_thin_book_values_the_rest_at_the_exit_floor():
    # 2026-11-20 server case: ~109 USD visible on BTC/USD for a ~1 300 USD position
    st = market_state(bids=((100, "0.5"), (99.9, "0.5")), asks=((100.1, 50),))
    value, estimated = liquidation_breakdown(D(13), st, IID)
    floor = D(100) * (1 - EXIT_FLOOR_SLIPPAGE)
    visible = D(100) * D("0.5") + D("99.9") * D("0.5")
    assert estimated == D(12) * floor * FEE
    assert value == (visible + D(12) * floor) * FEE
    assert value > D(13) * D(100) * D("0.9")                 # was ~1 % of the position before


def test_deep_book_is_unchanged_and_levels_below_the_floor_are_not_used():
    st = market_state(bids=((100, 10), (99, 10)), asks=((100.1, 50),))
    assert liquidation_breakdown(D(5), st, IID) == (D(500) * FEE, D(0))
    deep_low = market_state(bids=((100, 1), (90, 100)), asks=((100.1, 50),))   # 90 < floor 95
    value, estimated = liquidation_breakdown(D(3), deep_low, IID)
    assert estimated == D(2) * D(95) * FEE and value == (D(100) + D(190)) * FEE


def test_stale_book_stays_unknown():
    assert liquidation_value(D(1), market_state(fresh=False), IID) is None
