"""Domain enumerations (spec 04-metier/01, 10-backend/03)."""

from __future__ import annotations

from enum import Enum


class TradingRole(str, Enum):
    OBSERVER = "OBSERVER"
    ANALYST = "ANALYST"
    OPERATOR = "OPERATOR"
    RISK_OFFICER = "RISK_OFFICER"
    ADMIN = "ADMIN"


class Environment(str, Enum):
    DEMO = "DEMO"
    PAPER = "PAPER"
    LIVE = "LIVE"


class BotStatus(str, Enum):
    CREATED = "CREATED"
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"


class SessionStatus(str, Enum):
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


class StrategyVersionStatus(str, Enum):
    DRAFT = "DRAFT"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    PUBLISHED = "PUBLISHED"
    DEPRECATED = "DEPRECATED"


class PolicyVersionStatus(str, Enum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    DEPRECATED = "DEPRECATED"


class CommandType(str, Enum):
    START = "START"
    PAUSE_ENTRIES = "PAUSE_ENTRIES"
    RESUME_ENTRIES = "RESUME_ENTRIES"
    STOP = "STOP"
    EMERGENCY_STOP = "EMERGENCY_STOP"


class CommandStatus(str, Enum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"


class CandidateStatus(str, Enum):
    DISCOVERED = "DISCOVERED"
    ENRICHING = "ENRICHING"
    REJECTED = "REJECTED"
    APPROVED = "APPROVED"
    EXPIRED = "EXPIRED"


class AssessmentVerdict(str, Enum):
    PASS = "PASS"
    REJECT = "REJECT"


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class IntentPurpose(str, Enum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"


class OrderStatus(str, Enum):
    CREATED = "CREATED"
    VERIFIED = "VERIFIED"
    SUBMITTING = "SUBMITTING"
    ACKED = "ACKED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"


class PositionStatus(str, Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    REDUCING = "REDUCING"
    CLOSED = "CLOSED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


class LedgerKind(str, Enum):
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    RESERVE = "RESERVE"
    RELEASE = "RELEASE"
    BUY_FILL = "BUY_FILL"
    SELL_FILL = "SELL_FILL"
    COMPENSATION = "COMPENSATION"


class CashMovementKind(str, Enum):
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"


class SnapshotQuality(str, Enum):
    OK = "OK"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"
