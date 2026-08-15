from dataclasses import dataclass, field
from decimal import Decimal

from app.brokers.mock import MockBrokerAdapter
from app.core.enums import Decision, RiskClassification, TradingMode
from app.execution.engine import ExecutionEngine
from app.execution.symbols import broker_symbol
from app.positions.guard import ConcurrentLimits, evaluate_concurrent_positions
from app.positions.manager import LifecycleRecorder, ManagedPosition, PositionManagementService, managed_position_from_plan
from app.positions.tp_allocation import allocate_take_profits
from app.risk.classification import effective_risk_percent
from app.schemas.risk import RiskContext
from app.schemas.signal import NormalizedSignal


@dataclass
class ShadowAccount:
    account_id: str
    broker: MockBrokerAdapter
    equity: Decimal = Decimal("50000")
    active: bool = True
    automation_permitted: bool = True
    base_risk_percent: Decimal = Decimal("0.0035")
    maximum_risk: Decimal = Decimal("200")
    daily_buffer: Decimal = Decimal("2000")
    overall_buffer: Decimal = Decimal("2500")
    maximum_total_exposure: Decimal = Decimal("800")
    current_total_exposure: Decimal = Decimal("0")
    consecutive_losses: int = 0
    risk_multipliers: dict[str, object] = field(default_factory=dict)
    symbol_mappings: dict[str, str] = field(default_factory=dict)
    tp_preset: str = "PROTECT"
    tp_custom: list[Decimal] | None = None
    limits: ConcurrentLimits = field(default_factory=lambda: ConcurrentLimits(2, 1, 2, 2))
    blocking_reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ShadowDecision:
    account_id: str
    decision: Decision
    reasons: list[str]
    effective_risk_percent: Decimal
    calculated_size: Decimal = Decimal("0")
    position_id: str | None = None


@dataclass
class ShadowRun:
    decisions: list[ShadowDecision] = field(default_factory=list)
    positions: dict[str, ManagedPosition] = field(default_factory=dict)
    recorders: dict[str, LifecycleRecorder] = field(default_factory=dict)


class ShadowTradingEngine:
    """Pure in-process SHADOW pipeline used by workers and acceptance tests."""

    def __init__(self) -> None:
        self.execution = ExecutionEngine()

    def route(self, signal: NormalizedSignal, accounts: list[ShadowAccount]) -> ShadowRun:
        run = ShadowRun()
        for account in accounts:
            risk_percent = effective_risk_percent(account.base_risk_percent, signal.risk_classification, account.risk_multipliers)
            if account.blocking_reasons:
                run.decisions.append(ShadowDecision(account.account_id, Decision.REJECTED, list(account.blocking_reasons), risk_percent))
                continue
            if risk_percent <= 0:
                run.decisions.append(ShadowDecision(account.account_id, Decision.REJECTED, [f"{signal.risk_classification.value} policy disables execution"], risk_percent))
                continue
            mapped_symbol = broker_symbol(signal.symbol, account.symbol_mappings)
            mapped_signal = signal.model_copy(update={"symbol": mapped_symbol})
            concurrent = evaluate_concurrent_positions(account.broker.get_open_positions(), account.broker.get_pending_orders(), mapped_symbol, signal.direction, account.limits)
            if not concurrent.allowed:
                run.decisions.append(ShadowDecision(account.account_id, Decision.REJECTED, concurrent.reasons, risk_percent))
                continue
            context = RiskContext(
                equity=account.equity,
                risk_percent=risk_percent,
                maximum_risk=account.maximum_risk,
                remaining_daily_buffer=account.daily_buffer,
                remaining_overall_buffer=account.overall_buffer,
                current_total_exposure=account.current_total_exposure,
                maximum_total_exposure=account.maximum_total_exposure,
                consecutive_losses=account.consecutive_losses,
            )
            preflight = self.execution.preflight(mapped_signal, account.broker, context, account.active, account.automation_permitted)
            if not preflight.approved or preflight.sizing is None:
                run.decisions.append(ShadowDecision(account.account_id, Decision.REJECTED, preflight.reasons, risk_percent))
                continue
            execution_id = f"{signal.source_id}:{signal.telegram_message_id}:{account.account_id}"
            result = self.execution.execute(mapped_signal, account.broker, preflight, execution_id, TradingMode.SHADOW)
            if not result.accepted or not result.broker_order_id or result.fill_price is None:
                run.decisions.append(ShadowDecision(account.account_id, Decision.ERROR, [result.message], risk_percent, preflight.sizing.size))
                continue
            spec = account.broker.get_symbol_info(mapped_symbol)
            assert spec is not None
            plan = allocate_take_profits(preflight.sizing.size, signal.take_profits, spec.volume_min, spec.volume_step, account.tp_preset, account.tp_custom)
            managed = managed_position_from_plan(result.broker_order_id, execution_id, mapped_symbol, signal.direction, result.fill_price, signal.stop_loss, plan)
            managed.initial_risk = preflight.sizing.risk_amount
            recorder = LifecycleRecorder()
            recorder.record("POSITION_OPENED", managed, {"risk_classification": signal.risk_classification.value, "effective_risk_percent": str(risk_percent), "canonical_symbol": signal.symbol, "broker_symbol": mapped_symbol})
            run.positions[result.broker_order_id] = managed
            run.recorders[result.broker_order_id] = recorder
            run.decisions.append(ShadowDecision(account.account_id, Decision.APPROVED, [], risk_percent, preflight.sizing.size, result.broker_order_id))
        return run

    @staticmethod
    def manager(account: ShadowAccount, run: ShadowRun, position_id: str) -> PositionManagementService:
        return PositionManagementService(account.broker, run.recorders[position_id])
