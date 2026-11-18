"""T-M1.1-04 — Dec refuses float and keeps 18 decimals; UtcDateTime refuses naive datetimes."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.exc import StatementError

from app.domain.trading.enums import CashMovementKind
from app.domain.trading.ledger import record_cash_movement
from app.domain.trading.models import CashMovement, Instrument
from app.domain.trading.types import canonical_json, sha256_hex, to_decimal, to_jsonable

from .factories import make_bot

pytestmark = pytest.mark.anyio


def test_to_decimal_refuses_float_and_bool():
    with pytest.raises(TypeError):
        to_decimal(0.1)
    with pytest.raises(TypeError):
        to_decimal(True)
    assert to_decimal("0.1") + to_decimal("0.2") == Decimal("0.3")
    with pytest.raises(ValueError):
        to_decimal("NaN")


def test_json_refuses_float_and_hash_is_canonical():
    with pytest.raises(TypeError):
        to_jsonable({"x": 1.5})
    a = {"b": Decimal("1.50"), "a": [1, "x"]}
    b = {"a": [1, "x"], "b": Decimal("1.50")}
    assert canonical_json(a) == canonical_json(b)
    assert sha256_hex(a) == sha256_hex(b)


async def test_dec_round_trip_keeps_18_decimals(db):
    bot = await make_bot(db)
    tiny = Decimal("12345.123456789012345678")
    mv = await record_cash_movement(
        db, bot_id=bot.id, kind=CashMovementKind.DEPOSIT, amount=tiny, actor="user:test"
    )
    await db.commit()
    db.expunge_all()
    loaded = await db.get(CashMovement, mv.id)
    assert loaded.amount == tiny
    assert isinstance(loaded.amount, Decimal)


async def test_dec_column_refuses_float(db):
    db.add(
        Instrument(
            id="kraken_spot:X/USD", venue="kraken_spot", symbol="X/USD", base="X", quote="USD",
            pair_decimals=2, lot_decimals=8, status="online", ordermin=0.5,
        )
    )
    with pytest.raises((TypeError, StatementError)):
        await db.flush()


async def test_utc_datetime_refuses_naive_and_returns_aware(db):
    db.add(
        Instrument(
            id="kraken_spot:Y/USD", venue="kraken_spot", symbol="Y/USD", base="Y", quote="USD",
            pair_decimals=2, lot_decimals=8, status="online", updated_at=datetime(2026, 1, 1),
        )
    )
    with pytest.raises((ValueError, StatementError)):
        await db.flush()
    await db.rollback()

    aware = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    db.add(
        Instrument(
            id="kraken_spot:Z/USD", venue="kraken_spot", symbol="Z/USD", base="Z", quote="USD",
            pair_decimals=2, lot_decimals=8, status="online", updated_at=aware,
        )
    )
    await db.commit()
    db.expunge_all()
    loaded = await db.get(Instrument, "kraken_spot:Z/USD")
    assert loaded.updated_at == aware
    assert loaded.updated_at.tzinfo is not None
