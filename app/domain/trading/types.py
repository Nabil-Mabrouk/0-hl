"""Portable column types and helpers (spec 10-backend/03 « Conventions techniques »).

The domain runs on PostgreSQL (production) and SQLite (dev/tests). Money and
quantities are always `Decimal`, timestamps always timezone-aware UTC.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import JSON, DateTime, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator

NUMERIC_PRECISION = 38
NUMERIC_SCALE = 18


class Dec(TypeDecorator):
    """Exact decimal: NUMERIC(38,18) on PostgreSQL, canonical decimal TEXT on SQLite.

    Rejects `float` (and `bool`) on write: accounting never goes through binary
    floating point (00-guide/00-lecture « Conventions »).
    """

    impl = Numeric(NUMERIC_PRECISION, NUMERIC_SCALE)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(80))
        return dialect.type_descriptor(
            Numeric(NUMERIC_PRECISION, NUMERIC_SCALE, asdecimal=True)
        )

    def process_bind_param(self, value: Any, dialect) -> Any:
        if value is None:
            return None
        value = to_decimal(value)
        if dialect.name == "sqlite":
            return format(value, "f")
        return value

    def process_result_value(self, value: Any, dialect) -> Decimal | None:
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        return Decimal(str(value))


class UtcDateTime(TypeDecorator):
    """Timezone-aware UTC datetime on every dialect; naive datetimes are refused."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise TypeError(f"UtcDateTime expects datetime, got {type(value).__name__}")
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("naive datetime refused: business timestamps are UTC-aware")
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


JsonType = JSON().with_variant(JSONB(), "postgresql")


def to_decimal(value: Any) -> Decimal:
    """Strict Decimal coercion: accepts Decimal, int and decimal strings; never float."""
    if isinstance(value, bool) or isinstance(value, float):
        raise TypeError("float/bool refused for money or quantity: use Decimal or str")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, str):
        result = Decimal(value)
    else:
        raise TypeError(f"cannot convert {type(value).__name__} to Decimal")
    if not result.is_finite():
        raise ValueError("non-finite decimal refused")
    return result


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> uuid.UUID:
    return uuid.uuid4()


def to_jsonable(value: Any) -> Any:
    """Convert domain values to JSON-safe ones (Decimal → str, never float)."""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("naive datetime refused in JSON payload")
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, float):
        raise TypeError("float refused in domain JSON payloads: use Decimal or str")
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        to_jsonable(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
