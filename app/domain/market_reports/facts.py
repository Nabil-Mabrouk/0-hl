"""Fact sheet and chart data of a market report — spec 03-ecrans/UI-18 « Dossier de faits »,
« Étiquettes de situation », « Catalogue de graphiques » (ADR-030).

Deterministic, no LLM. `load()` reads the database asynchronously, then `compute()` (pure,
CPU-bound) runs in a thread so that the Kraken feed sharing the worker's event loop is never held
up. Numbers are strings with fixed decimals (the domain JSON refuses floats).
"""

from __future__ import annotations

import math
import statistics
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.trading.agents.features import BTC_IID
from app.domain.trading.enums import BotStatus
from app.domain.trading.lifecycle import initial_capital
from app.domain.trading.market.radar import compute_radar, pair_metrics
from app.domain.trading.models import (
    Bot, BookSnapshot, DomainEvent, EquitySnapshot, Instrument, Position, Strategy, StrategyVersion, TradeMinute,
)

FACTS_VERSION = "facts/1"
CADENCES = {"1h": 60, "4h": 240, "24h": 1440}
ETH_IID = "kraken_spot:ETH/USD"
MINUTE = timedelta(minutes=1)
LOOKBACK = timedelta(days=7)
VOLUME_BUCKET = {60: 5, 240: 30, 1440: 120}          # minutes per bar of the `volume` chart
BTC_THRESHOLD = {60: 0.5, 240: 1.0, 1440: 2.0}         # % for btc-up / btc-down
TAGS = ["btc-up", "btc-down", "btc-flat", "broad-rally", "broad-selloff", "alt-rotation", "btc-dominance",
        "high-volatility", "calm", "volume-surge", "thin-volume", "high-dispersion", "wide-spreads"]


def q(x: float | Decimal | None, places: int = 2) -> str | None:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return None
    return format(Decimal(str(x)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP), "f")


def sym(iid: str) -> str:
    return iid.split(":", 1)[1]


def period_bounds(cadence: str, end: datetime) -> tuple[datetime, datetime]:
    return end - MINUTE * CADENCES[cadence], end


def last_closed_end(cadence: str, now: datetime) -> datetime:
    """End of the last closed period, UTC-aligned (every hour; 00/04/08/12/16/20; 00:00)."""
    hours = CADENCES[cadence] // 60
    base = now.replace(minute=0, second=0, microsecond=0)
    return base - timedelta(hours=base.hour % hours)


# ---------------------------------------------------------------------------------------- data
Minute = tuple[Decimal | None, Decimal, int]              # (close, notional USD, trades)


@dataclass
class Raw:
    cadence: str
    start: datetime
    end: datetime
    pairs: dict[str, dict[datetime, Minute]]
    start_close: dict[str, Decimal | None]
    total_notional: dict[datetime, Decimal]                # 7 days, all pairs, live, per minute
    btc_closes_7d: dict[datetime, Decimal]
    books: dict[str, list[tuple[datetime, Decimal, Decimal]]]   # top-5 pairs: (minute, spread bps, depth USD)
    offline: list[dict[str, Any]] = field(default_factory=list)
    bots: list[dict[str, Any]] = field(default_factory=list)
    previous: list[dict[str, Any]] = field(default_factory=list)      # the two previous reports, newest first
    previous_7d: list[dict[str, Any]] = field(default_factory=list)   # reports of the series over 7 days
    radar: dict[str, Any] | None = None


def report_metrics(r: Any) -> dict[str, Any]:
    """What later reports need from an earlier one (comparisons and 7-day references)."""
    liq = ((r.facts or {}).get("liquidity") or {})
    return {"period_end": r.period_end, "title": r.title, "summary": r.summary, "tags": r.tags,
            "btc_return_pct": None if r.btc_return_pct is None else format(r.btc_return_pct, "f"),
            "breadth_pct": None if r.breadth_pct is None else format(r.breadth_pct, "f"),
            "dispersion_pp": None if r.dispersion_pp is None else format(r.dispersion_pp, "f"),
            "spread_bps": liq.get("spread_bps_median")}


async def _load_pairs(db: AsyncSession, ids: list[str], start: datetime, end: datetime):
    rows = (await db.execute(select(TradeMinute.instrument_id, TradeMinute.minute_start, TradeMinute.source,
                                    TradeMinute.close, TradeMinute.notional_usd, TradeMinute.trade_count)
                             .where(TradeMinute.instrument_id.in_(ids),
                                    TradeMinute.minute_start >= start - timedelta(hours=1),
                                    TradeMinute.minute_start < end))).all()
    pairs: dict[str, dict[datetime, Minute]] = {i: {} for i in ids}
    source: dict[tuple[str, datetime], str] = {}
    for iid, m, src, close, notional, n in rows:
        if source.get((iid, m)) == "live":
            continue
        source[(iid, m)] = src
        pairs[iid][m] = (close, notional, n)
    start_close: dict[str, Decimal | None] = {}
    for iid in ids:
        before = sorted((m, v[0]) for m, v in pairs[iid].items() if m < start and v[0] is not None and v[0] > 0)
        if before:
            start_close[iid] = before[-1][1]
            continue
        start_close[iid] = await db.scalar(select(TradeMinute.close).where(
            TradeMinute.instrument_id == iid, TradeMinute.close.is_not(None), TradeMinute.close > 0,
            TradeMinute.minute_start < start - timedelta(hours=1), TradeMinute.minute_start >= start - timedelta(hours=24))
            .order_by(TradeMinute.minute_start.desc()).limit(1))
    return pairs, start_close


async def _bots(db: AsyncSession, start: datetime, end: datetime) -> list[dict[str, Any]]:
    out = []
    deposits = await initial_capital(db)
    bots = (await db.scalars(select(Bot).where(Bot.status.in_((BotStatus.RUNNING, BotStatus.PAUSED)))
                             .order_by(Bot.name))).all()
    for b in bots:
        sv = await db.get(StrategyVersion, b.strategy_version_id)
        strat = await db.get(Strategy, sv.strategy_id) if sv else None
        signals, refusals = 0, {}
        for p in (await db.scalars(select(DomainEvent.payload).where(
                DomainEvent.bot_id == b.id, DomainEvent.event_type == "SCAN_CYCLE_COMPLETED",
                DomainEvent.occurred_at >= start, DomainEvent.occurred_at < end + MINUTE))).all():
            signals += int(p.get("candidates") or 0)
            for o in (p.get("outcomes") or {}).values():
                if o.get("r") in ("FILTERED", "REFUSED", "NOT_FILLED"):
                    k = str(o.get("why") or o.get("r")).lower()
                    refusals[k] = refusals.get(k, 0) + 1
        opened = await db.scalar(select(func.count()).select_from(Position).where(
            Position.bot_id == b.id, Position.opened_at >= start, Position.opened_at < end)) or 0
        closed = await db.scalar(select(func.count()).select_from(Position).where(
            Position.bot_id == b.id, Position.closed_at >= start, Position.closed_at < end)) or 0

        async def equity_at(t: datetime) -> Decimal | None:
            return await db.scalar(select(EquitySnapshot.equity).where(
                EquitySnapshot.bot_id == b.id, EquitySnapshot.equity.is_not(None), EquitySnapshot.at <= t)
                .order_by(EquitySnapshot.at.desc()).limit(1))

        e0, e1, dep = await equity_at(start), await equity_at(end), deposits.get(b.id)
        out.append({"name": b.name, "strategy": strat.name if strat else None,
                    "strategy_id": sv.strategy_id if sv else None, "signals": signals,
                    "refusals": dict(sorted(refusals.items())), "opened": opened, "closed": closed,
                    "net_pct": q((e1 - e0) / dep * 100) if e0 is not None and e1 is not None and dep else None})
    return out


def _depth(levels: Any) -> Decimal:
    return sum((Decimal(str(p)) * Decimal(str(qty)) for p, qty in (levels or [])[:10]), Decimal(0))


async def load(db: AsyncSession, cadence: str, end: datetime, previous: list[dict[str, Any]],
               previous_7d: list[dict[str, Any]]) -> Raw:
    start, end = period_bounds(cadence, end)
    universe = (await db.scalars(select(Instrument.id).where(Instrument.in_universe))).all()
    ids = sorted(set(universe) | {BTC_IID})
    pairs, start_close = await _load_pairs(db, ids, start, end)
    total = {m: Decimal(v) for m, v in (await db.execute(
        select(TradeMinute.minute_start, func.sum(TradeMinute.notional_usd)).where(
            TradeMinute.source == "live", TradeMinute.minute_start >= end - LOOKBACK, TradeMinute.minute_start < end)
        .group_by(TradeMinute.minute_start))).all()}
    btc7 = {m: c for m, c in (await db.execute(select(TradeMinute.minute_start, TradeMinute.close).where(
        TradeMinute.instrument_id == BTC_IID, TradeMinute.source == "live", TradeMinute.close.is_not(None),
        TradeMinute.minute_start >= end - LOOKBACK - timedelta(hours=1), TradeMinute.minute_start < end))).all()}
    notional = {i: sum((v[1] for m, v in pairs[i].items() if m >= start), Decimal(0)) for i in ids}
    top5 = [i for i, n in sorted(notional.items(), key=lambda kv: -kv[1])[:5] if n > 0]
    books: dict[str, list[tuple[datetime, Decimal, Decimal]]] = {}
    for iid in top5:
        rows = (await db.execute(select(BookSnapshot.minute_start, BookSnapshot.spread_bps, BookSnapshot.bids,
                                        BookSnapshot.asks).where(
            BookSnapshot.instrument_id == iid, BookSnapshot.minute_start >= start, BookSnapshot.minute_start < end)
            .order_by(BookSnapshot.minute_start))).all()
        books[iid] = [(m, s, _depth(bids) + _depth(asks)) for m, s, bids, asks in rows]
    offline = [{"symbol": sym(e.aggregate_id), "at": e.occurred_at, "to": (e.payload or {}).get("to")}
               for e in (await db.scalars(select(DomainEvent).where(
                   DomainEvent.event_type == "INSTRUMENT_STATUS_CHANGED", DomainEvent.occurred_at >= start,
                   DomainEvent.occurred_at < end))).all()
               if (e.payload or {}).get("to") != "online"]
    radar = await compute_radar(db, end + timedelta(seconds=30), CADENCES[cadence])
    return Raw(cadence, start, end, pairs, start_close, total, btc7, books, offline,
               await _bots(db, start, end), previous, previous_7d, radar)


# ---------------------------------------------------------------------------------------- compute
def _median(xs: list[float]) -> float | None:
    return statistics.median(xs) if xs else None


def _window_vol(closes: dict[datetime, Decimal], start: datetime, minutes: int) -> float | None:
    """Stdev of 1-minute log returns on the carried-forward grid (same rule as the radar)."""
    before = [m for m in closes if start - timedelta(hours=1) <= m < start]
    if not before:
        return None
    prev = closes[max(before)]
    rets = []
    for i in range(minutes):
        c = closes.get(start + MINUTE * i, prev)
        rets.append(math.log(float(c / prev)))
        prev = c
    return statistics.stdev(rets) if len(rets) > 1 else None


def _max_drop_1h(series: list[tuple[datetime, Decimal]]) -> tuple[float, datetime | None]:
    """Worst close compared with the highest close of the previous 60 minutes."""
    worst, at = 0.0, None
    window: deque[tuple[datetime, Decimal]] = deque()
    for m, c in series:
        while window and window[0][0] <= m - timedelta(minutes=60):
            window.popleft()
        if window:
            d = float(c / window[0][1]) - 1
            if d < worst:
                worst, at = d, m
        while window and window[-1][1] <= c:
            window.pop()
        window.append((m, c))
    return worst, at


def _num(v: Any) -> float | None:
    return None if v is None else float(v)


def situation_tags(f: dict[str, Any], minutes: int) -> list[str]:
    """Rules of UI-18 « Étiquettes de situation » (thresholds [OPEN], to calibrate on 30 days)."""
    tags: list[str] = []
    btc, thr = _num(f["btc"]["return_pct"]), BTC_THRESHOLD[minutes]
    if btc is not None:
        tags.append("btc-up" if btc >= thr else "btc-down" if btc <= -thr else "btc-flat")
    up, beat = _num(f["breadth"]["up_pct"]), _num(f["breadth"]["beating_btc_pct"])
    if up is not None and up >= 70:
        tags.append("broad-rally")
    if up is not None and up <= 30:
        tags.append("broad-selloff")
    if beat is not None and beat >= 60:
        tags.append("alt-rotation")
    if beat is not None and beat <= 30:
        tags.append("btc-dominance")
    vol = _num(f["btc"]["volatility_vs_7d"])
    if vol is not None and vol >= 1.5:
        tags.append("high-volatility")
    if vol is not None and vol <= 0.7:
        tags.append("calm")
    volume = _num(f["volume"]["vs_7d"])
    if volume is not None and volume >= 1.5:
        tags.append("volume-surge")
    if volume is not None and volume <= 0.6:
        tags.append("thin-volume")
    disp = _num(f["dispersion"]["iqr_vs_7d"])
    if disp is not None and disp >= 2:
        tags.append("high-dispersion")
    spread = _num(f["liquidity"]["spread_vs_7d"])
    if spread is not None and spread >= 2:
        tags.append("wide-spreads")
    return tags


@dataclass
class Result:
    facts: dict[str, Any]
    charts: dict[str, Any]


def compute(raw: Raw) -> Result:
    W = CADENCES[raw.cadence]
    start, end = raw.start, raw.end
    metrics: dict[str, dict[str, Any]] = {}
    for iid, mins in raw.pairs.items():
        closes = {m: v[0] for m, v in mins.items() if m >= start and v[0] is not None}
        traded = {m for m, v in mins.items() if m >= start and v[2] and v[0] is not None}
        m = pair_metrics(closes, traded, raw.start_close.get(iid), start, W)
        if m is not None:
            m["notional"] = sum((v[1] for t, v in mins.items() if t >= start), Decimal(0))
            metrics[iid] = m
    btc = metrics.get(BTC_IID)
    others = {i: m for i, m in metrics.items() if i != BTC_IID}
    excess = {i: (float(m["ret"]) - float(btc["ret"])) * 100 for i, m in others.items()} if btc else {}
    vol_ratio = {i: m["vol"] / btc["vol"] for i, m in others.items()
                 if btc and btc["vol"] > 0 and m["vol"] > 0 and m["minutes_traded"] >= W / 3}
    # leaders and laggards: only pairs traded often enough to be judged (same rule as the radar)
    ranked = sorted((i for i in excess if others[i]["minutes_traded"] >= W / 3), key=lambda i: -excess[i])
    rets = sorted(float(m["ret"]) * 100 for m in others.values())

    def entry(i: str) -> dict[str, Any]:
        return {"symbol": sym(i), "excess_pp": q(excess.get(i)), "vol_ratio": q(vol_ratio.get(i))}

    # 7-day references from earlier windows of the same length
    past_vols, past_volumes = [], []
    k = 1
    while start - MINUTE * W * k >= end - LOOKBACK:
        s = start - MINUTE * W * k
        v = _window_vol(raw.btc_closes_7d, s, W)
        if v:
            past_vols.append(v)
        past_volumes.append(float(sum((raw.total_notional.get(s + MINUTE * i, Decimal(0)) for i in range(W)), Decimal(0))))
        k += 1
    volume_now = float(sum((raw.total_notional.get(start + MINUTE * i, Decimal(0)) for i in range(W)), Decimal(0)))
    ref_vol, ref_volume = _median(past_vols), _median([x for x in past_volumes if x > 0])
    btc_vol = btc["vol"] if btc and btc["vol"] > 0 else None

    # dispersion and spread: 7-day reference from earlier reports of the series
    quart = statistics.quantiles(rets, n=4) if len(rets) >= 4 else None
    iqr = quart[2] - quart[0] if quart else None
    prev_iqr = [float(p["dispersion_pp"]) for p in raw.previous_7d if p.get("dispersion_pp") is not None]
    prev_spread = [float(p["spread_bps"]) for p in raw.previous_7d if p.get("spread_bps") is not None]
    spreads = [float(s) for rows in raw.books.values() for _, s, _ in rows]
    spread_med = _median(spreads)
    depth0 = sum(float(rows[0][2]) for rows in raw.books.values() if rows)
    depth1 = sum(float(rows[-1][2]) for rows in raw.books.values() if rows)

    # events: volume spikes (5-min blocks ≥ 4× the pair's median block) and fast drops (≤ −4 % in 1 h)
    spikes, drops = [], []
    for iid, mins in raw.pairs.items():
        blocks: dict[datetime, Decimal] = {}
        for m, v in mins.items():
            b = m - timedelta(minutes=m.minute % 5)
            blocks[b] = blocks.get(b, Decimal(0)) + v[1]
        base = _median([float(x) for x in blocks.values() if x > 0])
        if base:
            for b, x in blocks.items():
                if b >= start and float(x) >= 4 * base:
                    spikes.append({"type": "volume_spike", "symbol": sym(iid), "at": b, "ratio": q(float(x) / base, 1)})
        drop, at = _max_drop_1h(sorted((m, v[0]) for m, v in mins.items() if v[0] is not None))
        if at is not None and at >= start and drop <= -0.04:
            drops.append({"type": "fast_drop", "symbol": sym(iid), "at": at, "drop_pct": q(drop * 100, 1)})
    spikes = sorted(spikes, key=lambda e: -float(e["ratio"]))[:3]
    drops = sorted(drops, key=lambda e: float(e["drop_pct"]))[:3]
    events = sorted(spikes + drops, key=lambda e: e["at"]) + [{"type": "offline", **o} for o in raw.offline]

    btc_closes = [v[0] for m, v in raw.pairs.get(BTC_IID, {}).items() if m >= start and v[0] is not None]
    eth = metrics.get(ETH_IID)
    facts: dict[str, Any] = {
        "version": FACTS_VERSION,
        "period": {"cadence": raw.cadence, "start": start, "end": end, "pairs": len(others),
                   "pairs_without_data": len(raw.pairs) - len(metrics)},
        "btc": {"return_pct": q(float(btc["ret"]) * 100) if btc else None,
                "high": q(max(btc_closes)) if btc_closes else None, "low": q(min(btc_closes)) if btc_closes else None,
                "volatility_pct_per_min": q(btc_vol * 100, 3) if btc_vol else None,
                "volatility_vs_7d": q(btc_vol / ref_vol) if btc_vol and ref_vol else None},
        "eth": {"return_pct": q(float(eth["ret"]) * 100) if eth else None, "excess_pp": q(excess.get(ETH_IID))},
        "volume": {"vs_7d": q(volume_now / ref_volume) if ref_volume else None},
        "breadth": {"up_pct": q(sum(1 for r in rets if r > 0) / len(rets) * 100, 1) if rets else None,
                    "beating_btc_pct": q(sum(1 for x in excess.values() if x > 0) / len(excess) * 100, 1) if excess else None},
        "dispersion": {"iqr_pp": q(iqr), "median_return_pct": q(statistics.median(rets)) if rets else None,
                       "iqr_vs_7d": q(iqr / statistics.median(prev_iqr)) if iqr and len(prev_iqr) >= 3 and statistics.median(prev_iqr) > 0 else None},
        "leaders": [entry(i) for i in ranked[:5]],
        "laggards": [entry(i) for i in reversed(ranked[-5:])] if len(ranked) > 5 else [],
        "events": events,
        "liquidity": {"pairs": [sym(i) for i in raw.books], "spread_bps_median": q(spread_med, 1),
                      "spread_vs_7d": q(spread_med / statistics.median(prev_spread))
                      if spread_med is not None and len(prev_spread) >= 3 and statistics.median(prev_spread) > 0 else None,
                      "depth_change_pct": q((depth1 / depth0 - 1) * 100, 1) if depth0 else None},
        "bots": raw.bots,
        "changes": {"previous": [{k: p.get(k) for k in ("period_end", "btc_return_pct", "breadth_pct", "dispersion_pp")}
                                 for p in raw.previous[:2]]},
    }
    prev = raw.previous[0] if raw.previous else {}
    if facts["btc"]["return_pct"] is not None and prev.get("btc_return_pct") is not None:
        facts["changes"]["btc_return_delta_pp"] = q(float(facts["btc"]["return_pct"]) - float(prev["btc_return_pct"]))
    if facts["breadth"]["up_pct"] is not None and prev.get("breadth_pct") is not None:
        facts["changes"]["breadth_delta_pp"] = q(float(facts["breadth"]["up_pct"]) - float(prev["breadth_pct"]), 1)
    facts["regime"] = {"tags": situation_tags(facts, W)}
    return Result(facts, _charts(raw, facts, others, vol_ratio))


def _charts(raw: Raw, f: dict[str, Any], others: dict[str, dict[str, Any]],
            vol_ratio: dict[str, float]) -> dict[str, Any]:
    W, start = CADENCES[raw.cadence], raw.start
    out: dict[str, Any] = {}
    if raw.radar is not None:
        out["radar"] = {"window_minutes": W,
                        "pairs": [{k: p.get(k) for k in ("symbol", "excess_pp", "y", "judgeable")} for p in raw.radar.get("pairs", [])],
                        "bots": [{"name": b["name"], "strategy": b["strategy"]["id"], "open_positions": b["open_positions"]}
                                 for b in raw.radar.get("bots", [])]}
    # BTC price and share of pairs up since the start of the period (≤ 120 points)
    step = max(1, W // 120)
    last: dict[str, Decimal | None] = dict(raw.start_close)
    points = []
    for i in range(W):
        t = start + MINUTE * i
        for iid, mins in raw.pairs.items():
            v = mins.get(t)
            if v and v[0] is not None:
                last[iid] = v[0]
        if i % step == step - 1 or i == W - 1:
            up = 0
            for iid in others:
                cur, ref = last.get(iid), raw.start_close.get(iid)
                if cur is not None and ref is not None and cur > ref:
                    up += 1
            btc_last = last.get(BTC_IID)
            points.append({"t": t + MINUTE, "btc": q(btc_last) if btc_last is not None else None,
                           "up_pct": q(up / len(others) * 100, 1) if others else None})
    out["btc-breadth"] = {"points": points}
    out["leaders"] = {"bars": f["leaders"] + f["laggards"]}
    top10 = [i for i, _ in sorted(((i, m["notional"]) for i, m in others.items()), key=lambda kv: -kv[1])[:10]]
    out["volatility"] = {"bars": [{"symbol": sym(i), "vol_ratio": q(vol_ratio.get(i))} for i in top10]}
    rets = [float(m["ret"]) * 100 for m in others.values()]
    if rets:
        lo, hi = math.floor(min(rets) * 2) / 2, math.ceil(max(rets) * 2) / 2
        n = max(1, min(30, int(round((hi - lo) / 0.5))))
        width = (hi - lo) / n if hi > lo else 0.5
        bins = [0] * n
        for r in rets:
            bins[min(n - 1, int((r - lo) / width))] += 1
        out["distribution"] = {"bins": [{"from": q(lo + k * width), "to": q(lo + (k + 1) * width), "count": c}
                                        for k, c in enumerate(bins)]}
    bucket = VOLUME_BUCKET[W]

    def bucket_sum(s: datetime) -> float:
        return float(sum((raw.total_notional.get(s + MINUTE * i, Decimal(0)) for i in range(bucket)), Decimal(0)))

    hist, t = [], start - MINUTE * bucket
    while t >= raw.end - LOOKBACK:
        hist.append(bucket_sum(t))
        t -= MINUTE * bucket
    norm = _median([h for h in hist if h > 0])
    out["volume"] = {"bucket_minutes": bucket,
                     "bars": [{"t": start + MINUTE * bucket * k, "vs_normal": q(bucket_sum(start + MINUTE * bucket * k) / norm) if norm else None}
                              for k in range(W // bucket)]}
    out["liquidity"] = {"pairs": [{"symbol": sym(i), "spread_start": q(rows[0][1], 1), "spread_end": q(rows[-1][1], 1),
                                   "depth_change_pct": q((float(rows[-1][2]) / float(rows[0][2]) - 1) * 100, 1) if rows[0][2] else None}
                                  for i, rows in raw.books.items() if rows]}
    out["bots"] = {"bots": f["bots"]}
    trend = [{k: p.get(k) for k in ("period_end", "btc_return_pct", "breadth_pct", "dispersion_pp")}
             for p in reversed(raw.previous[:2])]
    trend.append({"period_end": raw.end, "btc_return_pct": f["btc"]["return_pct"], "breadth_pct": f["breadth"]["up_pct"],
                  "dispersion_pp": f["dispersion"]["iqr_pp"]})
    out["trend3"] = {"points": trend}
    return out
