"""Terminal WebSocket /ws/v1/terminal (spec 10-backend/04 « WebSocket »).

Origin check (browsers do not apply CORS to WebSockets), authentication by the
first message (no token in the URL), snapshot or replay from `last_position`,
then events pushed in journal order; closed with 4401 when the token expires.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import jwt
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.auth.security import decode_token
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.models import User
from app.domain.trading.api.common import event_json
from app.domain.trading.api.snapshot import build_snapshot
from app.domain.trading.models import Bot, DomainEvent
from app.domain.trading.rbac import active_roles
from app.domain.trading.types import to_jsonable, utcnow

log = logging.getLogger("hl.api.ws")
router = APIRouter()

REPLAY_LIMIT = 1000
HEARTBEAT_SECONDS = 10.0
POLL_SECONDS = 1.0
HELLO_TIMEOUT = 5.0
CLOSE_UNAUTHENTICATED = 4401
CLOSE_FORBIDDEN = 4403
CLOSE_NOT_FOUND = 4404
CLOSE_BAD_REQUEST = 4400


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return SessionLocal


def get_ws_timing() -> dict[str, float]:
    return {"poll": POLL_SECONDS, "heartbeat": HEARTBEAT_SECONDS, "hello_timeout": HELLO_TIMEOUT}


def get_allowed_origins() -> set[str]:
    """Only the configured frontend may open the terminal socket (CSWSH protection)."""
    return {get_settings().frontend_url.rstrip("/")}


def _stream_filter(bot_id: uuid.UUID):
    return or_(DomainEvent.bot_id == bot_id, DomainEvent.aggregate_type == "market_feed")


async def _send(ws: WebSocket, msg: dict[str, Any]) -> None:
    await ws.send_json(to_jsonable(msg))


async def _authenticate(ws: WebSocket, sessions, hello: dict[str, Any]) -> tuple[int, datetime] | None:
    try:
        claims = decode_token(str(hello.get("token", "")), expected_type="access")
        user_id = int(claims["sub"])
        expires = datetime.fromtimestamp(int(claims["exp"]), tz=timezone.utc)
    except (jwt.InvalidTokenError, KeyError, ValueError, TypeError):
        await ws.close(code=CLOSE_UNAUTHENTICATED)
        return None
    async with sessions() as db:
        user = await db.get(User, user_id)
        if user is None or not user.is_active:
            await ws.close(code=CLOSE_UNAUTHENTICATED)
            return None
        if not await active_roles(db, user_id):
            await ws.close(code=CLOSE_FORBIDDEN)
            return None
    return user_id, expires


@router.websocket("/ws/v1/terminal")
async def terminal_ws(
    ws: WebSocket,
    sessions: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    timing: dict[str, float] = Depends(get_ws_timing),
    origins: set[str] = Depends(get_allowed_origins),
) -> None:
    origin = (ws.headers.get("origin") or "").rstrip("/")
    await ws.accept()
    if origin not in origins:
        await ws.close(code=CLOSE_FORBIDDEN)
        return
    try:
        hello = await asyncio.wait_for(ws.receive_json(), timeout=timing["hello_timeout"])
    except (asyncio.TimeoutError, ValueError, WebSocketDisconnect):
        with contextlib.suppress(Exception):
            await ws.close(code=CLOSE_UNAUTHENTICATED)
        return
    if not isinstance(hello, dict) or hello.get("type") != "hello":
        await ws.close(code=CLOSE_BAD_REQUEST)
        return
    auth = await _authenticate(ws, sessions, hello)
    if auth is None:
        return
    _user_id, expires = auth
    try:
        bot_id = uuid.UUID(str(hello.get("bot_id")))
    except ValueError:
        await ws.close(code=CLOSE_NOT_FOUND)
        return

    async with sessions() as db:
        if await db.get(Bot, bot_id) is None:
            await ws.close(code=CLOSE_NOT_FOUND)
            return
        head = await db.scalar(select(func.max(DomainEvent.position))) or 0
        last = hello.get("last_position")
        backlog = None
        if isinstance(last, int) and 0 <= last <= head:
            backlog = await db.scalar(select(func.count()).select_from(DomainEvent).where(
                DomainEvent.position > last, _stream_filter(bot_id)))
        await _send(ws, {"type": "hello", "position": head, "replay_limit": REPLAY_LIMIT,
                         "heartbeat_ms": int(timing["heartbeat"] * 1000)})
        if backlog is None or backlog > REPLAY_LIMIT:
            snapshot = await build_snapshot(db, bot_id, utcnow())
            await _send(ws, {"type": "snapshot", "data": snapshot})
            cursor = snapshot["position"]
        else:
            cursor = last  # type: ignore[assignment]

    receiver = asyncio.create_task(_drain(ws))
    loop = asyncio.get_running_loop()
    next_heartbeat = loop.time() + timing["heartbeat"]
    try:
        while not receiver.done():
            if utcnow() >= expires:
                await ws.close(code=CLOSE_UNAUTHENTICATED)
                return
            async with sessions() as db:
                rows = (await db.scalars(select(DomainEvent).where(
                    DomainEvent.position > cursor, _stream_filter(bot_id))
                    .order_by(DomainEvent.position).limit(500))).all()
            for e in rows:
                await _send(ws, {"type": "event", "data": event_json(e)})
                cursor = e.position
            if loop.time() >= next_heartbeat:
                await _send(ws, {"type": "heartbeat", "position": cursor})
                next_heartbeat = loop.time() + timing["heartbeat"]
            await asyncio.wait({receiver}, timeout=timing["poll"])
    except (WebSocketDisconnect, RuntimeError):
        return
    finally:
        receiver.cancel()


async def _drain(ws: WebSocket) -> None:
    """Consume client messages (ignored) until the client disconnects."""
    try:
        while True:
            await ws.receive_text()
    except (WebSocketDisconnect, RuntimeError):
        return
