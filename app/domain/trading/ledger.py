"""Double-entry ledger — spec 08-risque-execution/03 and 10-backend/03 « Ledger ».

Sign convention: debit positive, credit negative. For every
(transaction, asset) the sum of amounts is exactly zero, otherwise the
transaction is refused. The ledger is the single accounting write point
(INV-02); balances and equity are projections. Sums are computed in Python
with `Decimal` (SQLite stores decimals as text).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.trading.enums import CashMovementKind, LedgerKind
from app.domain.trading.errors import LedgerImbalance, ValidationFailed
from app.domain.trading.models import CashMovement, LedgerEntry, LedgerTransaction
from app.domain.trading.types import to_decimal, utcnow

USD = "USD"
ZERO = Decimal(0)

ACCOUNT_CASH = "cash"
ACCOUNT_CASH_RESERVED = "cash_reserved"
ACCOUNT_FEES = "fees"
ACCOUNT_REALIZED_PNL = "realized_pnl"
ACCOUNT_OWNER_CAPITAL = "owner_capital"
INVENTORY_PREFIX = "inventory:"


def inventory_account(instrument_id: str) -> str:
    return f"{INVENTORY_PREFIX}{instrument_id}"


@dataclass(frozen=True)
class Posting:
    account: str
    amount: Decimal
    asset: str = USD


async def post_transaction(
    db: AsyncSession,
    *,
    bot_id: uuid.UUID,
    kind: LedgerKind,
    postings: list[Posting],
    occurred_at: datetime | None = None,
    source_event_id: uuid.UUID | None = None,
    description: str = "",
) -> LedgerTransaction:
    if len(postings) < 2:
        raise LedgerImbalance("a ledger transaction needs at least two postings")
    totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for p in postings:
        amount = to_decimal(p.amount)
        if amount == ZERO:
            raise ValidationFailed(f"zero posting on account {p.account}")
        totals[p.asset] += amount
    unbalanced = {asset: total for asset, total in totals.items() if total != ZERO}
    if unbalanced:
        raise LedgerImbalance(
            "unbalanced ledger transaction",
            details={asset: format(total, "f") for asset, total in unbalanced.items()},
        )

    tx = LedgerTransaction(
        bot_id=bot_id,
        kind=kind,
        occurred_at=occurred_at or utcnow(),
        source_event_id=source_event_id,
        description=description,
    )
    db.add(tx)
    await db.flush()
    for p in postings:
        db.add(
            LedgerEntry(
                transaction_id=tx.id,
                bot_id=bot_id,
                account_code=p.account,
                asset=p.asset,
                amount=to_decimal(p.amount),
            )
        )
    await db.flush()
    return tx


async def account_balances(db: AsyncSession, bot_id: uuid.UUID) -> dict[tuple[str, str], Decimal]:
    rows = await db.execute(
        select(LedgerEntry.account_code, LedgerEntry.asset, LedgerEntry.amount).where(
            LedgerEntry.bot_id == bot_id
        )
    )
    balances: dict[tuple[str, str], Decimal] = defaultdict(lambda: ZERO)
    for account, asset, amount in rows:
        balances[(account, asset)] += amount
    return dict(balances)


async def balance(db: AsyncSession, bot_id: uuid.UUID, account: str, asset: str = USD) -> Decimal:
    return (await account_balances(db, bot_id)).get((account, asset), ZERO)


# --------------------------------------------------------------------------- #
# Standard postings (table « Opération / Écritures » of 10-backend/03)
# --------------------------------------------------------------------------- #
def deposit_postings(amount: Decimal) -> list[Posting]:
    return [Posting(ACCOUNT_CASH, amount), Posting(ACCOUNT_OWNER_CAPITAL, -amount)]


def reserve_postings(amount: Decimal) -> list[Posting]:
    return [Posting(ACCOUNT_CASH, -amount), Posting(ACCOUNT_CASH_RESERVED, amount)]


def release_postings(amount: Decimal) -> list[Posting]:
    return [Posting(ACCOUNT_CASH_RESERVED, -amount), Posting(ACCOUNT_CASH, amount)]


def buy_fill_postings(
    instrument_id: str, notional: Decimal, fee: Decimal, *, from_reserved: bool = False
) -> list[Posting]:
    """Buy: inventory at cost (+notional), fees (+fee), cash or reserved cash (−total)."""
    source = ACCOUNT_CASH_RESERVED if from_reserved else ACCOUNT_CASH
    postings = [Posting(inventory_account(instrument_id), notional)]
    if fee != ZERO:
        postings.append(Posting(ACCOUNT_FEES, fee))
    postings.append(Posting(source, -(notional + fee)))
    return postings


def sell_fill_postings(
    instrument_id: str, proceeds: Decimal, fee: Decimal, cost_released: Decimal
) -> list[Posting]:
    """Sell: cash (+proceeds−fee), fees (+fee), inventory (−cost), realized PnL (−(proceeds−cost))."""
    postings = [Posting(ACCOUNT_CASH, proceeds - fee)]
    if fee != ZERO:
        postings.append(Posting(ACCOUNT_FEES, fee))
    postings.append(Posting(inventory_account(instrument_id), -cost_released))
    gross_pnl = proceeds - cost_released
    if gross_pnl != ZERO:
        postings.append(Posting(ACCOUNT_REALIZED_PNL, -gross_pnl))
    return postings


async def record_cash_movement(
    db: AsyncSession,
    *,
    bot_id: uuid.UUID,
    kind: CashMovementKind,
    amount: Decimal,
    actor: str,
    reason: str | None = None,
    asset: str = USD,
) -> CashMovement:
    """Capital flow (deposit/withdrawal): changes equity, never PnL (QA-07)."""
    amount = to_decimal(amount)
    if amount <= ZERO:
        raise ValidationFailed("cash movement amount must be > 0")
    if kind is CashMovementKind.DEPOSIT:
        postings, ledger_kind = deposit_postings(amount), LedgerKind.DEPOSIT
    else:
        postings = [Posting(ACCOUNT_CASH, -amount), Posting(ACCOUNT_OWNER_CAPITAL, amount)]
        ledger_kind = LedgerKind.WITHDRAWAL
    tx = await post_transaction(
        db, bot_id=bot_id, kind=ledger_kind, postings=postings, description=reason or kind.value
    )
    movement = CashMovement(
        bot_id=bot_id,
        kind=kind,
        asset=asset,
        amount=amount,
        actor=actor,
        reason=reason,
        ledger_transaction_id=tx.id,
        occurred_at=tx.occurred_at,
    )
    db.add(movement)
    await db.flush()
    return movement


@dataclass(frozen=True)
class EquitySummary:
    cash: Decimal
    cash_reserved: Decimal
    inventory_cost: Decimal
    positions_mark_value: Decimal | None
    equity: Decimal | None
    realized_pnl_gross: Decimal
    fees_total: Decimal
    owner_capital: Decimal


async def equity_summary(
    db: AsyncSession, bot_id: uuid.UUID, *, positions_mark_value: Decimal | None
) -> EquitySummary:
    """equity = cash + cash_reserved + positions mark value; `None` mark ⇒ equity unknown.

    `positions_mark_value` must be supplied by the caller (EQUITY agent) from a
    conservative liquidation estimate; it is never derived from cost here.
    """
    balances = await account_balances(db, bot_id)

    def bal(account: str) -> Decimal:
        return balances.get((account, USD), ZERO)

    inventory_cost = sum(
        (amount for (account, asset), amount in balances.items()
         if account.startswith(INVENTORY_PREFIX) and asset == USD),
        ZERO,
    )
    cash, reserved = bal(ACCOUNT_CASH), bal(ACCOUNT_CASH_RESERVED)
    has_inventory = inventory_cost != ZERO
    if positions_mark_value is None and has_inventory:
        equity = None
    else:
        equity = cash + reserved + (positions_mark_value or ZERO)
    return EquitySummary(
        cash=cash,
        cash_reserved=reserved,
        inventory_cost=inventory_cost,
        positions_mark_value=positions_mark_value,
        equity=equity,
        realized_pnl_gross=-bal(ACCOUNT_REALIZED_PNL),
        fees_total=bal(ACCOUNT_FEES),
        owner_capital=-bal(ACCOUNT_OWNER_CAPITAL),
    )
