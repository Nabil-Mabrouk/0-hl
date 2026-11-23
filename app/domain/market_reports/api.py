"""Public market reports API — spec 03-ecrans/UI-18 « API » (ADR-030).

No authentication, cached 60 s per process, no absolute amount (ADR-028). The list never carries the
facts or chart data; the detail carries everything, with the previous and next report of the series.
"""

from __future__ import annotations

import time
import uuid
from datetime import date, datetime, time as dtime, timedelta, timezone
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Float, Text, and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.domain.market_reports.facts import CADENCES, TAGS
from app.domain.market_reports.models import MarketReport
from app.domain.trading.api.common import DomainRoute, ok
from app.domain.trading.errors import NotFound, ValidationFailed

router = APIRouter(prefix="/api/v1/public/market", tags=["0-hl market reports"], route_class=DomainRoute)
DB = Annotated[AsyncSession, Depends(get_db)]
CACHE_SECONDS = 60.0
_cache: dict[str, tuple[float, Any]] = {}
VISIBLE = ("PUBLISHED", "DATA_ONLY", "MISSED")
# numeric columns are compared as numbers on every database (Dec is TEXT on SQLite)
SORTS = {"period_end": MarketReport.period_end, "btc_return": cast(MarketReport.btc_return_pct, Float),
         "breadth": cast(MarketReport.breadth_pct, Float)}


def _money(v: Decimal | None) -> str | None:
    return None if v is None else format(v, "f")


def summary_json(r: MarketReport) -> dict[str, Any]:
    return {"id": r.id, "cadence": r.cadence, "period_start": r.period_start, "period_end": r.period_end,
            "status": r.status, "generated_by": r.generated_by, "title": r.title, "summary": r.summary,
            "tags": r.tags, "btc_return_pct": _money(r.btc_return_pct), "breadth_pct": _money(r.breadth_pct),
            "dispersion_pp": _money(r.dispersion_pp), "btc_vol_ratio": _money(r.btc_vol_ratio),
            "volume_ratio": _money(r.volume_ratio)}


def detail_json(r: MarketReport, prev_id: uuid.UUID | None, next_id: uuid.UUID | None) -> dict[str, Any]:
    return summary_json(r) | {"sections": r.sections, "charts": r.charts, "facts": r.facts, "model": r.model,
                              "previous_id": prev_id, "next_id": next_id}


async def _cached(key: str, make) -> Any:
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = await make()
    _cache[key] = (time.monotonic(), value)
    return value


def _day(d: date) -> tuple[datetime, datetime]:
    start = datetime.combine(d, dtime(0), tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


@router.get("/reports")
async def list_reports(db: DB, cadence: str | None = None, date_: date | None = Query(None, alias="date"),
                       since: datetime | None = Query(None, alias="from"), until: datetime | None = Query(None, alias="to"),
                       tag: str | None = None, q: str | None = Query(None, max_length=100),
                       btc_min: Decimal | None = None, btc_max: Decimal | None = None,
                       breadth_min: Decimal | None = None, breadth_max: Decimal | None = None,
                       sort: str = "period_end", order: str = "desc",
                       limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    if cadence is not None and cadence not in CADENCES:
        raise ValidationFailed(f"cadence must be one of {', '.join(CADENCES)}")
    if tag is not None and tag not in TAGS:
        raise ValidationFailed("unknown situation tag")
    if sort not in SORTS or order not in ("asc", "desc"):
        raise ValidationFailed("sort: period_end | btc_return | breadth; order: asc | desc")

    async def make():
        conds: list[Any] = [MarketReport.status.in_(VISIBLE)]
        if cadence:
            conds.append(MarketReport.cadence == cadence)
        if date_:
            a, b = _day(date_)
            conds += [MarketReport.period_end > a, MarketReport.period_end <= b]
        if since:
            conds.append(MarketReport.period_end >= since)
        if until:
            conds.append(MarketReport.period_end <= until)
        if tag:
            conds.append(cast(MarketReport.tags, Text).like(f'%"tag": "{tag}"%') | cast(MarketReport.tags, Text).like(f'%"tag":"{tag}"%'))
        if q:
            like = f"%{q.lower()}%"
            conds.append(or_(func.lower(MarketReport.title).like(like), func.lower(cast(MarketReport.summary, Text)).like(like)))
        for col, lo, hi in ((MarketReport.btc_return_pct, btc_min, btc_max), (MarketReport.breadth_pct, breadth_min, breadth_max)):
            num = cast(col, Float)
            if lo is not None:
                conds.append(num >= float(lo))
            if hi is not None:
                conds.append(num <= float(hi))
        where = and_(*conds)
        col = SORTS[sort]
        total = await db.scalar(select(func.count()).select_from(MarketReport).where(where)) or 0
        rows = (await db.scalars(select(MarketReport).where(where)
                                 .order_by(col.asc() if order == "asc" else col.desc(), MarketReport.period_end.desc())
                                 .limit(limit).offset(offset))).all()
        return {"items": [summary_json(r) for r in rows], "total": total, "limit": limit, "offset": offset}

    key = f"list:{cadence}:{date_}:{since}:{until}:{tag}:{q}:{btc_min}:{btc_max}:{breadth_min}:{breadth_max}:{sort}:{order}:{limit}:{offset}"
    return ok(await _cached(key, make), headers={"Cache-Control": "public, max-age=60"})


@router.get("/reports/latest")
async def latest(db: DB, cadence: str = "4h"):
    if cadence not in CADENCES:
        raise ValidationFailed(f"cadence must be one of {', '.join(CADENCES)}")

    async def make():
        r = await db.scalar(select(MarketReport).where(MarketReport.cadence == cadence,
                                                       MarketReport.status.in_(("PUBLISHED", "DATA_ONLY")))
                            .order_by(MarketReport.period_end.desc()).limit(1))
        return None if r is None else summary_json(r) | {"charts": r.charts}

    data = await _cached(f"latest:{cadence}", make)
    if data is None:
        raise NotFound(f"no {cadence} report yet")
    return ok(data, headers={"Cache-Control": "public, max-age=60"})


@router.get("/reports/{report_id}")
async def report(report_id: uuid.UUID, db: DB):
    async def make():
        r = await db.get(MarketReport, report_id)
        if r is None or r.status not in VISIBLE:
            return None
        series = and_(MarketReport.cadence == r.cadence, MarketReport.status.in_(("PUBLISHED", "DATA_ONLY")))
        prev_id = await db.scalar(select(MarketReport.id).where(series, MarketReport.period_end < r.period_end)
                                  .order_by(MarketReport.period_end.desc()).limit(1))
        next_id = await db.scalar(select(MarketReport.id).where(series, MarketReport.period_end > r.period_end)
                                  .order_by(MarketReport.period_end.asc()).limit(1))
        return detail_json(r, prev_id, next_id)

    data = await _cached(f"one:{report_id}", make)
    if data is None:
        raise NotFound("unknown report")
    return ok(data, headers={"Cache-Control": "public, max-age=60"})
