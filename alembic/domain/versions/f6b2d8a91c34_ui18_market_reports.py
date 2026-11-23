"""UI-18 market reports: hl_market_report, system AI runs, market series setting (ADR-030)

Revision ID: f6b2d8a91c34
Revises: e4a9c2d7b610
Create Date: 2026-11-23
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f6b2d8a91c34"
down_revision: Union[str, None] = "e4a9c2d7b610"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEC = sa.Numeric(precision=38, scale=18)
TS = sa.DateTime(timezone=True)
_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    op.create_table(
        "hl_market_report",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("cadence", sa.String(length=8), nullable=False),
        sa.Column("period_start", TS, nullable=False),
        sa.Column("period_end", TS, nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("generated_by", sa.String(length=16), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("summary", _JSON, nullable=False),
        sa.Column("sections", _JSON, nullable=False),
        sa.Column("charts", _JSON, nullable=False),
        sa.Column("facts", _JSON, nullable=False),
        sa.Column("tags", _JSON, nullable=False),
        sa.Column("btc_return_pct", DEC, nullable=True),
        sa.Column("breadth_pct", DEC, nullable=True),
        sa.Column("dispersion_pp", DEC, nullable=True),
        sa.Column("btc_vol_ratio", DEC, nullable=True),
        sa.Column("volume_ratio", DEC, nullable=True),
        sa.Column("ai_run_id", sa.Uuid(), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("instructions_sha256", sa.String(length=64), nullable=True),
        sa.Column("cost_usd", DEC, nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.ForeignKeyConstraint(["ai_run_id"], ["hl_ai_run.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cadence", "period_end", name="uq_hl_market_report_period"),
    )
    op.create_index("ix_hl_market_report_end", "hl_market_report", ["period_end"])
    op.alter_column("hl_ai_run", "user_id", existing_type=sa.Integer(), nullable=True)
    op.add_column("hl_ai_settings", sa.Column("market_series", _JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("hl_ai_settings", "market_series")
    op.alter_column("hl_ai_run", "user_id", existing_type=sa.Integer(), nullable=False)
    op.drop_index("ix_hl_market_report_end", table_name="hl_market_report")
    op.drop_table("hl_market_report")
