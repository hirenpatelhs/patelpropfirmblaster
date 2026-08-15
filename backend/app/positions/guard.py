from dataclasses import dataclass
from typing import Any

from app.core.enums import Direction


@dataclass(frozen=True)
class ConcurrentLimits:
    max_positions: int
    max_per_symbol: int
    max_per_direction: int
    max_pending_orders: int
    on_limit: str = "REJECT"


@dataclass(frozen=True)
class ConcurrentDecision:
    allowed: bool
    action: str
    reasons: list[str]


def evaluate_concurrent_positions(
    positions: list[dict[str, Any]],
    pending_orders: list[dict[str, Any]],
    symbol: str,
    direction: Direction,
    limits: ConcurrentLimits,
) -> ConcurrentDecision:
    reasons: list[str] = []
    # One broker/PPB position counts once regardless of how many virtual targets it owns.
    open_positions = [position for position in positions if str(position.get("status", "OPEN")).upper() in {"OPEN", "ACTIVE"}]
    if len(open_positions) >= limits.max_positions:
        reasons.append("MAX_POSITIONS reached")
    same_symbol = [position for position in open_positions if position.get("symbol") == symbol]
    if len(same_symbol) >= limits.max_per_symbol:
        reasons.append(f"MAX_PER_SYMBOL reached for {symbol}")
    same_direction = [position for position in open_positions if str(position.get("direction")) == direction.value]
    if len(same_direction) >= limits.max_per_direction:
        reasons.append(f"MAX_PER_DIRECTION reached for {direction.value}")
    if len(pending_orders) >= limits.max_pending_orders:
        reasons.append("MAX_PENDING_ORDERS reached")
    return ConcurrentDecision(not reasons, limits.on_limit.upper() if reasons else "ALLOW", reasons)
