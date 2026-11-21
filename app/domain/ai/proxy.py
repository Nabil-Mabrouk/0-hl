"""Client of the chassis LLM proxy (LiteLLM, OpenAI-compatible) — spec 03-ecrans/UI-16 « Contrat avec
le proxy du châssis ».

Keys never live here: the proxy holds the provider keys; 0-hl only has `LLM_PROXY_URL` and its own
`LLM_PROXY_TOKEN` (API container only). Errors are classified for the agent loop:
`budget_exceeded` (never retried), `tool_use_failed` (retried once), `key_model_access_denied`.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

CHAT_TIMEOUT_S = 60.0
INFO_TIMEOUT_S = 10.0


class ProxyNotConfigured(RuntimeError):
    pass


@dataclass
class ChatResult:
    ok: bool
    http_status: int
    latency_ms: int
    message: dict[str, Any] = field(default_factory=dict)   # assistant message (content, tool_calls)
    finish_reason: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: Decimal | None = None                           # x-litellm-response-cost
    error_code: str | None = None                             # BUDGET_EXCEEDED | TOOL_USE_FAILED | ...
    error_detail: str | None = None


def classify_error(status: int, body: Any) -> str:
    text = str(body).lower()
    if "budget_exceeded" in text or "budget has been exceeded" in text or "exceededbudget" in text:
        return "BUDGET_EXCEEDED"
    if "tool_use_failed" in text:
        return "TOOL_USE_FAILED"
    if status == 403 or "key_model_access_denied" in text:
        return "MODEL_NOT_ALLOWED"
    if status == 429:
        return "RATE_LIMITED"
    if status in (408, 504) or status == 0:
        return "TIMEOUT"
    return "PROXY_ERROR"


def _cost(headers: httpx.Headers) -> Decimal | None:
    raw = headers.get("x-litellm-response-cost")
    if raw is None:
        return None
    try:
        v = Decimal(raw)
    except InvalidOperation:
        return None
    return v if v >= 0 else None


class ProxyClient:
    def __init__(self, base_url: str | None = None, token: str | None = None,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = (base_url if base_url is not None else os.environ.get("LLM_PROXY_URL", "")).rstrip("/")
        self.token = token if token is not None else os.environ.get("LLM_PROXY_TOKEN", "")
        self.transport = transport

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.token)

    def _client(self, timeout: float) -> httpx.AsyncClient:
        if not self.configured:
            raise ProxyNotConfigured("LLM_PROXY_URL / LLM_PROXY_TOKEN absents")
        return httpx.AsyncClient(base_url=self.base_url, timeout=timeout, transport=self.transport,
                                 headers={"Authorization": f"Bearer {self.token}"})

    async def chat(self, *, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None,
                   temperature: float, max_tokens: int, timeout: float = CHAT_TIMEOUT_S) -> ChatResult:
        body: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature,
                                "max_tokens": max_tokens}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        t0 = time.monotonic()
        try:
            async with self._client(timeout) as c:
                r = await c.post("/v1/chat/completions", json=body)
        except httpx.TimeoutException as exc:
            return ChatResult(False, 0, int((time.monotonic() - t0) * 1000), error_code="TIMEOUT",
                              error_detail=str(exc)[:500])
        except httpx.HTTPError as exc:
            return ChatResult(False, 0, int((time.monotonic() - t0) * 1000), error_code="PROXY_UNREACHABLE",
                              error_detail=str(exc)[:500])
        latency = int((time.monotonic() - t0) * 1000)
        try:
            data = r.json()
        except ValueError:
            data = {"raw": r.text[:2000]}
        if r.status_code != 200:
            return ChatResult(False, r.status_code, latency, cost_usd=_cost(r.headers),
                              error_code=classify_error(r.status_code, data), error_detail=str(data)[:2000])
        choice = (data.get("choices") or [{}])[0]
        usage = data.get("usage") or {}
        return ChatResult(True, 200, latency, message=choice.get("message") or {},
                          finish_reason=choice.get("finish_reason"),
                          tokens_in=int(usage.get("prompt_tokens") or 0),
                          tokens_out=int(usage.get("completion_tokens") or 0), cost_usd=_cost(r.headers))

    async def models(self) -> list[str]:
        async with self._client(INFO_TIMEOUT_S) as c:
            r = await c.get("/v1/models")
            r.raise_for_status()
            return sorted(m["id"] for m in r.json().get("data", []) if m.get("id"))

    async def key_info(self) -> dict[str, Any]:
        async with self._client(INFO_TIMEOUT_S) as c:
            r = await c.get("/key/info")
            r.raise_for_status()
            info = r.json().get("info", r.json())

            def num(v: Any) -> str | None:
                return None if v is None else str(v)
            return {"spend": num(info.get("spend")), "max_budget": num(info.get("max_budget")),
                    "budget_duration": info.get("budget_duration"),
                    "budget_reset_at": info.get("budget_reset_at")}
