"""Universe selection (ADR-003, spec 07-data/03 « Univers »). Pure and deterministic."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

VENUE = "kraken_spot"

# Stablecoins and fiat bases excluded from the universe (07-data/03, rule 3).
EXCLUDED_BASES: frozenset[str] = frozenset(
    {
        "USDT", "USDC", "DAI", "PYUSD", "RLUSD", "USD1", "USDQ", "USDR", "USDD", "USDG",
        "USDE", "TUSD", "FDUSD", "AUSD", "EURC", "EURCV", "EUROP",
        "EUR", "GBP", "CAD", "AUD", "CHF", "JPY", "AED",
    }
)


def instrument_id(symbol: str) -> str:
    return f"{VENUE}:{symbol}"


@dataclass(frozen=True)
class PairCandidate:
    symbol: str
    base: str
    quote: str
    status: str
    altname: str | None
    price_precision: int
    qty_precision: int
    price_increment: Decimal | None
    qty_min: Decimal | None
    cost_min: Decimal | None
    volume_24h_usd: Decimal | None

    @property
    def instrument_id(self) -> str:
        return instrument_id(self.symbol)


@dataclass(frozen=True)
class UniverseSelection:
    candidates: list[PairCandidate]  # every USD pair evaluated (persisted)
    selected: list[str]              # symbols, ranked


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value)) if not isinstance(value, Decimal) else value


def volume_24h_usd(ticker: dict[str, Any] | None) -> Decimal | None:
    """`v[1] × p[1]`: 24 h base volume × 24 h VWAP (USD-quoted pair)."""
    if not ticker:
        return None
    try:
        return Decimal(str(ticker["v"][1])) * Decimal(str(ticker["p"][1]))
    except (KeyError, IndexError, ArithmeticError):
        return None


def select_universe(
    asset_pairs: dict[str, dict[str, Any]],
    tickers: dict[str, dict[str, Any]],
    *,
    top_n: int,
    min_volume_24h_usd: Decimal | None,
    quote: str = "USD",
    excluded_bases: frozenset[str] = EXCLUDED_BASES,
) -> UniverseSelection:
    candidates: list[PairCandidate] = []
    for symbol, pair in asset_pairs.items():
        if pair.get("quote") != quote or pair.get("aclass_base", "currency") != "currency":
            continue
        candidates.append(
            PairCandidate(
                symbol=symbol,
                base=str(pair.get("base")),
                quote=quote,
                status=str(pair.get("status", "unknown")),
                altname=pair.get("altname"),
                price_precision=int(pair["pair_decimals"]),
                qty_precision=int(pair["lot_decimals"]),
                price_increment=_dec(pair.get("tick_size")),
                qty_min=_dec(pair.get("ordermin")),
                cost_min=_dec(pair.get("costmin")),
                volume_24h_usd=volume_24h_usd(tickers.get(symbol)),
            )
        )

    eligible = [
        c for c in candidates
        if c.status == "online"
        and c.base not in excluded_bases
        and c.volume_24h_usd is not None
        and (min_volume_24h_usd is None or c.volume_24h_usd >= min_volume_24h_usd)
    ]
    eligible.sort(key=lambda c: (-c.volume_24h_usd, c.symbol))  # type: ignore[operator]
    candidates.sort(key=lambda c: c.symbol)
    return UniverseSelection(candidates=candidates, selected=[c.symbol for c in eligible[:top_n]])
