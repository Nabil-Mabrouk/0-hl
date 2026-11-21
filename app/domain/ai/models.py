"""Tables of the AI services (spec 03-ecrans/UI-16 « Journal détaillé », « Budget et alertes »).

`hl_ai_run` and `hl_ai_step` are append-only: a step is never modified once written; a run is
only closed (status, totals, output). Settings are a single versioned row.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

import app.core.models  # noqa: F401  (registers chassis `users`)
from app.core.database import Base
from app.domain.trading.types import Dec, JsonType, UtcDateTime, new_id, utcnow


class AiSettings(Base):
    __tablename__ = "hl_ai_settings"

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default="default")
    daily_budget_usd: Mapped[Decimal] = mapped_column(Dec, nullable=False)
    models: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)     # service_id -> model
    prices: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)     # model -> {input, output} USD / 1M tokens
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_by: Mapped[str] = mapped_column(String(64), nullable=False, default="system")


class AiRun(Base):
    __tablename__ = "hl_ai_run"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    service_id: Mapped[str] = mapped_column(String(64), nullable=False)
    service_version: Mapped[str] = mapped_column(String(32), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False)
    agent_version: Mapped[str] = mapped_column(String(32), nullable=False)
    instructions_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    output_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_json: Mapped[Any | None] = mapped_column(JsonType, nullable=True)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_actual_usd: Mapped[Decimal] = mapped_column(Dec, nullable=False, default=Decimal(0))
    cost_estimated_usd: Mapped[Decimal] = mapped_column(Dec, nullable=False, default=Decimal(0))
    cost_estimated_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    model_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, default=new_id)

    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_hl_ai_run_idempotency"),
        Index("ix_hl_ai_run_started_at", "started_at"),
    )


class AiStep(Base):
    __tablename__ = "hl_ai_step"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hl_ai_run.id"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)       # MODEL_CALL | TOOL_CALL
    at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    # MODEL_CALL: {messages, params, response, finish_reason, http_status, attempt}
    # TOOL_CALL: {tool, tool_call_id, arguments, result, result_sha256, truncated}
    detail: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_actual_usd: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    cost_estimated_usd: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_hl_ai_step_seq"),)


class AiAlert(Base):
    """One per UTC day and threshold (80, 100); the in-app banner until acknowledged."""

    __tablename__ = "hl_ai_alert"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)     # app | proxy
    spent_usd: Mapped[Decimal] = mapped_column(Dec, nullable=False)
    budget_usd: Mapped[Decimal] = mapped_column(Dec, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    email_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    email_error: Mapped[str | None] = mapped_column(String(256), nullable=True)
    acked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    acked_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (UniqueConstraint("day", "threshold", name="uq_hl_ai_alert_day_threshold"),)
