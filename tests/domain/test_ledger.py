"""T-M1.1-01/02/03 — ledger balance, deposit vs PnL, FIFO sale postings (08-*/03, 10-backend/03)."""

from decimal import Decimal

import pytest

from app.domain.trading.enums import CashMovementKind, LedgerKind
from app.domain.trading.errors import LedgerImbalance, ValidationFailed
from app.domain.trading.ledger import (
    ACCOUNT_CASH,
    ACCOUNT_FEES,
    ACCOUNT_REALIZED_PNL,
    Posting,
    account_balances,
    buy_fill_postings,
    equity_summary,
    inventory_account,
    post_transaction,
    record_cash_movement,
    reserve_postings,
    sell_fill_postings,
)

from .factories import make_bot

pytestmark = pytest.mark.anyio
D = Decimal
INSTR = "kraken_spot:TEST/USD"


async def test_unbalanced_transaction_refused(db):
    bot = await make_bot(db)
    with pytest.raises(LedgerImbalance):
        await post_transaction(
            db, bot_id=bot.id, kind=LedgerKind.COMPENSATION,
            postings=[Posting(ACCOUNT_CASH, D("10")), Posting(ACCOUNT_FEES, D("-9.99"))],
        )
    with pytest.raises(LedgerImbalance):
        await post_transaction(
            db, bot_id=bot.id, kind=LedgerKind.COMPENSATION, postings=[Posting(ACCOUNT_CASH, D("1"))]
        )
    with pytest.raises(ValidationFailed):
        await post_transaction(
            db, bot_id=bot.id, kind=LedgerKind.COMPENSATION,
            postings=[Posting(ACCOUNT_CASH, D("0")), Posting(ACCOUNT_FEES, D("0"))],
        )


async def test_spec_buy_example_is_balanced(db):
    """08-*/03: buy 100 units @ $1, fee $1 → inventory +100, fees +1, cash −101."""
    bot = await make_bot(db)
    await record_cash_movement(
        db, bot_id=bot.id, kind=CashMovementKind.DEPOSIT, amount=D("1000"), actor="user:test"
    )
    await post_transaction(
        db, bot_id=bot.id, kind=LedgerKind.BUY_FILL,
        postings=buy_fill_postings(INSTR, notional=D("100"), fee=D("1")),
    )
    bal = await account_balances(db, bot.id)
    assert bal[(inventory_account(INSTR), "USD")] == D("100")
    assert bal[(ACCOUNT_FEES, "USD")] == D("1")
    assert bal[(ACCOUNT_CASH, "USD")] == D("899")
    assert sum(bal.values(), D(0)) == D(0)


async def test_deposit_raises_equity_not_pnl(db):
    """QA-07 / T-M1.1-02."""
    bot = await make_bot(db)
    before = await equity_summary(db, bot.id, positions_mark_value=None)
    assert before.equity == D(0)
    await record_cash_movement(
        db, bot_id=bot.id, kind=CashMovementKind.DEPOSIT, amount=D("10000"), actor="user:test"
    )
    after = await equity_summary(db, bot.id, positions_mark_value=None)
    assert after.equity == D("10000")
    assert after.realized_pnl_gross == D(0)
    assert after.owner_capital == D("10000")


async def test_sell_with_fifo_cost_matches_spec_table(db):
    """T-M1.1-03: sell 100 @ 1.2, fee 1.2, FIFO cost 100."""
    bot = await make_bot(db)
    await record_cash_movement(
        db, bot_id=bot.id, kind=CashMovementKind.DEPOSIT, amount=D("1000"), actor="user:test"
    )
    await post_transaction(
        db, bot_id=bot.id, kind=LedgerKind.BUY_FILL,
        postings=buy_fill_postings(INSTR, notional=D("100"), fee=D("1")),
    )
    postings = sell_fill_postings(INSTR, proceeds=D("120"), fee=D("1.2"), cost_released=D("100"))
    by_account = {p.account: p.amount for p in postings}
    assert by_account == {
        ACCOUNT_CASH: D("118.8"),
        ACCOUNT_FEES: D("1.2"),
        inventory_account(INSTR): D("-100"),
        ACCOUNT_REALIZED_PNL: D("-20"),
    }
    await post_transaction(db, bot_id=bot.id, kind=LedgerKind.SELL_FILL, postings=postings)
    summary = await equity_summary(db, bot.id, positions_mark_value=D(0))
    assert summary.realized_pnl_gross == D("20")
    assert summary.fees_total == D("2.2")
    assert summary.inventory_cost == D(0)
    assert summary.equity == D("1000") + D("20") - D("2.2")


async def test_equity_unknown_when_inventory_without_mark(db):
    """Missing mark ⇒ equity UNKNOWN (None), never cost or zero (design system « — »)."""
    bot = await make_bot(db)
    await record_cash_movement(
        db, bot_id=bot.id, kind=CashMovementKind.DEPOSIT, amount=D("500"), actor="user:test"
    )
    await post_transaction(
        db, bot_id=bot.id, kind=LedgerKind.RESERVE, postings=reserve_postings(D("200"))
    )
    await post_transaction(
        db, bot_id=bot.id, kind=LedgerKind.BUY_FILL,
        postings=buy_fill_postings(INSTR, notional=D("150"), fee=D("0.6"), from_reserved=True),
    )
    summary = await equity_summary(db, bot.id, positions_mark_value=None)
    assert summary.equity is None
    assert summary.cash_reserved == D("49.4")
