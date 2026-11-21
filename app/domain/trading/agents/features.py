"""SCANNER features for STRAT-01 and STRAT-02 Kraken (spec 06-agents/03 « SCANNER », ADR-023).

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
HISTORY_MINUTES = 250        # minutes the SCANNER reads before a window end (trend_4h, ADR-023)
RANGE_MINUTES = 60
MIN_RANGE_MINUTES = 54
TREND_MINUTES = 240
TREND_TOLERANCE_MINUTES = 10
HIGH_3H_MINUTES, MIN_HIGH_3H_MINUTES = 180, 162
MEAN_4H_MINUTES, MIN_MEAN_4H_MINUTES = 240, 216
DAY_MINUTES = 1440
MIN_RANKABLE_PAIRS = 20
BTC_IID = "kraken_spot:BTC/USD"
# features that need the narrow 24 h read, and features computed once per window for the
# whole universe (06-agents/03, ADR-024)
DAY_FEATURES = frozenset({"trend_24h", "rs_24h", "rs_rank_24h", "btc_trend_24h"})
MARKET_FEATURES = frozenset({"btc_change_3h", "btc_trend_24h", "rs_24h", "rs_rank_24h"})

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
    "breakout_1h": ("log_return", "log(dernier close / plus haut des 60 minutes précédentes)"),
    "range_1h": ("log_return", "log(plus haut / plus bas des 60 minutes précédant la dernière minute)"),
    "trend_4h": ("log_return", "log(dernier close / close d'il y a 240 minutes)"),
    "drop_from_high_3h": ("log_return", "log(dernier close / plus haut des 180 minutes précédentes)"),
    "gap_to_mean_4h": ("log_return", "log(dernier close / moyenne des closes des 240 minutes précédentes)"),
    "trend_24h": ("log_return", "log(dernier close / close d'il y a 24 h)"),
    "btc_change_3h": ("log_return", "variation du BTC/USD sur 3 h (feature de marché)"),
    "btc_trend_24h": ("log_return", "variation du BTC/USD sur 24 h (feature de marché)"),
    "rs_24h": ("log_return", "trend_24h de la paire − trend_24h du BTC/USD"),
    "rs_rank_24h": ("rank", "rang de trend_24h parmi les paires de l'univers (1 = la plus forte)"),
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
    high: Decimal | None = None
    low: Decimal | None = None

    @classmethod
    def of(cls, r: Any) -> "MinuteData":
        """From a `TradeMinute` row (or any object with the same fields)."""
        return cls(r.notional_usd, r.buy_notional_usd, r.close, r.source, r.trade_count,
                   r.high, r.low)


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
        out[r.minute_start] = MinuteData.of(r)
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


def breakout_features(
    minutes: dict[datetime, MinuteData], window_end: datetime
) -> dict[str, Feature]:
    """STRAT-02 (05-strategies/02, 06-agents/03): last minute t−1 must be live with a close; the
    range is read on [t−61 min, t−1 min) (≥ 54 of 60 minutes); trend reference close at t−240 min,
    or the nearest of the 10 minutes before it. Only minutes before `window_end` are read."""
    names = (("breakout_1h", "log_return"), ("range_1h", "log_return"))
    last = minutes.get(window_end - MINUTE)
    if last is None or last.source != "live" or last.close is None or last.close <= ZERO:
        out = {n: _unknown(n, u, "LAST_MINUTE_NOT_LIVE") for n, u in names}
        out["trend_4h"] = _unknown("trend_4h", "log_return", "LAST_MINUTE_NOT_LIVE")
        return out
    highs: list[Decimal] = []
    lows: list[Decimal] = []
    for k in range(2, RANGE_MINUTES + 2):
        m = minutes.get(window_end - MINUTE * k)
        if m is not None and m.high is not None and m.low is not None and m.low > ZERO:
            highs.append(m.high)
            lows.append(m.low)
    out: dict[str, Feature] = {}
    if len(highs) < MIN_RANGE_MINUTES:
        out.update({n: _unknown(n, u, "INSUFFICIENT_HISTORY") for n, u in names})
    else:
        hi, lo = max(highs), min(lows)
        out["breakout_1h"] = Feature("breakout_1h", (last.close / hi).ln(), "log_return")
        out["range_1h"] = Feature("range_1h", (hi / lo).ln(), "log_return")
    ref = _ref_close(minutes, window_end, TREND_MINUTES)
    out["trend_4h"] = (_unknown("trend_4h", "log_return", "INSUFFICIENT_HISTORY") if ref is None
                       else Feature("trend_4h", (last.close / ref).ln(), "log_return"))
    return out


def _ref_close(minutes: dict[datetime, MinuteData], window_end: datetime, lag: int) -> Decimal | None:
    """Close of minute `window_end − lag`, or the nearest of the 10 minutes before it."""
    for k in range(lag, lag + TREND_TOLERANCE_MINUTES + 1):
        m = minutes.get(window_end - MINUTE * k)
        if m is not None and m.close is not None and m.close > ZERO:
            return m.close
    return None


def _last_live_close(minutes: dict[datetime, MinuteData], window_end: datetime) -> Decimal | None:
    last = minutes.get(window_end - MINUTE)
    if last is None or last.source != "live" or last.close is None or last.close <= ZERO:
        return None
    return last.close


def change_feature(name: str, minutes: dict[datetime, MinuteData], window_end: datetime,
                   lag: int) -> Feature:
    """ln(last live close / close `lag` minutes before the window end)."""
    last = _last_live_close(minutes, window_end)
    if last is None:
        return _unknown(name, "log_return", "LAST_MINUTE_NOT_LIVE")
    ref = _ref_close(minutes, window_end, lag)
    if ref is None:
        return _unknown(name, "log_return", "INSUFFICIENT_HISTORY")
    return Feature(name, (last / ref).ln(), "log_return")


def gap_to_mean_feature(minutes: dict[datetime, MinuteData], window_end: datetime) -> Feature:
    """gap_to_mean_4h: ln(last live close / mean close of [t−241 min, t−1 min)), ≥ 216 of 240."""
    last = _last_live_close(minutes, window_end)
    if last is None:
        return _unknown("gap_to_mean_4h", "log_return", "LAST_MINUTE_NOT_LIVE")
    closes = [m.close for k in range(2, MEAN_4H_MINUTES + 2)
              if (m := minutes.get(window_end - MINUTE * k)) is not None
              and m.close is not None and m.close > ZERO]
    if len(closes) < MIN_MEAN_4H_MINUTES:
        return _unknown("gap_to_mean_4h", "log_return", "INSUFFICIENT_HISTORY")
    return Feature("gap_to_mean_4h", (last / (sum(closes, ZERO) / len(closes))).ln(), "log_return")


def reversion_features(
    minutes: dict[datetime, MinuteData], window_end: datetime
) -> dict[str, Feature]:
    """STRAT-06 (05-strategies/02, 06-agents/03): drop from the 3 h high, gap to the 4 h mean
    (minutes before the last one), 24 h trend (needs the narrow 24 h read)."""
    last = _last_live_close(minutes, window_end)
    names = ("drop_from_high_3h", "gap_to_mean_4h")
    out: dict[str, Feature] = {}
    if last is None:
        out.update({n: _unknown(n, "log_return", "LAST_MINUTE_NOT_LIVE") for n in names})
    else:
        highs = [m.high for k in range(2, HIGH_3H_MINUTES + 2)
                 if (m := minutes.get(window_end - MINUTE * k)) is not None
                 and m.high is not None and m.high > ZERO]
        out["drop_from_high_3h"] = (
            _unknown(names[0], "log_return", "INSUFFICIENT_HISTORY") if len(highs) < MIN_HIGH_3H_MINUTES
            else Feature(names[0], (last / max(highs)).ln(), "log_return"))
        out["gap_to_mean_4h"] = gap_to_mean_feature(minutes, window_end)
    out["trend_24h"] = change_feature("trend_24h", minutes, window_end, DAY_MINUTES)
    return out


def scanner_features(minutes: dict[datetime, MinuteData], window_end: datetime) -> dict[str, Feature]:
    """Every per-pair catalogue feature (not the spread, which needs the live book, nor the
    market features, which need the whole universe: `market_view`)."""
    return {**window_features(minutes, window_end), **breakout_features(minutes, window_end),
            **reversion_features(minutes, window_end)}


@dataclass(frozen=True)
class MarketView:
    """Market features of one window (06-agents/03, ADR-024), identical for every bot."""
    common: dict[str, Feature]
    per_pair: dict[str, dict[str, Feature]]

    def for_pair(self, iid: str) -> dict[str, Feature]:
        pair = self.per_pair.get(iid) or {
            "rs_24h": _unknown("rs_24h", "log_return", "NOT_IN_UNIVERSE"),
            "rs_rank_24h": _unknown("rs_rank_24h", "rank", "NOT_IN_UNIVERSE")}
        return {**self.common, **pair}


def market_view(trends: dict[str, Feature], btc_change_3h: Feature) -> MarketView:
    """`trends`: trend_24h of every universe pair. Rank 1 = strongest; ties share the best rank."""
    btc = trends.get(BTC_IID) or _unknown("trend_24h", "log_return", "BTC_NOT_IN_UNIVERSE")
    btc_trend = (Feature("btc_trend_24h", btc.value, "log_return") if btc.status == OK
                 else _unknown("btc_trend_24h", "log_return", btc.reason or "BTC_UNKNOWN"))
    btc_ch = (Feature("btc_change_3h", btc_change_3h.value, "log_return") if btc_change_3h.status == OK
              else _unknown("btc_change_3h", "log_return", btc_change_3h.reason or "BTC_UNKNOWN"))
    known = {iid: f.value for iid, f in trends.items() if f.status == OK and f.value is not None}
    enough = len(known) >= MIN_RANKABLE_PAIRS
    per_pair: dict[str, dict[str, Feature]] = {}
    for iid, f in trends.items():
        v = known.get(iid)
        if v is None:
            rank = _unknown("rs_rank_24h", "rank", f.reason or "UNKNOWN")
            rs = _unknown("rs_24h", "log_return", f.reason or "UNKNOWN")
        else:
            rank = (Feature("rs_rank_24h", Decimal(1 + sum(1 for w in known.values() if w > v)), "rank")
                    if enough else _unknown("rs_rank_24h", "rank", "TOO_FEW_PAIRS"))
            rs = (Feature("rs_24h", v - btc_trend.value, "log_return")  # type: ignore[operator]
                  if btc_trend.status == OK else _unknown("rs_24h", "log_return", btc_trend.reason or "BTC_UNKNOWN"))
        per_pair[iid] = {"rs_24h": rs, "rs_rank_24h": rank}
    return MarketView({"btc_change_3h": btc_ch, "btc_trend_24h": btc_trend}, per_pair)


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
