from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True)
class DailyGuardInput:
    realized_pnl: Decimal
    floating_pnl: Decimal
    trades: int
    consecutive_losses: int
    max_loss: Decimal
    max_profit: Decimal | None
    max_trades: int
    max_consecutive_losses: int
    manual_lock: bool = False


@dataclass(frozen=True)
class DailyGuardDecision:
    allowed: bool
    reasons: list[str] = field(default_factory=list)
    risk_multiplier: Decimal = Decimal("1")


def evaluate_daily_guard(data: DailyGuardInput) -> DailyGuardDecision:
    pnl = data.realized_pnl + data.floating_pnl
    reasons: list[str] = []
    if data.manual_lock:
        reasons.append("MANUAL_LOCK")
    if pnl <= -abs(data.max_loss):
        reasons.append("DAILY_LOSS_LOCK")
    if data.max_profit is not None and pnl >= data.max_profit:
        reasons.append("DAILY_PROFIT_LOCK")
    if data.trades >= data.max_trades:
        reasons.append("MAX_TRADES_REACHED")
    if data.consecutive_losses >= data.max_consecutive_losses:
        reasons.append("MAX_CONSECUTIVE_LOSSES")
    multiplier = Decimal("0.5") if pnl < -(abs(data.max_loss) * Decimal("0.5")) else Decimal("1")
    return DailyGuardDecision(not reasons, reasons, multiplier)
