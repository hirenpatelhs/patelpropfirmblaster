from dataclasses import dataclass, field
from decimal import Decimal

from app.core.enums import Direction


@dataclass(frozen=True)
class RuleContext:
    symbol: str
    direction: Direction
    automation_requested: bool
    proposed_risk: Decimal
    remaining_daily_buffer: Decimal
    remaining_overall_buffer: Decimal
    open_positions: int
    maximum_positions: int
    allowed_symbols: set[str] = field(default_factory=set)
    restricted_symbols: set[str] = field(default_factory=set)
    ea_allowed: bool = False
    signal_copying_allowed: bool = False
    third_party_signal_allowed: bool = False
    news_blackout_active: bool = False
    news_trading_allowed: bool = False
    opposite_position_open: bool = False
    hedging_allowed: bool = False


@dataclass(frozen=True)
class RuleDecision:
    approved: bool
    reasons: list[str]


def evaluate_rules(ctx: RuleContext) -> RuleDecision:
    reasons: list[str] = []
    if ctx.automation_requested and not (ctx.ea_allowed and ctx.signal_copying_allowed and ctx.third_party_signal_allowed):
        reasons.append("Rule profile does not permit the configured third-party signal automation")
    if ctx.allowed_symbols and ctx.symbol not in ctx.allowed_symbols:
        reasons.append(f"{ctx.symbol} is not in the allowed-symbol list")
    if ctx.symbol in ctx.restricted_symbols:
        reasons.append(f"{ctx.symbol} is restricted")
    if ctx.proposed_risk > ctx.remaining_daily_buffer:
        reasons.append("Remaining daily-loss buffer insufficient")
    if ctx.proposed_risk > ctx.remaining_overall_buffer:
        reasons.append("Remaining overall drawdown buffer insufficient")
    if ctx.open_positions >= ctx.maximum_positions:
        reasons.append("Maximum open positions reached")
    if ctx.news_blackout_active and not ctx.news_trading_allowed:
        reasons.append("High-impact news blackout active")
    if ctx.opposite_position_open and not ctx.hedging_allowed:
        reasons.append("Hedging is prohibited by the assigned rule profile")
    return RuleDecision(not reasons, reasons)
