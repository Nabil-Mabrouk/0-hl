"""Test fixtures for the 0-hl domain (spec 10-backend/03 « Environnement de développement »).

Tests run on an in-memory SQLite database (no network). Foreign keys are
enforced to mimic PostgreSQL. Tests marked `postgres` run only when
TEST_POSTGRES_URL is set.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

os.environ.setdefault("ENVIRONMENT", "test")

from app.core.database import Base  # noqa: E402
import app.core.models  # noqa: E402,F401  (users table)
import app.domain.models  # noqa: E402,F401  (hl_* tables)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "postgres: requires TEST_POSTGRES_URL")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("TEST_POSTGRES_URL"):
        return
    skip = pytest.mark.skip(reason="TEST_POSTGRES_URL not set")
    for item in items:
        if "postgres" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng.sync_engine, "connect")
    def _fk_on(dbapi_conn, _record) -> None:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
def session_factory(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)


@pytest.fixture
async def db(session_factory) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


async def _reset_public_schema(eng) -> None:
    async with eng.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))


@pytest.fixture
async def pg_engine():
    url = os.environ["TEST_POSTGRES_URL"]
    eng = create_async_engine(url)
    await _reset_public_schema(eng)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await _reset_public_schema(eng)
    await eng.dispose()
