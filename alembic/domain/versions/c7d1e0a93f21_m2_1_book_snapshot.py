"""m2.1 book snapshot — hl_book_snapshot (07-data/03 « Instantanés de carnet », ADR-021).

Reviewed by hand (ADR-011 procedure): upgrade() only creates one table; types frozen to
NUMERIC(38,18), TIMESTAMP WITH TIME ZONE and JSON/JSONB. Nothing is dropped or altered.

Revision ID: c7d1e0a93f21
Revises: b394e67c4ed6
Create Date: 2026-11-20 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c7d1e0a93f21'
down_revision: Union[str, None] = 'b394e67c4ed6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    op.create_table(
        'hl_book_snapshot',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('instrument_id', sa.String(length=64), nullable=False),
        sa.Column('minute_start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('captured_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('best_bid', sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column('best_ask', sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column('spread_bps', sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column('bids', _JSON, nullable=False),
        sa.Column('asks', _JSON, nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('instrument_id', 'minute_start', name='uq_hl_book_snapshot'),
    )


def downgrade() -> None:
    op.drop_table('hl_book_snapshot')
