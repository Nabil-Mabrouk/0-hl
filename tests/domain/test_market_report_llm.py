"""Market reports, lot MR.2 — spec 03-ecrans/UI-18 « Appel au modèle », « Vérification » (ADR-030):
T-MR-04 verification and correction, T-MR-05 budget and journal, settings and Generate now."""

import asyncio
import json
from datetime import timedelta
from decimal import Decimal as D

import pytest
from sqlalchemy import select

from app.domain.ai import api as ai_api
from app.domain.ai.models import AiRun, AiSettings, AiStep
from app.domain.ai.proxy import ChatResult
from app.domain.market_reports import writer as W
from app.domain.market_reports.models import MarketReport
from app.domain.market_reports.scheduler import generate
from app.domain.market_reports.verify import check

from .test_ai_services import FakeProxy, World, _h
from .test_market_reports import END, _world

pytestmark = pytest.mark.anyio
NOW = END + timedelta(minutes=3)
FILLER = ("The market moved in a steady way through the period and most pairs followed the same direction "
          "while a few names stood apart from the rest of the market. ")


def valid(facts: dict, *, extra: str = "", tags=("high-dispersion",)) -> dict:
    btc, up, lead = facts["btc"]["return_pct"], facts["breadth"]["up_pct"], facts["leaders"][0]
    body = FILLER * 9
    return {
        "title": f"BTC {btc} % while {up} % of pairs rise",
        "summary": [f"BTC moved {btc} % over the period.", f"Only {up} % of pairs rose.",
                    f"{lead['symbol']} led at {lead['excess_pp']} pp against BTC.{extra}"],
        "sections": [
            {"id": "regime", "heading": "A falling market", "body": body + "\n\n{{chart:btc-breadth}}"},
            {"id": "leaders", "heading": "Who held up", "body": body + "\n\n{{chart:leaders}}"},
            {"id": "volatility", "heading": "Spread of returns", "body": body + "\n\n{{chart:distribution}}"},
        ],
        "charts": [{"id": "btc-breadth", "caption": "BTC and breadth."}, {"id": "leaders", "caption": "Leaders."},
                   {"id": "distribution", "caption": "Returns."}],
        "tags": list(tags),
    }


def reply(obj: dict | str, cost: D | None = D("0.0021")) -> ChatResult:
    text = obj if isinstance(obj, str) else json.dumps(obj)
    return ChatResult(True, 200, 900, message={"role": "assistant", "content": text}, finish_reason="stop",
                      tokens_in=6000, tokens_out=1500, cost_usd=cost)


async def _facts(session_factory) -> dict:
    """Facts of the test period, as the writer will see them (to build answers that pass or fail)."""
    from app.domain.ai.runner import plain
    from app.domain.market_reports.scheduler import build_report
    async with session_factory() as db:
        r = await build_report(db, "4h", END)
        await db.rollback()
    return plain(r.facts)


async def _setup(session_factory):
    async with session_factory() as db:
        await _world(db)
        await db.commit()
    return await _facts(session_factory)


async def _report(session_factory) -> MarketReport:
    async with session_factory() as db:
        return await db.scalar(select(MarketReport).where(MarketReport.period_end == END, MarketReport.cadence == "4h"))


# T-MR-04 ---------------------------------------------------------------------------------------
async def test_llm_report_is_published_and_journaled(session_factory):
    facts = await _setup(session_factory)
    proxy = FakeProxy([reply(valid(facts))])
    await generate(session_factory, "4h", NOW, W.LlmWriter(proxy))
    r = await _report(session_factory)
    assert r.status == "PUBLISHED" and r.generated_by == "llm" and r.title.startswith("BTC -2.00 %")
    assert [c["id"] for c in r.charts] == ["btc-breadth", "leaders", "distribution"] and r.charts[0]["data"]["points"]
    assert {"tag": "high-dispersion", "source": "model"} in r.tags and {"tag": "btc-down", "source": "rules"} in r.tags
    assert r.cost_usd == D("0.0021") and r.model == "groq-gpt-oss-120b"
    call = proxy.calls[0]
    assert call["response_format"] == {"type": "json_object"} and call["tools"] is None
    assert '"previous_reports"' in call["messages"][1]["content"]
    async with session_factory() as db:
        run = await db.get(AiRun, r.ai_run_id)
        steps = (await db.scalars(select(AiStep).where(AiStep.run_id == run.id).order_by(AiStep.seq))).all()
    assert run.status == "SUCCEEDED" and run.user_id is None and run.service_id == "market-report"
    assert [s.kind for s in steps] == ["MODEL_CALL", "CHECK"] and steps[1].detail == {"errors": []}


async def test_invented_number_is_corrected_once(session_factory):
    facts = await _setup(session_factory)
    proxy = FakeProxy([reply(valid(facts, extra=" Volume reached 99.99 % of normal.")), reply(valid(facts))])
    await generate(session_factory, "4h", NOW, W.LlmWriter(proxy))
    r = await _report(session_factory)
    assert r.status == "PUBLISHED" and len(proxy.calls) == 2
    correction = proxy.calls[1]["messages"][-1]["content"]
    assert "number 99.99 is not in the facts" in correction


async def test_two_bad_answers_keep_the_data_only_report(session_factory):
    facts = await _setup(session_factory)
    bad = valid(facts, extra=" Buy it now before the price target of 99.99.")
    proxy = FakeProxy([reply(bad), reply("not json at all")])
    await generate(session_factory, "4h", NOW, W.LlmWriter(proxy))
    r = await _report(session_factory)
    assert r.status == "DATA_ONLY" and r.generated_by == "data-only" and "CHECK_FAILED" in r.error_detail
    async with session_factory() as db:
        run = await db.get(AiRun, r.ai_run_id)
    assert run.status == "FAILED" and run.model_calls == 2


def test_verification_rules():
    facts = {"btc": {"return_pct": "-1.84"}, "period": {"end": "2026-11-23T12:00:00+00:00"}}
    base = {"title": "BTC -1.84 % at 12:00", "summary": ["a", "b", "c"],
            "sections": [{"id": "regime", "heading": "h", "body": FILLER * 12 + "{{chart:radar}}\n{{chart:leaders}}\n{{chart:bots}}"}],
            "charts": [{"id": "radar", "caption": "x"}, {"id": "leaders", "caption": "x"}, {"id": "bots", "caption": "x"}],
            "tags": []}
    assert check(base, facts, [], {"radar", "leaders", "bots"}) == []
    assert check({**base, "title": "BTC -1.8 % at 12:00"}, facts, [], {"radar", "leaders", "bots"}) == []   # rounded
    errs = check({**base, "title": "BTC will reach 90,000 at 13:00 <b>now</b>", "tags": ["moon", "calm", "btc-up"]},
                 facts, [], {"radar", "leaders"})
    joined = " | ".join(errs)
    for expected in ("number 90,000", "time 13:00", "price prediction", "HTML", "tags:", "chart bots"):
        assert expected in joined, expected


# T-MR-05 ---------------------------------------------------------------------------------------
async def test_budget_exhausted_means_no_call(session_factory):
    await _setup(session_factory)
    async with session_factory() as db:
        db.add(AiSettings(id="default", daily_budget_usd=D("0"), models={}, prices={
            "groq-gpt-oss-120b": {"input": "0.15", "output": "0.60"}}, version=1))
        await db.commit()
    proxy = FakeProxy([])
    await generate(session_factory, "4h", NOW, W.LlmWriter(proxy))
    r = await _report(session_factory)
    assert proxy.calls == [] and r.status == "DATA_ONLY"
    async with session_factory() as db:
        assert (await db.get(AiRun, r.ai_run_id)).status == "BUDGET_BLOCKED"


async def test_rate_limit_is_retried_once(session_factory, monkeypatch):
    monkeypatch.setattr(W, "RETRY_429_SECONDS", 0)
    facts = await _setup(session_factory)
    proxy = FakeProxy([ChatResult(False, 429, 5, error_code="RATE_LIMITED", error_detail="429"), reply(valid(facts))])
    await generate(session_factory, "4h", NOW, W.LlmWriter(proxy))
    assert (await _report(session_factory)).status == "PUBLISHED" and len(proxy.calls) == 2


async def test_series_settings_and_generate_now(session_factory, monkeypatch):
    w = await World().build(session_factory, monkeypatch, [])
    r = await w.c.get("/api/v1/ai/settings", headers=_h(w.admin.id))
    assert r.json()["market_series"] == ["4h", "24h"] and r.json()["models"]["market-report"] == "groq-gpt-oss-120b"
    r = await w.c.patch("/api/v1/ai/settings", json={"market_series": ["24h", "1h"],
                                                     "models": {"market-report": "groq-gpt-oss-20b"}},
                        headers=_h(w.admin.id))
    assert r.status_code == 200 and r.json()["market_series"] == ["1h", "24h"]
    assert (await w.c.patch("/api/v1/ai/settings", json={"market_series": ["2h"]}, headers=_h(w.admin.id))).status_code == 422
    assert (await w.c.post("/api/v1/ai/market-reports/generate", json={"cadence": "24h"},
                           headers=_h(w.analyst.id))).status_code == 403
    g = await w.c.post("/api/v1/ai/market-reports/generate", json={"cadence": "24h"}, headers=_h(w.admin.id))
    assert g.status_code == 202
    await asyncio.gather(*ai_api._report_tasks)
    async with session_factory() as db:
        assert await db.scalar(select(MarketReport).where(MarketReport.cadence == "24h")) is not None
