"""m2.3 policy publish request — pending publication of a loosened policy (05-strategies/04).

Reviewed by hand (ADR-011 procedure): upgrade() only adds three nullable columns to
hl_risk_policy_version; types frozen to TIMESTAMP WITH TIME ZONE, VARCHAR(64) and TEXT.

Revision ID: d2a7f4c81b90
Revises: c7d1e0a93f21
Create Date: 2026-11-20 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd2a7f4c81b90'
down_revision: Union[str, None] = 'c7d1e0a93f21'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('hl_risk_policy_version',
                  sa.Column('publish_requested_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('hl_risk_policy_version',
                  sa.Column('publish_requested_by', sa.String(length=64), nullable=True))
    op.add_column('hl_risk_policy_version',
                  sa.Column('publish_request_reason', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('hl_risk_policy_version', 'publish_request_reason')
    op.drop_column('hl_risk_policy_version', 'publish_requested_by')
    op.drop_column('hl_risk_policy_version', 'publish_requested_at')
