from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN

from app.brokers.base import BrokerAdapter
from app.core.enums import Decision, TradingMode
from app.execution.engine import ExecutionEngine, PreExecutionResult
from app.execution.shadow import ShadowDecision, ShadowRun
from app.execution.symbols import broker_symbol
from app.positions.guard import ConcurrentLimits, evaluate_concurrent_positions
from app.positions.manager import LifecycleRecorder, managed_position_from_plan
from app.positions.tp_allocation import allocate_take_profits
from app.risk.classification import effective_risk_percent
from app.schemas.risk import RiskContext
from app.schemas.signal import NormalizedSignal


@dataclass
class DemoAccount:
    account_id: str
    broker: BrokerAdapter
    equity: Decimal
    automation_permitted: bool
    base_risk_percent: Decimal
    maximum_risk: Decimal
    daily_buffer: Decimal
    overall_buffer: Decimal
    maximum_total_exposure: Decimal
    current_total_exposure: Decimal
    max_volume: Decimal = Decimal("0.01")
    consecutive_losses: int = 0
    risk_multipliers: dict[str, object] = field(default_factory=dict)
    symbol_mappings: dict[str, str] = field(default_factory=dict)
    tp_preset: str = "PROTECT"
    tp_custom: list[Decimal] | None = None
    limits: ConcurrentLimits = field(default_factory=lambda: ConcurrentLimits(1, 1, 1, 1))
    blocking_reasons: list[str] = field(default_factory=list)


class DemoTradingEngine:
    """Real MT5 execution constrained to a verified demo login and volume cap."""

    def __init__(self) -> None:
        self.execution = ExecutionEngine()

    def route(self, signal: NormalizedSignal, accounts: list[DemoAccount]) -> ShadowRun:
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
            concurrent = evaluate_concurrent_positions(
                account.broker.get_open_positions(),
                account.broker.get_pending_orders(),
                mapped_symbol,
                signal.direction,
                account.limits,
            )
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
            preflight = self.execution.preflight(mapped_signal, account.broker, context, True, account.automation_permitted)
            if not preflight.approved or preflight.sizing is None:
                run.decisions.append(ShadowDecision(account.account_id, Decision.REJECTED, preflight.reasons, risk_percent))
                continue
            spec = account.broker.get_symbol_info(mapped_symbol)
            if spec is None:
                run.decisions.append(ShadowDecision(account.account_id, Decision.REJECTED, ["Instrument specification is unavailable"], risk_percent))
                continue
            cap_steps = (account.max_volume / spec.volume_step).to_integral_value(rounding=ROUND_DOWN)
            volume_cap = min(cap_steps * spec.volume_step, spec.volume_max)
            if volume_cap < spec.volume_min:
                run.decisions.append(ShadowDecision(account.account_id, Decision.REJECTED, ["DEMO volume cap is below broker minimum"], risk_percent))
                continue
            if preflight.sizing.size > volume_cap:
                capped_risk = (preflight.sizing.loss_per_lot * volume_cap).quantize(Decimal("0.01"))
                preflight = PreExecutionResult(
                    True,
                    [],
                    preflight.sizing.model_copy(update={"size": volume_cap, "risk_amount": capped_risk}),
                )
            execution_id = f"{signal.source_id}:{signal.telegram_message_id}:{account.account_id}"
            result = self.execution.execute(mapped_signal, account.broker, preflight, execution_id, TradingMode.DEMO)
            if not result.accepted or not result.broker_order_id or result.fill_price is None:
                run.decisions.append(ShadowDecision(account.account_id, Decision.ERROR, [result.message], risk_percent, preflight.sizing.size))
                continue
            plan = allocate_take_profits(preflight.sizing.size, signal.take_profits, spec.volume_min, spec.volume_step, account.tp_preset, account.tp_custom)
            managed = managed_position_from_plan(result.broker_order_id, execution_id, mapped_symbol, signal.direction, result.fill_price, signal.stop_loss, plan)
            managed.initial_risk = preflight.sizing.risk_amount
            recorder = LifecycleRecorder()
            recorder.record("POSITION_OPENED", managed, {"mode": "DEMO", "effective_risk_percent": str(risk_percent), "volume_cap": str(volume_cap)})
            run.positions[result.broker_order_id] = managed
            run.recorders[result.broker_order_id] = recorder
            run.decisions.append(ShadowDecision(account.account_id, Decision.APPROVED, [], risk_percent, preflight.sizing.size, result.broker_order_id))
        return run
