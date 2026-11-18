"""Business RBAC on top of the chassis auth (ADR-006, spec 10-backend/03 « RBAC »).

Chassis `UserRole` (app/core) is never modified: trading roles live in
`hl_role_assignment`. Bootstrap: while no assignment exists at all, the first
chassis `admin` calling the trading API receives the five roles.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.dependencies import get_current_user
from app.core.database import get_db
from app.core.models import User, UserRole
from app.domain.trading.audit import record_audit, system_actor, user_actor
from app.domain.trading.enums import TradingRole
from app.domain.trading.errors import PermissionDenied, StateConflict
from app.domain.trading.events import append_event
from app.domain.trading.models import RoleAssignment
from app.domain.trading.types import utcnow


@dataclass(frozen=True)
class TradingPrincipal:
    user_id: int
    roles: frozenset[TradingRole]

    @property
    def actor(self) -> str:
        return user_actor(self.user_id)

    def has_any(self, *roles: TradingRole) -> bool:
        return bool(self.roles & set(roles))


async def active_roles(db: AsyncSession, user_id: int) -> frozenset[TradingRole]:
    rows = await db.scalars(
        select(RoleAssignment.role).where(
            RoleAssignment.user_id == user_id, RoleAssignment.revoked_at.is_(None)
        )
    )
    return frozenset(rows.all())


async def ensure_bootstrap(db: AsyncSession, user: User) -> bool:
    """Grant all roles to the first chassis admin if no assignment exists yet."""
    if user.role != UserRole.admin:
        return False
    existing = await db.scalar(select(func.count()).select_from(RoleAssignment))
    if existing:
        return False
    for role in TradingRole:
        db.add(RoleAssignment(user_id=user.id, role=role, granted_by=None, granted_at=utcnow()))
    await db.flush()
    await record_audit(
        db, actor=system_actor("rbac"), action="ROLES_BOOTSTRAPPED", entity_type="user",
        entity_id=str(user.id), diff={"roles": [r.value for r in TradingRole]},
        reason="ADR-006 bootstrap: first chassis admin",
    )
    await append_event(
        db, event_type="ROLES_BOOTSTRAPPED", aggregate_type="user", aggregate_id=str(user.id),
        source="api", payload={"user_id": user.id, "roles": [r.value for r in TradingRole]},
    )
    await db.commit()
    return True


async def grant_role(
    db: AsyncSession, *, granter: TradingPrincipal, user_id: int, role: TradingRole
) -> RoleAssignment:
    if not granter.has_any(TradingRole.ADMIN):
        raise PermissionDenied("only ADMIN can grant trading roles")
    if role in await active_roles(db, user_id):
        raise StateConflict(f"user {user_id} already has role {role.value}")
    assignment = RoleAssignment(
        user_id=user_id, role=role, granted_by=granter.user_id, granted_at=utcnow()
    )
    db.add(assignment)
    await db.flush()
    await record_audit(
        db, actor=granter.actor, action="ROLE_GRANTED", entity_type="user",
        entity_id=str(user_id), diff={"role": role.value},
    )
    return assignment


async def revoke_role(
    db: AsyncSession, *, revoker: TradingPrincipal, user_id: int, role: TradingRole
) -> None:
    if not revoker.has_any(TradingRole.ADMIN):
        raise PermissionDenied("only ADMIN can revoke trading roles")
    assignment = await db.scalar(
        select(RoleAssignment).where(
            RoleAssignment.user_id == user_id,
            RoleAssignment.role == role,
            RoleAssignment.revoked_at.is_(None),
        )
    )
    if assignment is None:
        raise StateConflict(f"user {user_id} has no active role {role.value}")
    assignment.revoked_at = utcnow()
    assignment.revoked_by = revoker.user_id
    await db.flush()
    await record_audit(
        db, actor=revoker.actor, action="ROLE_REVOKED", entity_type="user",
        entity_id=str(user_id), diff={"role": role.value},
    )


def require_trading_roles(
    *roles: TradingRole, audit_action: str | None = None
) -> Callable[..., Awaitable[TradingPrincipal]]:
    """FastAPI dependency: authenticated chassis user holding at least one of `roles`.

    A refusal returns 403 `PERMISSION_DENIED`; when `audit_action` is given
    (sensitive actions), the attempt is audited (QA-13).
    """
    wanted = frozenset(roles)

    async def dependency(
        user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
    ) -> TradingPrincipal:
        await ensure_bootstrap(db, user)
        held = await active_roles(db, user.id)
        if not held & wanted:
            if audit_action:
                await record_audit(
                    db, actor=user_actor(user.id), action=f"{audit_action}_DENIED",
                    entity_type="user", entity_id=str(user.id),
                    diff={"required_any": sorted(r.value for r in wanted),
                          "held": sorted(r.value for r in held)},
                )
                await db.commit()
            raise HTTPException(
                status_code=403,
                detail={
                    "code": PermissionDenied.code,
                    "message": "missing trading role",
                    "required_any": sorted(r.value for r in wanted),
                },
            )
        return TradingPrincipal(user_id=user.id, roles=held)

    return dependency
