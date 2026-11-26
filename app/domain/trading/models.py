"""ORM models of the 0-hl trading domain — physical model of spec 10-backend/03.

All tables are prefixed `hl_`. Money/quantities use `Dec`, timestamps
`UtcDateTime`. Constraints are named explicitly so future Alembic revisions of
the `domain` chain (ADR-011) are stable.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum as PyEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
    inspect,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

import app.core.models  # noqa: F401  (registers chassis `users`, target of FKs below)
from app.core.database import Base
from app.domain.trading.enums import (
    AssessmentVerdict,
    BotStatus,
    CandidateStatus,
    CashMovementKind,
    CommandStatus,
    CommandType,
    Environment,
    IntentPurpose,
    LedgerKind,
    OrderStatus,
    PolicyVersionStatus,
    PositionStatus,
    SessionStatus,
    Side,
    SnapshotQuality,
    StrategyVersionStatus,
    TradingRole,
)
from app.domain.trading.errors import VersionImmutable
from app.domain.trading.types import Dec, JsonType, UtcDateTime, new_id, utcnow

# Autoincrement BIGINT on PostgreSQL; INTEGER PRIMARY KEY (rowid alias) on SQLite.
BigIntPK = BigInteger().with_variant(Integer(), "sqlite")


def _enum(enum_cls: type[PyEnum], name: str) -> Enum:
    """Portable enum stored as its value, with a named CHECK constraint."""
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        length=32,
        values_callable=lambda cls: [m.value for m in cls],
    )


# --------------------------------------------------------------------------- #
# Governance
# --------------------------------------------------------------------------- #
class RoleAssignment(Base):
    __tablename__ = "hl_role_assignment"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    role: Mapped[TradingRole] = mapped_column(_enum(TradingRole, "hl_trading_role"), nullable=False)
    granted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    granted_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    revoked_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class AuditEvent(Base):
    __tablename__ = "hl_audit_event"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(128), nullable=False)
    diff: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    self_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    correlation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)

    __table_args__ = (
        Index("ix_hl_audit_entity", "entity_type", "entity_id"),
        Index("ix_hl_audit_occurred", "occurred_at"),
    )


# --------------------------------------------------------------------------- #
# Event journal (outbox)
# --------------------------------------------------------------------------- #
class DomainEvent(Base):
    __tablename__ = "hl_domain_event"

    position: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, unique=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(128), nullable=False)
    aggregate_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    correlation_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    causation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    bot_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    received_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "aggregate_type", "aggregate_id", "aggregate_sequence", name="uq_hl_event_aggregate_seq"
        ),
        CheckConstraint("aggregate_sequence >= 1", name="ck_hl_event_seq_positive"),
        Index("ix_hl_event_correlation", "correlation_id", "occurred_at"),
        Index("ix_hl_event_bot_position", "bot_id", "position"),
    )


class ConsumerCursor(Base):
    __tablename__ = "hl_consumer_cursor"

    consumer: Mapped[str] = mapped_column(String(64), primary_key=True)
    position: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
class Command(Base):
    __tablename__ = "hl_command"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    bot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hl_bot.id"), nullable=False, index=True)
    command: Mapped[CommandType] = mapped_column(_enum(CommandType, "hl_command_type"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[CommandStatus] = mapped_column(
        _enum(CommandStatus, "hl_command_status"), nullable=False, default=CommandStatus.PENDING
    )
    rejection_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    deadline_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    result: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)


# --------------------------------------------------------------------------- #
# Reference data and versioned configuration
# --------------------------------------------------------------------------- #
class Instrument(Base):
    __tablename__ = "hl_instrument"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # "kraken_spot:BTC/USD"
    venue: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    rest_name: Mapped[str | None] = mapped_column(String(32), nullable=True)
    altname: Mapped[str | None] = mapped_column(String(32), nullable=True)
    base: Mapped[str] = mapped_column(String(16), nullable=False)
    quote: Mapped[str] = mapped_column(String(16), nullable=False)
    pair_decimals: Mapped[int] = mapped_column(Integer, nullable=False)
    lot_decimals: Mapped[int] = mapped_column(Integer, nullable=False)
    tick_size: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    ordermin: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    costmin: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    in_universe: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    volume_24h_usd: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)

    __table_args__ = (UniqueConstraint("venue", "symbol", name="uq_hl_instrument_venue_symbol"),)


class Strategy(Base):
    __tablename__ = "hl_strategy"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)


class StrategyVersion(Base):
    __tablename__ = "hl_strategy_version"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    strategy_id: Mapped[str] = mapped_column(ForeignKey("hl_strategy.id"), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[StrategyVersionStatus] = mapped_column(
        _enum(StrategyVersionStatus, "hl_strategy_version_status"), nullable=False
    )
    config: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    config_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    published_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("strategy_id", "version", name="uq_hl_strategy_version"),
    )


class RiskPolicy(Base):
    __tablename__ = "hl_risk_policy"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)


class RiskPolicyVersion(Base):
    __tablename__ = "hl_risk_policy_version"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    policy_id: Mapped[str] = mapped_column(ForeignKey("hl_risk_policy.id"), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[PolicyVersionStatus] = mapped_column(
        _enum(PolicyVersionStatus, "hl_policy_version_status"), nullable=False
    )
    config: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    config_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    effective_from: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    published_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    # M2.3: pending publication of a loosened policy (ADR-006 review delay)
    publish_requested_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    publish_requested_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    publish_request_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("policy_id", "version", name="uq_hl_risk_policy_version"),
    )


def _guard_immutable_config(mapper, connection, target) -> None:
    """INV-06 defence in depth: config/hash of a non-DRAFT version never change."""
    state = inspect(target)
    status_hist = state.attrs.status.history
    previous_status = (
        status_hist.deleted[0] if status_hist.deleted else target.status
    )
    if previous_status is None or getattr(previous_status, "value", previous_status) == "DRAFT":
        return
    for attr in ("config", "config_sha256", "version"):
        if state.attrs[attr].history.has_changes():
            raise VersionImmutable(
                f"{type(target).__name__} {target.id} is {previous_status}: {attr} is immutable"
            )


event.listen(StrategyVersion, "before_update", _guard_immutable_config)
event.listen(RiskPolicyVersion, "before_update", _guard_immutable_config)


# --------------------------------------------------------------------------- #
# Bots and sessions
# --------------------------------------------------------------------------- #
class Bot(Base):
    __tablename__ = "hl_bot"
    # ADR-032: a deleted (archived) bot frees its name — unique among non-deleted bots only
    __table_args__ = (
        Index("uq_hl_bot_name_live", "name", unique=True,
              postgresql_where=text("deleted_at IS NULL"), sqlite_where=text("deleted_at IS NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    environment: Mapped[Environment] = mapped_column(
        _enum(Environment, "hl_environment"), nullable=False
    )
    status: Mapped[BotStatus] = mapped_column(_enum(BotStatus, "hl_bot_status"), nullable=False)
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hl_strategy_version.id"), nullable=False
    )
    risk_policy_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hl_risk_policy_version.id"), nullable=False
    )
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)  # ADR-032


class BotSession(Base):
    __tablename__ = "hl_bot_session"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    bot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hl_bot.id"), nullable=False, index=True)
    environment: Mapped[Environment] = mapped_column(
        _enum(Environment, "hl_session_environment"), nullable=False
    )
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hl_strategy_version.id"), nullable=False
    )
    strategy_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    risk_policy_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hl_risk_policy_version.id"), nullable=False
    )
    risk_policy_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[SessionStatus] = mapped_column(
        _enum(SessionStatus, "hl_session_status"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


# --------------------------------------------------------------------------- #
# Decision and execution chain (used from M1.3)
# --------------------------------------------------------------------------- #
class Candidate(Base):
    __tablename__ = "hl_candidate"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    bot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hl_bot.id"), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hl_bot_session.id"), nullable=False)
    instrument_id: Mapped[str] = mapped_column(ForeignKey("hl_instrument.id"), nullable=False)
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hl_strategy_version.id"), nullable=False
    )
    window_start: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    status: Mapped[CandidateStatus] = mapped_column(
        _enum(CandidateStatus, "hl_candidate_status"), nullable=False
    )
    features: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "instrument_id",
            "strategy_version_id",
            "window_start",
            name="uq_hl_candidate_window",
        ),
    )


class RiskAssessment(Base):
    __tablename__ = "hl_risk_assessment"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hl_candidate.id"), nullable=False, index=True
    )
    verdict: Mapped[AssessmentVerdict] = mapped_column(
        _enum(AssessmentVerdict, "hl_assessment_verdict"), nullable=False
    )
    criteria: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    risk_policy_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hl_risk_policy_version.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)


class SizingDecision(Base):
    __tablename__ = "hl_sizing_decision"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hl_candidate.id"), nullable=False, index=True
    )
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False)
    notional_requested: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    notional_approved: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    quantity: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    reference_price: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    stop_price: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    risk_budget: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    limits_applied: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    rejection_reasons: Mapped[list[Any]] = mapped_column(JsonType, nullable=False, default=list)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)


class OrderIntent(Base):
    __tablename__ = "hl_order_intent"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    bot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hl_bot.id"), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hl_bot_session.id"), nullable=False)
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hl_candidate.id"), nullable=True
    )
    position_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hl_position.id"), nullable=True
    )
    instrument_id: Mapped[str] = mapped_column(ForeignKey("hl_instrument.id"), nullable=False)
    side: Mapped[Side] = mapped_column(_enum(Side, "hl_intent_side"), nullable=False)
    purpose: Mapped[IntentPurpose] = mapped_column(
        _enum(IntentPurpose, "hl_intent_purpose"), nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(Dec, nullable=False)
    limit_price: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    max_notional: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    risk_approval_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hl_sizing_decision.id"), nullable=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)

    __table_args__ = (
        # INV-01: an entry order is never created without a risk authorisation.
        CheckConstraint(
            "purpose = 'EXIT' OR risk_approval_id IS NOT NULL",
            name="ck_hl_intent_entry_requires_risk_approval",
        ),
        CheckConstraint(
            "purpose = 'ENTRY' OR position_id IS NOT NULL",
            name="ck_hl_intent_exit_requires_position",
        ),
        CheckConstraint("CAST(quantity AS NUMERIC) > 0", name="ck_hl_intent_qty_positive"),
    )


class Order(Base):
    __tablename__ = "hl_order"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    intent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hl_order_intent.id"), nullable=False, index=True
    )
    client_order_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    venue_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    side: Mapped[Side] = mapped_column(_enum(Side, "hl_order_side"), nullable=False)
    status: Mapped[OrderStatus] = mapped_column(
        _enum(OrderStatus, "hl_order_status"), nullable=False
    )
    requested_qty: Mapped[Decimal] = mapped_column(Dec, nullable=False)
    filled_qty: Mapped[Decimal] = mapped_column(Dec, nullable=False, default=Decimal(0))
    avg_fill_price: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    limit_price: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    acked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Fill(Base):
    __tablename__ = "hl_fill"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hl_order.id"), nullable=False)
    venue_fill_id: Mapped[str] = mapped_column(String(64), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Dec, nullable=False)
    price: Mapped[Decimal] = mapped_column(Dec, nullable=False)
    fee: Mapped[Decimal] = mapped_column(Dec, nullable=False, default=Decimal(0))
    fee_asset: Mapped[str] = mapped_column(String(16), nullable=False, default="USD")
    liquidity: Mapped[str] = mapped_column(String(8), nullable=False, default="taker")
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    book_snapshot: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("order_id", "venue_fill_id", name="uq_hl_fill_venue_id"),
        CheckConstraint("CAST(qty AS NUMERIC) > 0", name="ck_hl_fill_qty_positive"),
        CheckConstraint("CAST(price AS NUMERIC) > 0", name="ck_hl_fill_price_positive"),
    )


class Position(Base):
    __tablename__ = "hl_position"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    bot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hl_bot.id"), nullable=False, index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hl_bot_session.id"), nullable=False)
    instrument_id: Mapped[str] = mapped_column(ForeignKey("hl_instrument.id"), nullable=False)
    status: Mapped[PositionStatus] = mapped_column(
        _enum(PositionStatus, "hl_position_status"), nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(Dec, nullable=False, default=Decimal(0))
    cost_basis: Mapped[Decimal] = mapped_column(Dec, nullable=False, default=Decimal(0))
    avg_entry_price: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    initial_stop_price: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    take_profit_price: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    r0_usd: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    opened_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # M1.3 (06-agents/03)
    realized_pnl_net: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    fees_paid: Mapped[Decimal] = mapped_column(Dec, nullable=False, default=Decimal(0))
    exit_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # M1.4 (10-backend/04): conservative marks written by the EQUITY step
    mark_price: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    mark_value: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    marked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


class EquitySnapshot(Base):
    __tablename__ = "hl_equity_snapshot"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    bot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hl_bot.id"), nullable=False)
    at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    cash: Mapped[Decimal] = mapped_column(Dec, nullable=False)
    cash_reserved: Mapped[Decimal] = mapped_column(Dec, nullable=False)
    positions_mark_value: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    equity: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    realized_pnl_net: Mapped[Decimal] = mapped_column(Dec, nullable=False)
    unrealized_pnl: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    quality: Mapped[SnapshotQuality] = mapped_column(
        _enum(SnapshotQuality, "hl_snapshot_quality"), nullable=False
    )

    __table_args__ = (Index("ix_hl_equity_bot_at", "bot_id", "at"),)


# --------------------------------------------------------------------------- #
# Market data (M1.2, spec 07-data/03)
# --------------------------------------------------------------------------- #
class TradeMinute(Base):
    __tablename__ = "hl_trade_minute"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    instrument_id: Mapped[str] = mapped_column(String(64), nullable=False)
    minute_start: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # live | backfill
    notional_usd: Mapped[Decimal] = mapped_column(Dec, nullable=False)
    buy_notional_usd: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    sell_notional_usd: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    buy_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sell_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False)
    volume_base: Mapped[Decimal] = mapped_column(Dec, nullable=False)
    open: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    high: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    low: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    close: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "instrument_id", "minute_start", "source", name="uq_hl_trade_minute"
        ),
        CheckConstraint("source IN ('live', 'backfill')", name="ck_hl_trade_minute_source"),
        Index("ix_hl_trade_minute_instr_time", "instrument_id", "minute_start"),
    )


class FeedStatus(Base):
    __tablename__ = "hl_feed_status"

    instrument_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False)  # LIVE | STALE | UNKNOWN
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_message_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    last_book_change_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    last_trade_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    last_checksum_ok_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    resync_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)


class BookSnapshot(Base):
    """One order-book capture per LIVE pair and minute (M2.1, 07-data/03 « Instantanés »)."""

    __tablename__ = "hl_book_snapshot"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    instrument_id: Mapped[str] = mapped_column(String(64), nullable=False)
    minute_start: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    best_bid: Mapped[Decimal] = mapped_column(Dec, nullable=False)
    best_ask: Mapped[Decimal] = mapped_column(Dec, nullable=False)
    spread_bps: Mapped[Decimal] = mapped_column(Dec, nullable=False)
    bids: Mapped[list[Any]] = mapped_column(JsonType, nullable=False)  # [[price, qty]] best first
    asks: Mapped[list[Any]] = mapped_column(JsonType, nullable=False)

    __table_args__ = (
        UniqueConstraint("instrument_id", "minute_start", name="uq_hl_book_snapshot"),
    )


# --------------------------------------------------------------------------- #
# Ledger
# --------------------------------------------------------------------------- #
class LedgerTransaction(Base):
    __tablename__ = "hl_ledger_transaction"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    bot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hl_bot.id"), nullable=False, index=True)
    kind: Mapped[LedgerKind] = mapped_column(_enum(LedgerKind, "hl_ledger_kind"), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    source_event_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")


class LedgerEntry(Base):
    __tablename__ = "hl_ledger_entry"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hl_ledger_transaction.id"), nullable=False, index=True
    )
    bot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hl_bot.id"), nullable=False)
    account_code: Mapped[str] = mapped_column(String(96), nullable=False)
    asset: Mapped[str] = mapped_column(String(16), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Dec, nullable=False)

    __table_args__ = (Index("ix_hl_ledger_entry_bot_account", "bot_id", "account_code"),)


class CashMovement(Base):
    __tablename__ = "hl_cash_movement"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    bot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hl_bot.id"), nullable=False, index=True)
    kind: Mapped[CashMovementKind] = mapped_column(
        _enum(CashMovementKind, "hl_cash_movement_kind"), nullable=False
    )
    asset: Mapped[str] = mapped_column(String(16), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Dec, nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    ledger_transaction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hl_ledger_transaction.id"), nullable=False
    )
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)

    __table_args__ = (
        CheckConstraint("CAST(amount AS NUMERIC) > 0", name="ck_hl_cash_movement_positive"),
    )
