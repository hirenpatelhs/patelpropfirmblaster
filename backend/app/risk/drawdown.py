from dataclasses import dataclass
from decimal import Decimal

from app.core.enums import DrawdownType


@dataclass(frozen=True)
class DrawdownState:
    threshold: Decimal
    safety_threshold: Decimal
    remaining_buffer: Decimal
    breached: bool


@dataclass(frozen=True)
class HighWaterState:
    balance: Decimal
    equity: Decimal


def update_high_water(previous_balance: Decimal, previous_equity: Decimal, current_balance: Decimal, current_equity: Decimal) -> HighWaterState:
    """Monotonic high-water update using durable values restored from storage."""
    return HighWaterState(max(previous_balance, current_balance), max(previous_equity, current_equity))


def calculate_drawdown(
    mode: DrawdownType,
    initial_balance: Decimal,
    current_balance: Decimal,
    current_equity: Decimal,
    high_water_balance: Decimal,
    high_water_equity: Decimal,
    maximum_drawdown: Decimal,
    safety_buffer: Decimal,
    eod_high_water_balance: Decimal | None = None,
) -> DrawdownState:
    if mode == DrawdownType.STATIC:
        anchor = initial_balance
    elif mode == DrawdownType.TRAILING_BALANCE:
        anchor = high_water_balance
    elif mode == DrawdownType.TRAILING_EQUITY:
        anchor = high_water_equity
    else:
        anchor = eod_high_water_balance or high_water_balance
    threshold = anchor - maximum_drawdown
    safety_threshold = threshold + safety_buffer
    remaining = current_equity - safety_threshold
    return DrawdownState(threshold, safety_threshold, remaining, current_equity <= safety_threshold)
