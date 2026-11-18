"""Shared API plumbing (spec 10-backend/04 « Authentification, rôles, erreurs »).

- `DomainRoute`: RFC 9457 problem responses for domain errors, HTTP errors and
  validation errors; `X-Correlation-ID` echoed or generated.
- `ok()`: JSON with Decimal as strings (FastAPI would emit floats otherwise).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute

from app.domain.trading.enums import TradingRole
from app.domain.trading.errors import DomainError
from app.domain.trading.models import DomainEvent
from app.domain.trading.types import to_jsonable

PROBLEM_JSON = "application/problem+json"
ALL_ROLES = tuple(TradingRole)
_STATUS_CODES = {401: "UNAUTHENTICATED", 403: "PERMISSION_DENIED", 404: "NOT_FOUND",
                 409: "STATE_CONFLICT", 422: "VALIDATION_FAILED"}


def ok(data: Any, status: int = 200, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(content=to_jsonable(data), status_code=status, headers=headers)


def problem(status: int, code: str, detail: str, correlation_id: str,
            extra: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> JSONResponse:
    body = {"type": f"https://0-hl.com/problems/{code.lower()}", "title": code, "status": status,
            "code": code, "detail": detail, "correlation_id": correlation_id}
    if extra:
        body["extra"] = to_jsonable(extra)
    return JSONResponse(content=body, status_code=status, media_type=PROBLEM_JSON, headers=headers)


def _correlation(request: Request) -> str:
    raw = request.headers.get("X-Correlation-ID")
    try:
        return str(uuid.UUID(raw)) if raw else str(uuid.uuid4())
    except ValueError:
        return str(uuid.uuid4())


class DomainRoute(APIRoute):
    def get_route_handler(self) -> Callable:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            corr = _correlation(request)
            request.state.correlation_id = corr
            try:
                response = await original(request)
            except DomainError as exc:
                response = problem(exc.http_status, exc.code, exc.message, corr, exc.details)
            except RequestValidationError as exc:
                response = problem(422, "VALIDATION_FAILED", "invalid request", corr,
                                   {"errors": [{"loc": list(map(str, e.get("loc", ()))),
                                                "msg": str(e.get("msg"))} for e in exc.errors()]})
            except HTTPException as exc:
                detail = exc.detail
                if isinstance(detail, dict):
                    code = str(detail.get("code") or _STATUS_CODES.get(exc.status_code, "ERROR"))
                    message = str(detail.get("message") or code)
                else:
                    code = _STATUS_CODES.get(exc.status_code, "ERROR")
                    message = str(detail)
                response = problem(exc.status_code, code, message, corr,
                                   headers=dict(exc.headers or {}))
            response.headers["X-Correlation-ID"] = corr
            return response

        return handler


def event_json(e: DomainEvent) -> dict[str, Any]:
    return {
        "position": e.position, "event_id": e.event_id, "event_type": e.event_type,
        "aggregate_type": e.aggregate_type, "aggregate_id": e.aggregate_id,
        "aggregate_sequence": e.aggregate_sequence, "correlation_id": e.correlation_id,
        "causation_id": e.causation_id, "bot_id": e.bot_id, "occurred_at": e.occurred_at,
        "source": e.source, "payload": e.payload,
    }


def parse_uuid(value: str, what: str = "id") -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND",
                                                     "message": f"unknown {what}"}) from exc
