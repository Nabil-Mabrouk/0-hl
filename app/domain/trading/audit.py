"""Append-only audit trail (INV-07, spec 10-backend/03 « Gouvernance »)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.trading.models import AuditEvent
from app.domain.trading.types import to_jsonable, utcnow


def user_actor(user_id: int) -> str:
    return f"user:{user_id}"


def system_actor(component: str) -> str:
    return f"system:{component}"


async def record_audit(
    db: AsyncSession,
    *,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str | uuid.UUID,
    diff: dict[str, Any] | None = None,
    reason: str | None = None,
    self_approved: bool = False,
    correlation_id: uuid.UUID | None = None,
) -> AuditEvent:
    entry = AuditEvent(
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        diff=to_jsonable(diff or {}),
        reason=reason,
        self_approved=self_approved,
        correlation_id=correlation_id,
        occurred_at=utcnow(),
    )
    db.add(entry)
    await db.flush()
    return entry
