"""T-M1.1-11 — RBAC: bootstrap of first admin, 403 without role, audit of denied attempts."""

import httpx
import pytest
from fastapi import Depends, FastAPI
from sqlalchemy import select

from app.core.auth.security import create_access_token
from app.core.database import get_db
from app.core.models import UserRole
from app.domain.trading.enums import TradingRole
from app.domain.trading.models import AuditEvent, RoleAssignment
from app.domain.trading.rbac import (
    TradingPrincipal,
    active_roles,
    grant_role,
    require_trading_roles,
    revoke_role,
)

from .factories import make_user

pytestmark = pytest.mark.anyio


def _app(session_factory) -> FastAPI:
    app = FastAPI()

    async def _db():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_db] = _db

    @app.get("/observe")
    async def observe(p: TradingPrincipal = Depends(require_trading_roles(TradingRole.OBSERVER))):
        return {"roles": sorted(r.value for r in p.roles)}

    @app.post("/publish")
    async def publish(
        p: TradingPrincipal = Depends(
            require_trading_roles(TradingRole.RISK_OFFICER, audit_action="STRATEGY_PUBLISH")
        ),
    ):
        return {"ok": True}

    return app


def _auth(user_id: int) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


async def _client(session_factory):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(session_factory)), base_url="http://t"
    )


async def test_first_admin_bootstraps_all_roles(session_factory):
    async with session_factory() as db:
        user = await make_user(db, "plain@x.io")
        admin = await make_user(db, "admin@x.io", UserRole.admin)
        await db.commit()
    async with await _client(session_factory) as c:
        r = await c.get("/observe", headers=_auth(user.id))
        assert r.status_code == 403  # plain user, no bootstrap
        r = await c.get("/observe", headers=_auth(admin.id))
        assert r.status_code == 200
        assert r.json()["roles"] == sorted(role.value for role in TradingRole)
    async with session_factory() as db:
        assert await db.scalar(
            select(AuditEvent).where(AuditEvent.action == "ROLES_BOOTSTRAPPED")
        ) is not None


async def test_second_admin_gets_nothing_after_bootstrap(session_factory):
    async with session_factory() as db:
        a1 = await make_user(db, "a1@x.io", UserRole.admin)
        a2 = await make_user(db, "a2@x.io", UserRole.admin)
        await db.commit()
    async with await _client(session_factory) as c:
        assert (await c.get("/observe", headers=_auth(a1.id))).status_code == 200
        assert (await c.get("/observe", headers=_auth(a2.id))).status_code == 403


async def test_denied_sensitive_action_is_audited(session_factory):
    async with session_factory() as db:
        admin = await make_user(db, "admin@x.io", UserRole.admin)
        observer = await make_user(db, "obs@x.io")
        await db.commit()
    async with await _client(session_factory) as c:
        assert (await c.get("/observe", headers=_auth(admin.id))).status_code == 200  # bootstrap
    async with session_factory() as db:
        principal = TradingPrincipal(admin.id, await active_roles(db, admin.id))
        await grant_role(db, granter=principal, user_id=observer.id, role=TradingRole.OBSERVER)
        await db.commit()
    async with await _client(session_factory) as c:
        assert (await c.get("/observe", headers=_auth(observer.id))).status_code == 200
        r = await c.post("/publish", headers=_auth(observer.id))
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "PERMISSION_DENIED"
    async with session_factory() as db:
        denied = await db.scalar(
            select(AuditEvent).where(AuditEvent.action == "STRATEGY_PUBLISH_DENIED")
        )
        assert denied is not None and denied.entity_id == str(observer.id)


async def test_unauthenticated_is_401(session_factory):
    async with await _client(session_factory) as c:
        assert (await c.get("/observe")).status_code == 401


async def test_revoke_role(session_factory):
    async with session_factory() as db:
        admin = await make_user(db, "admin@x.io", UserRole.admin)
        await db.commit()
    async with await _client(session_factory) as c:
        await c.get("/observe", headers=_auth(admin.id))
    async with session_factory() as db:
        principal = TradingPrincipal(admin.id, await active_roles(db, admin.id))
        await revoke_role(db, revoker=principal, user_id=admin.id, role=TradingRole.OBSERVER)
        await db.commit()
        assert TradingRole.OBSERVER not in await active_roles(db, admin.id)
        rows = (await db.scalars(select(RoleAssignment))).all()
        assert len(rows) == len(TradingRole)  # revocation keeps history
