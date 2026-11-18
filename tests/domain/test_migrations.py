"""Domain migration chain (ADR-011, 10-backend/03 « Migrations »).

On an empty PostgreSQL: apply every chain exactly like the production `migrate`
service, then check that the `hl_*` schema produced by the `domain` revisions
matches the ORM metadata (no forgotten migration), and that the chain only
creates tables it owns.
"""

import asyncio
import os

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.core.database import Base
from scripts.migrate import run_migrations

pytestmark = pytest.mark.postgres

PROD_MODULES = dict(
    module_admin=True, module_agentic=True, module_analytics=True, module_i18n=True,
    module_leads=True, module_onboarding=True, module_security_middleware=True,
    module_tutorials=True, module_worker=True,
)


def _run(coro):
    return asyncio.run(coro)


async def _reset_schema(url: str) -> None:
    eng = create_async_engine(url)
    async with eng.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await eng.dispose()


async def _diff_and_tables(url: str):
    eng = create_async_engine(url)

    def _inspect(sync_conn):
        ctx = MigrationContext.configure(
            sync_conn,
            opts={
                "compare_type": True,
                "include_object": lambda obj, name, type_, reflected, compare_to: not (
                    type_ == "table" and reflected and compare_to is None
                ),
            },
        )
        diffs = compare_metadata(ctx, Base.metadata)
        return diffs, inspect(sync_conn).get_table_names()

    async with eng.connect() as conn:
        result = await conn.run_sync(_inspect)
    await eng.dispose()
    return result


def test_domain_chain_matches_models_on_postgres():
    url = os.environ["TEST_POSTGRES_URL"]
    _run(_reset_schema(url))
    applied = run_migrations(url=url, settings=Settings(database_url=url, **PROD_MODULES))
    assert applied[-1] == "domain"

    diffs, tables = _run(_diff_and_tables(url))
    hl_tables = {t for t in tables if t.startswith("hl_")}
    expected = {t for t in Base.metadata.tables if t.startswith("hl_")}
    assert hl_tables == expected

    def _concerns_hl(diff) -> bool:
        text_ = repr(diff)
        return "hl_" in text_

    hl_diffs = [d for d in diffs if _concerns_hl(d)]
    assert hl_diffs == [], f"models and domain migrations diverge: {hl_diffs}"
