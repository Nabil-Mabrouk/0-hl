"""Domain errors with stable codes (spec 10-backend/02 « Taxonomie d'erreurs »)."""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    code: str = "DOMAIN_ERROR"
    http_status: int = 400

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ValidationFailed(DomainError):
    code = "VALIDATION_FAILED"
    http_status = 422


class VersionConflict(DomainError):
    code = "VERSION_CONFLICT"
    http_status = 409


class StateConflict(DomainError):
    code = "STATE_CONFLICT"
    http_status = 409


class VersionImmutable(DomainError):
    """A non-DRAFT strategy/policy version cannot be modified (INV-06)."""

    code = "VERSION_IMMUTABLE"
    http_status = 409


class StrategyNotPublished(DomainError):
    code = "STRATEGY_NOT_PUBLISHED"
    http_status = 409


class LiveNotApproved(DomainError):
    code = "LIVE_NOT_APPROVED"
    http_status = 403


class LedgerImbalance(DomainError):
    code = "LEDGER_IMBALANCE"
    http_status = 500


class PermissionDenied(DomainError):
    code = "PERMISSION_DENIED"
    http_status = 403


class ProviderUnavailable(DomainError):
    code = "PROVIDER_UNAVAILABLE"
    http_status = 503


class NotFound(DomainError):
    code = "NOT_FOUND"
    http_status = 404


class PreconditionFailed(DomainError):
    """`If-Match` does not match the current resource (M2.3, 05-strategies/04)."""

    code = "PRECONDITION_FAILED"
    http_status = 412


class ReviewDelayPending(DomainError):
    """Loosened policy: confirmation before the 10-minute review delay (ADR-006)."""

    code = "REVIEW_DELAY_PENDING"
    http_status = 409


class BotNotRebindable(DomainError):
    code = "BOT_NOT_REBINDABLE"
    http_status = 409
