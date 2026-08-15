from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from app.brokers.base import BrokerAdapter, BrokerResult, OrderRequest
from app.core.enums import Confidence, TradingMode
from app.risk.position_sizing import calculate_position_size
from app.schemas.risk import PositionSizeResult, RiskContext
from app.schemas.signal import NormalizedSignal
from app.signal_engine.fingerprint import is_expired


@dataclass(frozen=True)
class PreExecutionResult:
    approved: bool
    reasons: list[str]
    sizing: PositionSizeResult | None = None


class ExecutionEngine:
    def preflight(
        self,
        signal: NormalizedSignal,
        broker: BrokerAdapter,
        risk: RiskContext,
        account_active: bool,
        automation_permitted: bool,
        require_stop_loss: bool = True,
        max_signal_age_seconds: int = 120,
    ) -> PreExecutionResult:
        reasons: list[str] = []
        if not account_active:
            reasons.append("Account is not active")
        if not automation_permitted:
            reasons.append("Automation is not permitted by the current rule profile")
        if signal.confidence != Confidence.HIGH:
            reasons.append("Signal confidence is not HIGH")
        if is_expired(signal, datetime.now(timezone.utc), max_signal_age_seconds):
            reasons.append("Signal is stale")
        if require_stop_loss and signal.stop_loss is None:
            reasons.append("A valid stop loss is required")
        if not broker.health_check():
            reasons.append("Broker is disconnected")
        price = broker.get_execution_price(signal.symbol, signal.direction)
        instrument = broker.get_symbol_info(signal.symbol)
        if price is None:
            reasons.append("Current market price is unavailable")
        if instrument is None:
            reasons.append("Instrument specification is unavailable")
        if reasons or price is None or instrument is None or signal.stop_loss is None:
            return PreExecutionResult(False, reasons)
        if signal.entry_min is not None and signal.entry_max is not None and not (signal.entry_min <= price <= signal.entry_max):
            return PreExecutionResult(False, ["Current price is outside the permitted entry range"])
        sizing = calculate_position_size(price, signal.stop_loss, instrument, risk)
        return PreExecutionResult(sizing.approved, sizing.reasons, sizing)

    def execute(
        self,
        signal: NormalizedSignal,
        broker: BrokerAdapter,
        preflight: PreExecutionResult,
        execution_id: str,
        trading_mode: TradingMode,
        max_slippage_points: int = 30,
    ) -> BrokerResult:
        if not preflight.approved or not preflight.sizing or signal.stop_loss is None:
            return BrokerResult(False, None, None, "PREFLIGHT_REJECTED", "; ".join(preflight.reasons), {})
        if trading_mode == TradingMode.DISABLED:
            return BrokerResult(False, None, None, "DISABLED", "Account execution is disabled", {})
        request = OrderRequest(
            idempotency_key=execution_id,
            symbol=signal.symbol,
            direction=signal.direction,
            order_type=signal.order_type,
            size=preflight.sizing.size,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profits[0] if signal.take_profits else None,
            entry_price=signal.entry_price,
            max_slippage_points=max_slippage_points,
        )
        if trading_mode == TradingMode.SHADOW and broker.__class__.__name__ != "MockBrokerAdapter":
            return BrokerResult(False, None, None, "SHADOW_GUARD", "Shadow mode cannot submit to a live adapter", {})
        return broker.place_order(request)
