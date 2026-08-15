from enum import StrEnum


class AccountStage(StrEnum):
    EVALUATION = "EVALUATION"
    FUNDED = "FUNDED"
    LIVE_PERSONAL = "LIVE_PERSONAL"
    SIMULATION = "SIMULATION"


class AccountStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    LOCKED = "LOCKED"
    PASSED = "PASSED"
    FAILED = "FAILED"
    ARCHIVED = "ARCHIVED"


class TradingMode(StrEnum):
    LIVE = "LIVE"
    SHADOW = "SHADOW"
    DISABLED = "DISABLED"


class RiskMode(StrEnum):
    EVALUATION = "EVALUATION"
    FUNDED = "FUNDED"
    CUSTOM = "CUSTOM"


class Direction(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"


class SignalStatus(StrEnum):
    RECEIVED = "RECEIVED"
    PARSED = "PARSED"
    VALIDATED = "VALIDATED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    QUEUED = "QUEUED"
    EXECUTED = "EXECUTED"
    PARTIAL = "PARTIAL"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    WAITING_FOR_SL = "WAITING_FOR_SL"
    ERROR = "ERROR"


class Confidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class Decision(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"


class DrawdownType(StrEnum):
    STATIC = "STATIC"
    TRAILING_BALANCE = "TRAILING_BALANCE"
    TRAILING_EQUITY = "TRAILING_EQUITY"
    END_OF_DAY_TRAILING = "END_OF_DAY_TRAILING"


class WarningLevel(StrEnum):
    SAFE = "SAFE"
    WARNING = "WARNING"
    DANGER = "DANGER"
    LOCKED = "LOCKED"


class RiskClassification(StrEnum):
    NORMAL = "NORMAL"
    MEDIUM_RISK = "MEDIUM_RISK"
    HIGH_RISK = "HIGH_RISK"
    VERY_HIGH_RISK = "VERY_HIGH_RISK"


class TargetStatus(StrEnum):
    WAITING = "WAITING"
    TRIGGERED = "TRIGGERED"
    EXECUTED = "EXECUTED"
    SKIPPED = "SKIPPED"
    MERGED = "MERGED"
    FAILED = "FAILED"


class UpdateStatus(StrEnum):
    RECEIVED = "RECEIVED"
    RESOLVED = "RESOLVED"
    EXECUTED = "EXECUTED"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"
    FAILED = "FAILED"
