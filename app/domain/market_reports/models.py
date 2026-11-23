"""Market reports — spec 03-ecrans/UI-18 « Stockage » (ADR-030).

One row per (cadence, period_end): PUBLISHED (LLM), DATA_ONLY (charts + template summary),
MISSED (period skipped while the worker was down) or FAILED. Facts and chart data are frozen.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.domain.trading.types import Dec, JsonType, UtcDateTime, new_id, utcnow


class MarketReport(Base):
    __tablename__ = "hl_market_report"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    cadence: Mapped[str] = mapped_column(String(8), nullable=False)
    period_start: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    period_end: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    generated_by: Mapped[str | None] = mapped_column(String(16), nullable=True)   # llm | data-only
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[list[Any]] = mapped_column(JsonType, nullable=False, default=list)
    sections: Mapped[list[Any]] = mapped_column(JsonType, nullable=False, default=list)
    charts: Mapped[list[Any]] = mapped_column(JsonType, nullable=False, default=list)
    facts: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    tags: Mapped[list[Any]] = mapped_column(JsonType, nullable=False, default=list)
    btc_return_pct: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    breadth_pct: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    dispersion_pp: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    btc_vol_ratio: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    volume_ratio: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    ai_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("hl_ai_run.id"), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    instructions_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cost_usd: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("cadence", "period_end", name="uq_hl_market_report_period"),
        Index("ix_hl_market_report_end", "period_end"),
    )
