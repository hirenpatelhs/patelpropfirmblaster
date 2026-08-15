from datetime import datetime, timezone
from decimal import Decimal

from app.brokers.base import OrderRequest
from app.brokers.mock import MockBrokerAdapter
from app.core.enums import Decision, Direction, OrderType, RiskClassification, TargetStatus
from app.execution.shadow import ShadowAccount, ShadowTradingEngine
from app.positions.manager import PositionManagementService
from app.positions.resolution import ActivePositionRef, resolve_update
from app.positions.tp_allocation import allocate_take_profits
from app.risk.classification import effective_risk_percent
from app.schemas.signal import SignalUpdate
from app.signal_parser.parser import DeterministicSignalParser


def test_risk_classification_and_multiplier_never_increase_risk() -> None:
    parser = DeterministicSignalParser()
    signal = parser.parse("XAUUSD BUY SL 3334 TP1 3350 HIGH RISK", "source", 1)
    assert signal.risk_classification == RiskClassification.HIGH_RISK
    assert effective_risk_percent(Decimal("0.004"), signal.risk_classification, {"HIGH_RISK": "5"}) == Decimal("0.00400")
    assert effective_risk_percent(Decimal("0.004"), RiskClassification.VERY_HIGH_RISK) == Decimal("0.00000")


def test_tp_allocation_merges_tiny_parts_and_preserves_total_volume() -> None:
    plan = allocate_take_profits(Decimal("0.03"), [Decimal("10"), Decimal("20"), Decimal("30"), Decimal("40")], Decimal("0.01"), Decimal("0.01"))
    assert sum((target.allocated_volume for target in plan.targets), Decimal("0")) == Decimal("0.03")
    assert [(target.sequence, target.status, target.merged_into_sequence) for target in plan.targets] == [
        (1, TargetStatus.WAITING, None), (2, TargetStatus.WAITING, None),
        (3, TargetStatus.MERGED, 4), (4, TargetStatus.WAITING, None),
    ]
    assert "3 executable" in plan.explanation


def test_fred_protect_allocation_is_40_20_20_20() -> None:
    plan = allocate_take_profits(Decimal("0.10"), [Decimal("10"), Decimal("20"), Decimal("30"), Decimal("40")], Decimal("0.01"), Decimal("0.01"))
    assert [target.requested_percentage for target in plan.targets] == [Decimal("0.40"), Decimal("0.20"), Decimal("0.20"), Decimal("0.20")]
    assert [target.allocated_volume for target in plan.targets] == [Decimal("0.04"), Decimal("0.02"), Decimal("0.02"), Decimal("0.02")]


def test_mock_broker_uses_side_aware_entry_and_exit_prices() -> None:
    broker = MockBrokerAdapter()
    broker.connect()
    broker.set_price("XAUUSD", Decimal("100"), Decimal("100.20"))
    buy = broker.place_order(OrderRequest("buy", "XAUUSD", Direction.BUY, OrderType.MARKET, Decimal("0.10"), Decimal("99"), None))
    sell = broker.place_order(OrderRequest("sell", "XAUUSD", Direction.SELL, OrderType.MARKET, Decimal("0.10"), Decimal("101"), None))
    assert buy.fill_price == Decimal("100.20")
    assert sell.fill_price == Decimal("100")
    assert broker.close_position(buy.broker_order_id or "").fill_price == Decimal("100")
    assert broker.close_position(sell.broker_order_id or "").fill_price == Decimal("100.20")


def test_multi_account_high_risk_signal_and_complete_position_lifecycle() -> None:
    brokers = [MockBrokerAdapter() for _ in range(3)]
    for broker in brokers:
        broker.connect()
        broker.set_price("XAUUSD", Decimal("3344.00"), Decimal("3344.20"))
    signal = DeterministicSignalParser().parse(
        "XAUUSD BUY SL 3334 TP1 3345 TP2 3346 TP3 3347 TP4 3350 HIGH RISK", "guru", 4812, datetime.now(timezone.utc)
    )
    accounts = [
        ShadowAccount("evaluation", brokers[0], base_risk_percent=Decimal("0.003")),
        ShadowAccount("funded", brokers[1], base_risk_percent=Decimal("0.002")),
        ShadowAccount("daily-locked", brokers[2], blocking_reasons=["DAILY_LOSS_LOCK"]),
    ]
    engine = ShadowTradingEngine()
    run = engine.route(signal, accounts)
    assert [decision.decision for decision in run.decisions] == [Decision.APPROVED, Decision.APPROVED, Decision.REJECTED]
    assert run.decisions[0].calculated_size > run.decisions[1].calculated_size
    assert run.decisions[0].effective_risk_percent == Decimal("0.00150")
    assert run.decisions[1].effective_risk_percent == Decimal("0.00100")
    assert run.decisions[2].reasons == ["DAILY_LOSS_LOCK"]

    approved = run.decisions[0]
    assert approved.position_id is not None
    position = run.positions[approved.position_id]
    manager = engine.manager(accounts[0], run, approved.position_id)
    brokers[0].set_price("XAUUSD", Decimal("3345.10"), Decimal("3345.30"))
    first_results = manager.monitor(position)
    second_results = manager.monitor(position)
    assert len(first_results) == 2 and all(result.accepted for result in first_results)
    assert second_results == []
    assert sum(event["action"] == "VIRTUAL_TP_EXECUTED" for event in run.recorders[approved.position_id].events) == 1
    assert position.stop_loss == position.entry_price
    remaining_after_tp1 = position.remaining_volume
    brokers[0].set_price("XAUUSD", Decimal("3346.10"), Decimal("3346.30"))
    assert len(manager.monitor(position)) == 2
    assert position.remaining_volume < remaining_after_tp1
    assert sum(event["action"] == "VIRTUAL_TP_EXECUTED" for event in run.recorders[approved.position_id].events) == 2
    assert position.stop_loss == Decimal("3345")
    remaining_after_tp = position.remaining_volume
    assert manager.move_break_even(position).code == "WORSENS_STOP"
    assert manager.partial_close(position, Decimal("0.5")).accepted
    assert position.remaining_volume < remaining_after_tp
    assert manager.close(position).accepted
    assert position.status == "CLOSED" and position.remaining_volume == 0
    assert approved.position_id not in brokers[0].positions
    assert len(run.recorders[approved.position_id].events) == len(run.recorders[approved.position_id].notifications)
    assert len(run.recorders[approved.position_id].events) == len(run.recorders[approved.position_id].audits)


def test_update_resolution_fails_closed_when_symbol_is_ambiguous() -> None:
    now = datetime.now(timezone.utc)
    positions = [
        ActivePositionRef("one", "guru", 10, "XAUUSD", now),
        ActivePositionRef("two", "guru", 11, "XAUUSD", now),
    ]
    update = SignalUpdate(action="MOVE_BREAK_EVEN", symbol="XAUUSD", confidence="HIGH")
    ambiguous = resolve_update(update, "guru", None, positions)
    replied = resolve_update(update, "guru", 11, positions)
    assert ambiguous.manual_review_required and ambiguous.position_id is None
    assert replied.position_id == "two" and replied.reason == "REPLY_TO_SIGNAL"
