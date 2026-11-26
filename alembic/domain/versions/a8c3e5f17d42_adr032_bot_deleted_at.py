"""ADR-032 bot management: hl_bot.deleted_at, name unique among non-deleted bots

Revision ID: a8c3e5f17d42
Revises: f6b2d8a91c34
Create Date: 2026-11-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a8c3e5f17d42"
down_revision: Union[str, None] = "f6b2d8a91c34"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column("hl_bot", sa.Column("deleted_at", TS, nullable=True))
    # PostgreSQL default name of the M1 `UNIQUE (name)` constraint
    op.drop_constraint("hl_bot_name_key", "hl_bot", type_="unique")
    op.create_index("uq_hl_bot_name_live", "hl_bot", ["name"], unique=True,
                    postgresql_where=sa.text("deleted_at IS NULL"))


def downgrade() -> None:
    op.drop_index("uq_hl_bot_name_live", table_name="hl_bot")
    op.create_unique_constraint("hl_bot_name_key", "hl_bot", ["name"])
    op.drop_column("hl_bot", "deleted_at")
