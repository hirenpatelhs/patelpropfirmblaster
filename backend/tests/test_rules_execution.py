from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from app.brokers.mock import MockBrokerAdapter
from app.core.enums import Confidence, Direction, TradingMode
from app.execution.engine import ExecutionEngine
from app.monitoring.recovery import reconcile_positions
from app.prop_rules.engine import RuleContext, evaluate_rules
from app.schemas.risk import RiskContext
from app.schemas.signal import NormalizedSignal


def signal() -> NormalizedSignal:
    return NormalizedSignal(source_id=uuid4(), telegram_message_id=1, symbol="XAUUSD", direction=Direction.BUY, entry_min=Decimal("3342"), entry_max=Decimal("3345"), stop_loss=Decimal("3334"), take_profits=[Decimal("3350")], timestamp=datetime.now(timezone.utc), confidence=Confidence.HIGH, raw_text="test")


def risk() -> RiskContext:
    return RiskContext(equity=Decimal("50000"), risk_percent=Decimal("0.0035"), maximum_risk=Decimal("200"), remaining_daily_buffer=Decimal("1000"), remaining_overall_buffer=Decimal("4000"), maximum_total_exposure=Decimal("1000"))


def test_automation_prohibited_rejects() -> None:
    decision = evaluate_rules(RuleContext(symbol="XAUUSD", direction=Direction.BUY, automation_requested=True, proposed_risk=Decimal("175"), remaining_daily_buffer=Decimal("1000"), remaining_overall_buffer=Decimal("4000"), open_positions=0, maximum_positions=2))
    assert not decision.approved
    assert "automation" in decision.reasons[0]


def test_mock_execution_is_idempotent() -> None:
    broker = MockBrokerAdapter()
    broker.connect()
    engine = ExecutionEngine()
    preflight = engine.preflight(signal(), broker, risk(), True, True)
    first = engine.execute(signal(), broker, preflight, "same-key", TradingMode.SHADOW)
    second = engine.execute(signal(), broker, preflight, "same-key", TradingMode.SHADOW)
    assert first.accepted and second.accepted
    assert first.broker_order_id == second.broker_order_id
    assert len(broker.get_open_positions()) == 1


def test_broker_disconnect_fails_closed() -> None:
    broker = MockBrokerAdapter()
    result = ExecutionEngine().preflight(signal(), broker, risk(), True, True)
    assert not result.approved
    assert "Broker is disconnected" in result.reasons


def test_shadow_never_submits_to_live_adapter() -> None:
    class PretendLive(MockBrokerAdapter):
        pass

    broker = PretendLive()
    broker.connect()
    engine = ExecutionEngine()
    preflight = engine.preflight(signal(), broker, risk(), True, True)
    result = engine.execute(signal(), broker, preflight, "live-key", TradingMode.SHADOW)
    assert not result.accepted
    assert result.code == "SHADOW_GUARD"


def test_live_mode_remains_explicitly_disabled() -> None:
    broker = MockBrokerAdapter()
    broker.connect()
    engine = ExecutionEngine()
    preflight = engine.preflight(signal(), broker, risk(), True, True)
    result = engine.execute(signal(), broker, preflight, "live-key", TradingMode.LIVE)
    assert not result.accepted
    assert result.code == "LIVE_GUARD"


def test_reconciliation_detects_volume_stop_and_tp_mismatches() -> None:
    broker = MockBrokerAdapter()
    broker.connect()
    result = ExecutionEngine().execute(signal(), broker, ExecutionEngine().preflight(signal(), broker, risk(), True, True), "reconcile", TradingMode.SHADOW)
    assert result.broker_order_id
    issues = reconcile_positions([{"broker_position_id": result.broker_order_id, "remaining_volume": Decimal("9"), "stop_loss": Decimal("3300"), "take_profit": Decimal("4000")}], broker)
    assert {issue.kind for issue in issues} == {"VOLUME_MISMATCH", "SL_MISMATCH", "TP_MISMATCH"}
