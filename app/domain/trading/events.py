"""Event journal (PostgreSQL outbox) — spec 10-backend/03 « Journal d'événements ».

Appends take a global journal write lock for the duration of the transaction
(`pg_advisory_xact_lock` on PostgreSQL; SQLite already serialises writers), so
the order of `position` is the commit order and a consumer reading
`position > cursor` never skips an event committed earlier.
The caller owns the transaction (append never commits).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.trading.errors import StateConflict
from app.domain.trading.models import ConsumerCursor, DomainEvent
from app.domain.trading.types import sha256_hex, to_jsonable, utcnow

# Arbitrary constant identifying the 0-hl journal write lock.
JOURNAL_LOCK_KEY = 0x0_1A_EE_70


async def _acquire_journal_lock(db: AsyncSession) -> None:
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": JOURNAL_LOCK_KEY})


def event_checksum(
    event_type: str, aggregate_type: str, aggregate_id: str, aggregate_sequence: int, payload: Any
) -> str:
    return sha256_hex(
        {
            "event_type": event_type,
            "aggregate_type": aggregate_type,
            "aggregate_id": aggregate_id,
            "aggregate_sequence": aggregate_sequence,
            "payload": payload,
        }
    )


async def append_event(
    db: AsyncSession,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str | uuid.UUID,
    payload: dict[str, Any],
    source: str,
    correlation_id: uuid.UUID | None = None,
    causation_id: uuid.UUID | None = None,
    bot_id: uuid.UUID | None = None,
    event_id: uuid.UUID | None = None,
    occurred_at: datetime | None = None,
    schema_version: int = 1,
) -> DomainEvent:
    """Append an event; an already-known `event_id` returns the stored event (idempotent)."""
    if event_id is not None:
        existing = await db.scalar(select(DomainEvent).where(DomainEvent.event_id == event_id))
        if existing is not None:
            return existing

    await _acquire_journal_lock(db)
    aggregate_id = str(aggregate_id)
    last_seq = await db.scalar(
        select(func.max(DomainEvent.aggregate_sequence)).where(
            DomainEvent.aggregate_type == aggregate_type,
            DomainEvent.aggregate_id == aggregate_id,
        )
    )
    sequence = (last_seq or 0) + 1
    clean_payload = to_jsonable(payload)
    new_event_id = event_id or uuid.uuid4()
    event = DomainEvent(
        event_id=new_event_id,
        schema_version=schema_version,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        aggregate_sequence=sequence,
        correlation_id=correlation_id or new_event_id,
        causation_id=causation_id,
        bot_id=bot_id,
        occurred_at=occurred_at or utcnow(),
        received_at=utcnow(),
        source=source,
        payload=clean_payload,
        checksum=event_checksum(event_type, aggregate_type, aggregate_id, sequence, clean_payload),
    )
    db.add(event)
    await db.flush()
    return event


def verify_checksum(event: DomainEvent) -> bool:
    return event.checksum == event_checksum(
        event.event_type,
        event.aggregate_type,
        event.aggregate_id,
        event.aggregate_sequence,
        event.payload,
    )


async def read_events_after(
    db: AsyncSession,
    position: int,
    *,
    limit: int = 500,
    bot_id: uuid.UUID | None = None,
    event_types: Sequence[str] | None = None,
) -> list[DomainEvent]:
    stmt = select(DomainEvent).where(DomainEvent.position > position)
    if bot_id is not None:
        stmt = stmt.where(DomainEvent.bot_id == bot_id)
    if event_types:
        stmt = stmt.where(DomainEvent.event_type.in_(list(event_types)))
    stmt = stmt.order_by(DomainEvent.position).limit(limit)
    return list((await db.scalars(stmt)).all())


async def get_cursor(db: AsyncSession, consumer: str) -> int:
    cursor = await db.get(ConsumerCursor, consumer)
    return cursor.position if cursor else 0


async def advance_cursor(db: AsyncSession, consumer: str, position: int) -> None:
    """Move a consumer cursor forward, in the caller's transaction. Never backwards."""
    cursor = await db.get(ConsumerCursor, consumer)
    if cursor is None:
        db.add(ConsumerCursor(consumer=consumer, position=position, updated_at=utcnow()))
    else:
        if position < cursor.position:
            raise StateConflict(
                f"cursor {consumer} cannot move backwards ({cursor.position} -> {position})"
            )
        cursor.position = position
        cursor.updated_at = utcnow()
    await db.flush()
