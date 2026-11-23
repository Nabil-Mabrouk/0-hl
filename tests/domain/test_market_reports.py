"""Market reports, lot MR.1 — spec 03-ecrans/UI-18 (ADR-030): T-MR-01 facts, T-MR-02 tags,
T-MR-03 schedule, and the public read API (T-MR-06, pulled forward)."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.core.database import get_db
from app.domain.market_reports import api as report_api
from app.domain.market_reports import facts as F
from app.domain.market_reports.models import MarketReport
from app.domain.market_reports.scheduler import build_report, run_once
from app.domain.trading.models import BookSnapshot, Instrument, TradeMinute

pytestmark = pytest.mark.anyio

END = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)
START = END - timedelta(hours=4)


def _instr(s: str) -> Instrument:
    return Instrument(id=f"kraken_spot:{s}/USD", venue="kraken_spot", symbol=f"{s}/USD", base=s, quote="USD",
                      pair_decimals=2, lot_decimals=4, status="online", in_universe=True, volume_24h_usd=D(1e8))


def _m(s: str, t: datetime, close: float, notional: float = 1000) -> TradeMinute:
    return TradeMinute(instrument_id=f"kraken_spot:{s}/USD", minute_start=t, source="live", notional_usd=D(str(notional)),
                       trade_count=3, volume_base=D(1), close=D(str(round(close, 6))))


# 4 h returns: BTC −2 %, ETH −3 %, then six pairs; DOGE has a volume spike, SUI a fast drop
PATHS = {"BTC": -0.02, "ETH": -0.03, "TRX": 0.01, "XMR": 0.0, "SOL": -0.04, "ADA": -0.05, "DOGE": -0.025, "SUI": -0.06}


async def _world(db, *, weeks_of_btc: bool = True):
    for s in PATHS:
        db.add(_instr(s))
    t0 = START - timedelta(hours=1)
    for s, r in PATHS.items():
        for i in range(300):                                  # 1 h before + the 4 h window
            t = t0 + timedelta(minutes=i)
            k = max(0, i - 60) / 239
            wobble = 0.001 * (1 if i % 2 else -1) * (2 if s in ("SOL", "ADA", "SUI") else 1)
            close = 100 * (1 + r * k) * (1 + wobble)
            if s == "SUI" and 150 <= i < 170:
                close *= 0.94                                  # fast drop, then back
            notional = 1000 * (8 if s == "DOGE" and 120 <= i < 125 else 1)
            db.add(_m(s, t, close, notional))
    if weeks_of_btc:                                           # 7-day reference: calmer BTC, smaller volume
        for i in range(60 * 24 * 2):
            t = START - timedelta(hours=1, minutes=1 + i)
            db.add(_m("BTC", t, 100 * (1 + 0.0005 * (1 if i % 2 else -1)), 500))
    for i in range(0, 240, 30):
        t = START + timedelta(minutes=i)
        db.add(BookSnapshot(instrument_id="kraken_spot:BTC/USD", minute_start=t, captured_at=t, best_bid=D(100),
                            best_ask=D("100.05"), spread_bps=D("5"), bids=[["100", "10"]], asks=[["100.05", "10"]]))
    await db.flush()


# T-MR-01 -------------------------------------------------------------------------------------
async def test_facts_and_charts_of_a_period(session_factory):
    async with session_factory() as db:
        await _world(db)
        report = await build_report(db, "4h", END)
    f = report.facts
    assert f["period"]["pairs"] == 7 and f["btc"]["return_pct"] == "-2.00"
    assert f["eth"]["excess_pp"] == "-1.00"
    assert f["breadth"]["up_pct"] == "14.3"                    # TRX only
    assert [x["symbol"] for x in f["leaders"]][:2] == ["TRX/USD", "XMR/USD"]
    assert f["leaders"][0]["excess_pp"] == "3.00"
    assert f["laggards"][0]["symbol"] == "SUI/USD"
    assert f["btc"]["volatility_vs_7d"] is not None and float(f["btc"]["volatility_vs_7d"]) > 1
    assert any(e["type"] == "volume_spike" and e["symbol"] == "DOGE/USD" for e in f["events"])
    assert any(e["type"] == "fast_drop" and e["symbol"] == "SUI/USD" for e in f["events"])
    assert f["liquidity"]["spread_bps_median"] == "5.0"
    assert "btc-down" in f["regime"]["tags"] and "broad-selloff" in f["regime"]["tags"]
    assert report.status == "DATA_ONLY" and report.generated_by == "data-only"
    assert report.title.startswith("BTC -2.00 % · 14.3 % of pairs up")
    assert len(report.summary) == 3
    assert [c["id"] for c in report.charts] == ["radar", "btc-breadth", "leaders", "distribution"]
    pts = report.charts[1]["data"]["points"]
    assert len(pts) == 120 and pts[-1]["up_pct"] == "14.3"
    assert set(f["charts_available"]) >= {"radar", "btc-breadth", "leaders", "volatility", "distribution", "volume",
                                          "liquidity", "bots", "trend3"}
    assert report.btc_return_pct == D("-2.00") and report.breadth_pct == D("14.3")


async def test_no_data_never_crashes(session_factory):
    async with session_factory() as db:
        report = await build_report(db, "24h", END)
    assert report.status == "DATA_ONLY" and report.facts["btc"]["return_pct"] is None
    assert report.title.startswith("BTC — %")


async def test_facts_ignore_data_after_the_period(session_factory):
    async with session_factory() as db:
        await _world(db)
        before = (await build_report(db, "4h", END)).facts
        db.add(_m("BTC", END + timedelta(minutes=1), 1))       # a crash after the end
        await db.flush()
        after = (await build_report(db, "4h", END)).facts
    assert before["btc"] == after["btc"] and before["breadth"] == after["breadth"]


# T-MR-02 -------------------------------------------------------------------------------------
def test_situation_tags_follow_the_rules():
    f = {"btc": {"return_pct": "2.10", "volatility_vs_7d": "0.65"}, "breadth": {"up_pct": "75.0", "beating_btc_pct": "62.0"},
         "volume": {"vs_7d": "1.60"}, "dispersion": {"iqr_vs_7d": "2.00"}, "liquidity": {"spread_vs_7d": "1.0"}}
    assert F.situation_tags(f, 240) == ["btc-up", "broad-rally", "alt-rotation", "calm", "volume-surge", "high-dispersion"]
    f["btc"]["return_pct"] = "1.90"
    assert F.situation_tags(f, 1440)[0] == "btc-flat"         # ±2 % in 24 h
    f["btc"]["return_pct"] = "-0.60"
    assert F.situation_tags(f, 60)[0] == "btc-down"           # ±0.5 % in 1 h
    assert set(F.situation_tags(f, 60)) <= set(F.TAGS)


def test_period_alignment():
    now = datetime(2026, 11, 23, 13, 1, tzinfo=timezone.utc)
    assert F.last_closed_end("1h", now) == datetime(2026, 11, 23, 13, 0, tzinfo=timezone.utc)
    assert F.last_closed_end("4h", now) == datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)
    assert F.last_closed_end("24h", now) == datetime(2026, 11, 23, 0, 0, tzinfo=timezone.utc)


# T-MR-03 -------------------------------------------------------------------------------------
async def test_one_report_per_period_and_missed_gaps(session_factory):
    async with session_factory() as db:
        await _world(db)
        await db.commit()
    early = await run_once(session_factory, END + timedelta(minutes=1))      # 12:01: 12:00 not settled yet
    assert {(r.cadence, r.period_end) for r in early} == {("4h", END - timedelta(hours=4)), ("24h", END - timedelta(hours=12))}
    done = await run_once(session_factory, END + timedelta(minutes=2, seconds=5))
    assert [(r.cadence, r.period_end) for r in done] == [("4h", END)]
    assert await run_once(session_factory, END + timedelta(minutes=3)) == []   # never twice
    later = END + timedelta(hours=12, minutes=3)                                # worker down for 12 h
    done = await run_once(session_factory, later)
    assert ("4h", END + timedelta(hours=12)) in [(r.cadence, r.period_end) for r in done]
    async with session_factory() as db:
        rows = (await db.scalars(select(MarketReport).where(MarketReport.cadence == "4h").order_by(MarketReport.period_end))).all()
    assert [r.status for r in rows] == ["DATA_ONLY", "DATA_ONLY", "MISSED", "MISSED", "DATA_ONLY"]
    # the newest report compares itself with the two previous published ones
    assert [p["period_end"][:16] for p in rows[-1].facts["changes"]["previous"]] == [
        rows[1].period_end.isoformat()[:16], rows[0].period_end.isoformat()[:16]]


# public API ----------------------------------------------------------------------------------
def _client(session_factory) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(report_api.router)

    async def _db():
        async with session_factory() as s:
            yield s
    app.dependency_overrides[get_db] = _db
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_public_reports_filters_and_navigation(session_factory):
    report_api._cache.clear()
    async with session_factory() as db:
        await _world(db)
        await db.commit()
    await run_once(session_factory, END + timedelta(minutes=1))
    await run_once(session_factory, END + timedelta(minutes=3))
    async with _client(session_factory) as c:
        all4 = (await c.get("/api/v1/public/market/reports?cadence=4h")).json()
        tagged = (await c.get("/api/v1/public/market/reports?tag=broad-selloff")).json()
        down = (await c.get("/api/v1/public/market/reports?btc_max=-1")).json()
        day = (await c.get("/api/v1/public/market/reports?date=2026-11-23&cadence=4h")).json()
        text = (await c.get("/api/v1/public/market/reports?q=pairs%20up")).json()
        latest = await c.get("/api/v1/public/market/reports/latest?cadence=4h")
        bad = await c.get("/api/v1/public/market/reports?tag=moon")
        missing = await c.get("/api/v1/public/market/reports/00000000-0000-0000-0000-000000000000")
        newest = all4["items"][0]
        detail = (await c.get(f"/api/v1/public/market/reports/{newest['id']}")).json()
    assert all4["total"] == 2 and newest["period_end"].startswith("2026-11-23T12:00")
    assert "facts" not in newest and "charts" not in newest
    assert any(i["id"] == newest["id"] for i in tagged["items"])
    assert newest["id"] in [i["id"] for i in down["items"]]
    assert day["total"] == 2 and text["total"] >= 1
    assert latest.status_code == 200 and latest.json()["id"] == newest["id"] and latest.json()["charts"]
    assert bad.status_code == 422 and missing.status_code == 404
    assert detail["previous_id"] == all4["items"][1]["id"] and detail["next_id"] is None
    assert detail["facts"]["version"] == "facts/1"
