"""Kraken public REST client (spec 07-data/03).

`assetVersion=1` everywhere (keys `BTC/USD`, same as WS v2). At most one request
per `min_interval` seconds (public limits undocumented, [OPEN]); a rate-limit
error is retried with backoff, any other error raises PROVIDER_UNAVAILABLE.
"""

from __future__ import annotations

import asyncio
import json
import time
from decimal import Decimal
from typing import Any

import httpx

from app.domain.trading.errors import ProviderUnavailable

BASE_URL = "https://api.kraken.com/0/public"


class KrakenRestClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        base_url: str = BASE_URL,
        min_interval: float = 1.0,
        max_retries: int = 3,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._min_interval = min_interval
        self._max_retries = max_retries
        self._lock = asyncio.Lock()
        self._last_call = 0.0

    async def _throttle(self) -> None:
        wait = self._min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_call = time.monotonic()

    async def _get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        params = {"assetVersion": 1, **params}
        attempt = 0
        while True:
            async with self._lock:
                await self._throttle()
                try:
                    resp = await self._client.get(f"{self._base_url}/{endpoint}", params=params)
                    resp.raise_for_status()
                    body = json.loads(resp.text, parse_float=Decimal)
                except (httpx.HTTPError, ValueError) as exc:
                    raise ProviderUnavailable(f"kraken {endpoint}: {exc}") from exc
            errors = body.get("error") or []
            if not errors:
                return body.get("result", {})
            if any("Rate limit" in str(e) for e in errors) and attempt < self._max_retries:
                attempt += 1
                await asyncio.sleep(2**attempt)
                continue
            raise ProviderUnavailable(f"kraken {endpoint}: {errors}")

    async def asset_pairs(self) -> dict[str, dict[str, Any]]:
        return await self._get("AssetPairs", {})

    async def ticker(self) -> dict[str, dict[str, Any]]:
        return await self._get("Ticker", {})

    async def ohlc(self, symbol: str, *, interval: int = 1) -> list[list[Any]]:
        result = await self._get("OHLC", {"pair": symbol, "interval": interval})
        for key, value in result.items():
            if key != "last":
                return value
        return []
