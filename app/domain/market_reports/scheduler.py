"""Market report series in the worker — spec 03-ecrans/UI-18 « Séries et calendrier » (ADR-030).

Every 30 s: for each active series, the last closed period (end + 2 min, minutes settled) gets one
report; a gap left by a stopped worker is recorded as MISSED (only the last period is generated).
MR.1 publishes DATA_ONLY reports (facts, charts, template summary); MR.2 adds the LLM text.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.market_reports import facts as F
from app.domain.ai.runner import plain
from app.domain.market_reports.models import MarketReport
from app.domain.trading.types import utcnow

log = logging.getLogger("hl.market_reports")
DEFAULT_SERIES = ["4h", "24h"]
DELAY = timedelta(minutes=2)
TICK_SECONDS = 30.0
DATA_ONLY_CHARTS = ["radar", "btc-breadth", "leaders", "distribution"]


def _dec(v: str | None) -> Decimal | None:
    return None if v is None else Decimal(v)


def data_only_text(facts: dict[str, Any]) -> tuple[str, list[str], list[dict[str, Any]]]:
    """Title, 3-point summary and chart captions built from the facts by fixed templates."""
    btc, br = facts["btc"]["return_pct"], facts["breadth"]["up_pct"]
    tags = facts["regime"]["tags"]
    words = {"broad-selloff": "broad sell-off", "broad-rally": "broad rally", "alt-rotation": "rotation into alts",
             "btc-dominance": "BTC holds up best", "high-volatility": "high volatility", "calm": "calm market"}
    mood = next((words[t] for t in tags if t in words), "mixed market")
    p = facts["period"]
    title = f"BTC {'+' if btc and not btc.startswith('-') else ''}{btc or '—'} % · {br or '—'} % of pairs up · {mood}"
    lead = facts["leaders"][0] if facts["leaders"] else None
    lag = facts["laggards"][0] if facts["laggards"] else None
    summary = [
        f"BTC moved {btc or '—'} % over the {p['cadence']} to {p['end']:%H:%M} UTC; volatility was "
        f"{facts['btc']['volatility_vs_7d'] or '—'}× its 7-day norm.",
        f"{br or '—'} % of the {p['pairs']} pairs rose and {facts['breadth']['beating_btc_pct'] or '—'} % beat BTC.",
        (f"Best against BTC: {lead['symbol']} ({lead['excess_pp']} pp); worst: {lag['symbol']} ({lag['excess_pp']} pp)."
         if lead and lag else "Not enough pairs to rank leaders and laggards."),
    ]
    captions = {"radar": "Where every pair ended the period against BTC.",
                "btc-breadth": "BTC price and the share of pairs up since the start of the period.",
                "leaders": "The five pairs furthest above and below BTC.",
                "distribution": "How the returns of all pairs were spread."}
    return title, summary, [{"id": c, "caption": captions[c]} for c in DATA_ONLY_CHARTS]


async def previous_reports(db: AsyncSession, cadence: str, end: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = (await db.scalars(select(MarketReport).where(
        MarketReport.cadence == cadence, MarketReport.period_end < end, MarketReport.period_end >= end - F.LOOKBACK,
        MarketReport.status.in_(("PUBLISHED", "DATA_ONLY"))).order_by(MarketReport.period_end.desc()))).all()
    metrics = [F.report_metrics(r) for r in rows]
    return metrics[:2], metrics


async def build_report(db: AsyncSession, cadence: str, end: datetime,
                       writer: Callable[[AsyncSession, MarketReport, F.Result], Awaitable[None]] | None = None) -> MarketReport:
    """Facts + charts for one period; DATA_ONLY unless `writer` (MR.2, LLM) fills the text."""
    prev2, prev7 = await previous_reports(db, cadence, end)
    raw = await F.load(db, cadence, end, prev2, prev7)
    result = await asyncio.to_thread(F.compute, raw)
    start, _ = F.period_bounds(cadence, end)
    title, summary, captions = data_only_text(result.facts)
    report = MarketReport(
        cadence=cadence, period_start=start, period_end=end, status="DATA_ONLY", generated_by="data-only",
        title=title, summary=summary, sections=[],
        charts=plain([{**c, "data": result.charts.get(c["id"])} for c in captions if result.charts.get(c["id"]) is not None]),
        facts=plain(result.facts | {"charts_available": sorted(result.charts)}),
        tags=[{"tag": t, "source": "rules"} for t in result.facts["regime"]["tags"]],
        btc_return_pct=_dec(result.facts["btc"]["return_pct"]), breadth_pct=_dec(result.facts["breadth"]["up_pct"]),
        dispersion_pp=_dec(result.facts["dispersion"]["iqr_pp"]),
        btc_vol_ratio=_dec(result.facts["btc"]["volatility_vs_7d"]), volume_ratio=_dec(result.facts["volume"]["vs_7d"]))
    if writer is not None:
        await writer(db, report, result)
    return report


async def active_series(db: AsyncSession) -> list[str]:
    from app.domain.ai.models import AiSettings
    s = await db.get(AiSettings, "default")
    series = getattr(s, "market_series", None) if s is not None else None
    return [c for c in (series if series is not None else DEFAULT_SERIES) if c in F.CADENCES]


async def run_once(sessions: async_sessionmaker[AsyncSession], now: datetime, writer=None) -> list[MarketReport]:
    """Generate what is due now; returns the reports written (tests, Generate now)."""
    written: list[MarketReport] = []
    async with sessions() as db:
        series = await active_series(db)
    for cadence in series:
        end = F.last_closed_end(cadence, now)
        if now < end + DELAY:
            end -= timedelta(minutes=F.CADENCES[cadence])
        async with sessions() as db:
            if await db.scalar(select(MarketReport.id).where(MarketReport.cadence == cadence, MarketReport.period_end == end)):
                continue
            last = await db.scalar(select(MarketReport.period_end).where(MarketReport.cadence == cadence)
                                   .order_by(MarketReport.period_end.desc()).limit(1))
            if last is not None:      # periods skipped while the worker was down
                gap = last + timedelta(minutes=F.CADENCES[cadence])
                while gap < end:
                    db.add(MarketReport(cadence=cadence, period_start=gap - timedelta(minutes=F.CADENCES[cadence]),
                                        period_end=gap, status="MISSED"))
                    gap += timedelta(minutes=F.CADENCES[cadence])
            try:
                report = await build_report(db, cadence, end, writer)
                db.add(report)
                await db.commit()
                written.append(report)
                log.info("market report %s %s: %s (%s)", cadence, end.isoformat(), report.status, report.title)
            except IntegrityError:
                await db.rollback()           # generated meanwhile by another process: never overwritten
            except Exception as exc:  # noqa: BLE001 — a failed report never stops the worker
                await db.rollback()
                log.exception("market report %s %s failed", cadence, end.isoformat())
                async with sessions() as db2:
                    db2.add(MarketReport(cadence=cadence, period_start=end - timedelta(minutes=F.CADENCES[cadence]),
                                         period_end=end, status="FAILED", error_detail=f"{type(exc).__name__}: {exc}"[:500]))
                    try:
                        await db2.commit()
                    except IntegrityError:
                        await db2.rollback()
    return written


class MarketReportScheduler:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], writer=None, clock=utcnow) -> None:
        self.sessions, self.writer, self.clock = sessions, writer, clock

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await run_once(self.sessions, self.clock(), self.writer)
            except Exception:  # noqa: BLE001
                log.exception("market report scheduler tick failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=TICK_SECONDS)
            except asyncio.TimeoutError:
                pass
