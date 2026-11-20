"""SCANNER features for STRAT-01 Kraken (spec 06-agents/03 « SCANNER »).

All values are Decimal; a missing input yields UNKNOWN (never zero).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.trading.market.book import OrderBook
from app.domain.trading.models import TradeMinute

MINUTE = timedelta(minutes=1)
WINDOW_MINUTES = 5
BASELINE_WINDOWS = 12
MIN_BASELINE_WINDOWS = 6
EPSILON_USD = Decimal(1)
ZERO = Decimal(0)

OK = "OK"
UNKNOWN = "UNKNOWN"

# Features the SCANNER computes (06-agents/03): name -> (unit, description). A strategy
# condition on anything else would stay UNKNOWN forever (05-strategies/04 « Règles » 2).
FEATURE_CATALOGUE: dict[str, tuple[str, str]] = {
    "volume_spike": ("ratio", "notionnel de la fenêtre de 5 min / médiane des 12 fenêtres précédentes"),
    "buy_notional_share": ("fraction", "part acheteuse du notionnel de la fenêtre"),
    "price_change_5m": ("log_return", "log(dernier close / close précédant la fenêtre)"),
    "spread_bps": ("bps", "écart achat/vente du carnet en points de base"),
    "window_notional_usd": ("USD", "notionnel échangé sur la fenêtre de 5 min"),
    "window_trade_count": ("count", "nombre de trades sur la fenêtre de 5 min"),
}


@dataclass(frozen=True)
class Feature:
    name: str
    value: Decimal | None
    unit: str
    status: str = OK
    reason: str | None = None
    uses_backfill: bool = False

    def as_json(self) -> dict[str, Any]:
        return {"value": None if self.value is None else format(self.value, "f"),
                "unit": self.unit, "status": self.status, "reason": self.reason,
                "uses_backfill": self.uses_backfill}


def _unknown(name: str, unit: str, reason: str) -> Feature:
    return Feature(name=name, value=None, unit=unit, status=UNKNOWN, reason=reason)


@dataclass(frozen=True)
class MinuteData:
    notional: Decimal
    buy_notional: Decimal | None
    close: Decimal | None
    source: str
    trade_count: int = 0


async def load_minutes(
    db: AsyncSession, instrument_id: str, *, start: datetime, end: datetime
) -> dict[datetime, MinuteData]:
    """Minutes in [start, end); `live` wins over `backfill` for the same minute."""
    rows = await db.scalars(
        select(TradeMinute).where(
            TradeMinute.instrument_id == instrument_id,
            TradeMinute.minute_start >= start,
            TradeMinute.minute_start < end,
        )
    )
    out: dict[datetime, MinuteData] = {}
    for r in rows.all():
        if r.minute_start in out and out[r.minute_start].source == "live":
            continue
        out[r.minute_start] = MinuteData(r.notional_usd, r.buy_notional_usd, r.close, r.source,
                                         r.trade_count)
    return out


def window_features(
    minutes: dict[datetime, MinuteData], window_end: datetime
) -> dict[str, Feature]:
    window = [window_end - MINUTE * i for i in range(WINDOW_MINUTES, 0, -1)]
    current = [minutes.get(m) for m in window]
    complete_live = all(m is not None and m.source == "live" for m in current)

    # volume_spike
    if not complete_live:
        spike = _unknown("volume_spike", "ratio", "CURRENT_WINDOW_INCOMPLETE")
    else:
        cur_total = sum((m.notional for m in current), ZERO)  # type: ignore[union-attr]
        baselines: list[Decimal] = []
        used_backfill = False
        for k in range(1, BASELINE_WINDOWS + 1):
            start = window_end - MINUTE * WINDOW_MINUTES * (k + 1)
            mins = [minutes.get(start + MINUTE * i) for i in range(WINDOW_MINUTES)]
            if all(m is not None for m in mins):
                baselines.append(sum((m.notional for m in mins), ZERO))  # type: ignore[union-attr]
                used_backfill |= any(m.source == "backfill" for m in mins)  # type: ignore[union-attr]
        if len(baselines) < MIN_BASELINE_WINDOWS:
            spike = _unknown("volume_spike", "ratio", "INSUFFICIENT_HISTORY")
        else:
            base = max(Decimal(statistics.median(baselines)), EPSILON_USD)
            spike = Feature("volume_spike", cur_total / base, "ratio", uses_backfill=used_backfill)

    # buy_notional_share
    if not complete_live:
        share = _unknown("buy_notional_share", "fraction", "CURRENT_WINDOW_INCOMPLETE")
    else:
        total = sum((m.notional for m in current), ZERO)  # type: ignore[union-attr]
        buys = [m.buy_notional for m in current]  # type: ignore[union-attr]
        if total <= ZERO or any(b is None for b in buys):
            share = _unknown("buy_notional_share", "fraction", "NO_NOTIONAL")
        else:
            share = Feature("buy_notional_share", sum(buys, ZERO) / total, "fraction")  # type: ignore[arg-type]

    # price_change_5m
    last_close = next((m.close for m in reversed(current) if m and m.close is not None), None)
    prev_close = None
    for i in range(1, 61):
        m = minutes.get(window[0] - MINUTE * i)
        if m is not None and m.close is not None:
            prev_close = m.close
            break
    if not complete_live or last_close is None or prev_close is None or prev_close <= ZERO:
        change = _unknown("price_change_5m", "log_return", "MISSING_PRICE")
    else:
        change = Feature("price_change_5m", (last_close / prev_close).ln(), "log_return")
    # activity floors (ADR-014): current live window only
    if not complete_live:
        notional = _unknown("window_notional_usd", "USD", "CURRENT_WINDOW_INCOMPLETE")
        trades = _unknown("window_trade_count", "count", "CURRENT_WINDOW_INCOMPLETE")
    else:
        notional = Feature("window_notional_usd",
                           sum((m.notional for m in current), ZERO), "USD")  # type: ignore[union-attr]
        trades = Feature("window_trade_count",
                         Decimal(sum(m.trade_count for m in current)), "count")  # type: ignore[union-attr]
    return {f.name: f for f in (spike, share, change, notional, trades)}


def spread_feature(book: OrderBook | None, fresh: bool) -> Feature:
    if book is None or not fresh:
        return _unknown("spread_bps", "bps", "BOOK_NOT_LIVE")
    value = book.spread_bps()
    if value is None:
        return _unknown("spread_bps", "bps", "EMPTY_BOOK")
    return Feature("spread_bps", value, "bps")


def evaluate_conditions(entry: dict[str, Any], features: dict[str, Feature]) -> tuple[str, list[dict]]:
    """PASS / FAIL / UNKNOWN for the strategy entry block (UNKNOWN never counts as PASS)."""
    details: list[dict] = []
    results: list[str] = []
    for cond in entry["conditions"]:
        f = features.get(cond["feature"])
        threshold = Decimal(cond["threshold"])
        if f is None or f.status != OK or f.value is None:
            res = UNKNOWN
        else:
            v = f.value
            ok = {"gt": v > threshold, "gte": v >= threshold, "lt": v < threshold,
                  "lte": v <= threshold}[cond["op"]]
            res = "PASS" if ok else "FAIL"
        results.append(res)
        details.append({"feature": cond["feature"], "op": cond["op"],
                        "threshold": cond["threshold"], "result": res,
                        "value": None if f is None or f.value is None else format(f.value, "f")})
    if entry["operator"] == "AND":
        verdict = "FAIL" if "FAIL" in results else (UNKNOWN if UNKNOWN in results else "PASS")
    else:
        verdict = "PASS" if "PASS" in results else (UNKNOWN if UNKNOWN in results else "FAIL")
    return verdict, details
