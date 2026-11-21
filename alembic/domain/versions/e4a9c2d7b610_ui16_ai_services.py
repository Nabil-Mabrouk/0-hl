"""ui-16 AI services — hl_ai_settings, hl_ai_run, hl_ai_step, hl_ai_alert (03-ecrans/UI-16, ADR-026).

Reviewed by hand (ADR-011 procedure): upgrade() only creates four tables; types frozen to
NUMERIC(38,18), TIMESTAMP WITH TIME ZONE and JSON/JSONB. Nothing is dropped or altered.

Revision ID: e4a9c2d7b610
Revises: d2a7f4c81b90
Create Date: 2026-11-21 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e4a9c2d7b610'
down_revision: Union[str, None] = 'd2a7f4c81b90'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')
_DEC = sa.Numeric(precision=38, scale=18)
_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        'hl_ai_settings',
        sa.Column('id', sa.String(length=16), nullable=False),
        sa.Column('daily_budget_usd', _DEC, nullable=False),
        sa.Column('models', _JSON, nullable=False),
        sa.Column('prices', _JSON, nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('updated_at', _TS, nullable=False),
        sa.Column('updated_by', sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'hl_ai_run',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('service_id', sa.String(length=64), nullable=False),
        sa.Column('service_version', sa.String(length=32), nullable=False),
        sa.Column('agent_id', sa.String(length=64), nullable=False),
        sa.Column('agent_version', sa.String(length=32), nullable=False),
        sa.Column('instructions_sha256', sa.String(length=64), nullable=False),
        sa.Column('model', sa.String(length=128), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('idempotency_key', sa.String(length=128), nullable=True),
        sa.Column('input', _JSON, nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('output_text', sa.Text(), nullable=True),
        sa.Column('output_json', _JSON, nullable=True),
        sa.Column('tokens_in', sa.Integer(), nullable=False),
        sa.Column('tokens_out', sa.Integer(), nullable=False),
        sa.Column('cost_actual_usd', _DEC, nullable=False),
        sa.Column('cost_estimated_usd', _DEC, nullable=False),
        sa.Column('cost_estimated_only', sa.Boolean(), nullable=False),
        sa.Column('model_calls', sa.Integer(), nullable=False),
        sa.Column('tool_calls', sa.Integer(), nullable=False),
        sa.Column('started_at', _TS, nullable=False),
        sa.Column('finished_at', _TS, nullable=True),
        sa.Column('error_code', sa.String(length=64), nullable=True),
        sa.Column('error_detail', sa.Text(), nullable=True),
        sa.Column('correlation_id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'idempotency_key', name='uq_hl_ai_run_idempotency'),
    )
    op.create_index('ix_hl_ai_run_started_at', 'hl_ai_run', ['started_at'])
    op.create_table(
        'hl_ai_step',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('run_id', sa.Uuid(), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('at', _TS, nullable=False),
        sa.Column('detail', _JSON, nullable=False),
        sa.Column('tokens_in', sa.Integer(), nullable=False),
        sa.Column('tokens_out', sa.Integer(), nullable=False),
        sa.Column('cost_actual_usd', _DEC, nullable=True),
        sa.Column('cost_estimated_usd', _DEC, nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=False),
        sa.Column('error_code', sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(['run_id'], ['hl_ai_run.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('run_id', 'seq', name='uq_hl_ai_step_seq'),
    )
    op.create_table(
        'hl_ai_alert',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('threshold', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(length=16), nullable=False),
        sa.Column('spent_usd', _DEC, nullable=False),
        sa.Column('budget_usd', _DEC, nullable=False),
        sa.Column('created_at', _TS, nullable=False),
        sa.Column('email_sent', sa.Boolean(), nullable=False),
        sa.Column('email_error', sa.String(length=256), nullable=True),
        sa.Column('acked_at', _TS, nullable=True),
        sa.Column('acked_by', sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('day', 'threshold', name='uq_hl_ai_alert_day_threshold'),
    )


def downgrade() -> None:
    op.drop_table('hl_ai_alert')
    op.drop_table('hl_ai_step')
    op.drop_index('ix_hl_ai_run_started_at', table_name='hl_ai_run')
    op.drop_table('hl_ai_run')
    op.drop_table('hl_ai_settings')
