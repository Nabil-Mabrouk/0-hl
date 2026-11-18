"""T-M1.4-08 — terminal WebSocket: origin, auth, snapshot, push, resume, backlog."""

import asyncio
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.websockets import WebSocketDisconnect

from app.core.auth.security import create_access_token
from app.core.database import Base
from app.core.models import UserRole
from app.domain.routers import terminal_ws_router
from app.domain.trading.api import ws as ws_mod
from app.domain.trading.events import append_event
from app.domain.trading.seed import run_seed

from .factories import make_user

ORIGIN = {"origin": "http://localhost:5173"}


@pytest.fixture
def env(tmp_path):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'ws.db').as_posix()}"
    engine = create_async_engine(url, poolclass=NullPool)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with sessions() as db:
            seed = await run_seed(db)
            admin = await make_user(db, "admin@x.io", UserRole.admin)
            plain = await make_user(db, "plain@x.io")
            await db.commit()
        # bootstrap roles for the admin (ADR-006) through the RBAC helper
        from app.domain.trading.rbac import ensure_bootstrap
        async with sessions() as db:
            from app.core.models import User
            await ensure_bootstrap(db, await db.get(User, admin.id))
        return seed.bot.id, admin.id, plain.id

    bot_id, admin_id, plain_id = asyncio.run(setup())
    app = FastAPI()
    app.include_router(terminal_ws_router)
    app.dependency_overrides[ws_mod.get_session_factory] = lambda: sessions
    app.dependency_overrides[ws_mod.get_allowed_origins] = lambda: {ORIGIN["origin"]}
    app.dependency_overrides[ws_mod.get_ws_timing] = lambda: {
        "poll": 0.05, "heartbeat": 0.3, "hello_timeout": 0.5}

    def add_events(n: int, event_type: str = "TEST_EVENT", bot=None):
        async def _go():
            async with sessions() as db:
                for i in range(n):
                    await append_event(db, event_type=event_type, aggregate_type="test",
                                       aggregate_id=f"a{i}", payload={"i": str(i)}, source="test",
                                       bot_id=bot or bot_id)
                await db.commit()
        asyncio.run(_go())

    class Env:
        pass

    e = Env()
    e.client, e.bot_id, e.admin, e.plain, e.add_events = TestClient(app), bot_id, admin_id, plain_id, add_events
    yield e
    asyncio.run(engine.dispose())


def _hello(env, **kw):
    return {"type": "hello", "token": create_access_token(env.admin), "bot_id": str(env.bot_id),
            "last_position": None, **kw}


def _closed_code(ws) -> int:
    with pytest.raises(WebSocketDisconnect) as exc:
        while True:
            ws.receive_json()
    return exc.value.code


def test_foreign_origin_is_refused(env):
    with env.client.websocket_connect("/ws/v1/terminal", headers={"origin": "https://evil.example"}) as ws:
        assert _closed_code(ws) == 4403


def test_bad_or_missing_token_and_role(env):
    with env.client.websocket_connect("/ws/v1/terminal", headers=ORIGIN) as ws:
        ws.send_json(_hello(env, token="not-a-jwt"))
        assert _closed_code(ws) == 4401
    with env.client.websocket_connect("/ws/v1/terminal", headers=ORIGIN) as ws:
        assert _closed_code(ws) == 4401                         # no hello within the timeout
    with env.client.websocket_connect("/ws/v1/terminal", headers=ORIGIN) as ws:
        ws.send_json(_hello(env, token=create_access_token(env.plain)))
        assert _closed_code(ws) == 4403                         # no trading role
    with env.client.websocket_connect("/ws/v1/terminal", headers=ORIGIN) as ws:
        ws.send_json(_hello(env, bot_id=str(uuid.uuid4())))
        assert _closed_code(ws) == 4404


def test_snapshot_then_live_events_then_resume(env):
    with env.client.websocket_connect("/ws/v1/terminal", headers=ORIGIN) as ws:
        ws.send_json(_hello(env))
        hello = ws.receive_json()
        assert hello["type"] == "hello" and hello["replay_limit"] == 1000
        snap = ws.receive_json()
        assert snap["type"] == "snapshot" and snap["data"]["bot"]["name"] == "paper-kraken-01"
        start = snap["data"]["position"]
        env.add_events(3)
        got = []
        while len(got) < 3:
            msg = ws.receive_json()
            if msg["type"] == "event":
                got.append(msg["data"]["position"])
        assert got == sorted(got) and got[0] > start
        last = got[-1]
    env.add_events(2)                                            # missed while disconnected
    with env.client.websocket_connect("/ws/v1/terminal", headers=ORIGIN) as ws:
        ws.send_json(_hello(env, last_position=last))
        assert ws.receive_json()["type"] == "hello"
        resumed = []
        while len(resumed) < 2:
            msg = ws.receive_json()
            assert msg["type"] != "snapshot"                    # replay, not snapshot
            if msg["type"] == "event":
                resumed.append(msg["data"]["position"])
        assert resumed[0] > last and len(set(resumed)) == 2


def test_other_bot_events_are_not_streamed_and_heartbeat(env):
    with env.client.websocket_connect("/ws/v1/terminal", headers=ORIGIN) as ws:
        ws.send_json(_hello(env))
        ws.receive_json()
        ws.receive_json()
        env.add_events(2, bot=uuid.uuid4())
        msg = ws.receive_json()
        assert msg["type"] == "heartbeat"


def test_large_backlog_gets_a_snapshot(env):
    with env.client.websocket_connect("/ws/v1/terminal", headers=ORIGIN) as ws:
        ws.send_json(_hello(env))
        ws.receive_json()
        start = ws.receive_json()["data"]["position"]
    env.add_events(1001)
    with env.client.websocket_connect("/ws/v1/terminal", headers=ORIGIN) as ws:
        ws.send_json(_hello(env, last_position=start))
        ws.receive_json()
        assert ws.receive_json()["type"] == "snapshot"
