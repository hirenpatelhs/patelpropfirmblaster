from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.brokers.base import OrderRequest
from app.brokers.mock import MockBrokerAdapter
from app.core.enums import Direction, OrderType, TargetStatus
from app.models.entities import AccountDailyStat, Notification, Trade
from app.monitoring.service import ReconciliationService
from app.positions.guard import ConcurrentLimits, evaluate_concurrent_positions
from app.positions.manager import LifecycleRecorder, PositionManagementService, managed_position_from_plan
from app.positions.tp_allocation import TargetAllocation, allocate_take_profits
from app.positions.updates import apply_position_update
from app.schemas.signal import SignalUpdate


class ScalarRows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class FakeSession:
    def __init__(self, targets, profile):
        self.targets = targets
        self.profile = profile
        self.added: list[object] = []
        self.scalar_calls = 0

    async def scalars(self, _query):
        return ScalarRows(self.targets)

    async def scalar(self, _query):
        self.scalar_calls += 1
        return None  # no existing Trade, then no existing AccountDailyStat

    async def get(self, _model, _identifier):
        return self.profile

    def add(self, item):
        self.added.append(item)

    def add_all(self, items):
        self.added.extend(items)


def broker_flat_fixture():
    broker = MockBrokerAdapter()
    broker.connect()
    broker.set_price("XAUUSD", Decimal("104"), Decimal("104.20"))
    opened = broker.place_order(OrderRequest("broker-flat", "XAUUSD", Direction.BUY, OrderType.MARKET, Decimal("0.02"), Decimal("99"), Decimal("104")))
    broker.positions.pop(opened.broker_order_id or "")  # external TP/SL/manual broker closure
    account = SimpleNamespace(id=uuid4(), rule_profile_id=uuid4(), name="DEMO", current_balance=Decimal("50000"), current_equity=Decimal("50000"))
    position_id = uuid4()
    signal_id = uuid4()
    row = SimpleNamespace(
        id=position_id, account_id=account.id, signal_id=signal_id, correlation_id="closure-test",
        broker_position_id=opened.broker_order_id, broker_symbol="XAUUSD", symbol="XAUUSD", direction=Direction.BUY,
        entry_price=Decimal("100.20"), stop_loss=Decimal("99"), take_profit=Decimal("104"),
        original_volume=Decimal("0.10"), remaining_volume=Decimal("0.02"), size=Decimal("0.02"), status="OPEN",
        metadata_json={"realized_pnl": "12", "initial_risk": "12", "journal": []}, created_at=datetime.now(timezone.utc),
    )
    targets = [SimpleNamespace(position_id=position_id, sequence=index, price=Decimal(str(100 + index)), status=TargetStatus.EXECUTED if index < 4 else TargetStatus.WAITING, executed_at=None) for index in range(1, 5)]
    profile = SimpleNamespace(daily_reset_timezone="Europe/London")
    return broker, account, row, targets, profile


@pytest.mark.asyncio
async def test_broker_flat_reconciliation_closes_records_notifies_and_is_idempotent() -> None:
    broker, account, row, targets, profile = broker_flat_fixture()
    db = FakeSession(targets, profile)
    service = ReconciliationService(db)  # type: ignore[arg-type]

    assert await service._reconcile_broker_closed(account, row, broker, {})
    assert row.status == "CLOSED" and row.remaining_volume == 0
    trades = [item for item in db.added if isinstance(item, Trade)]
    stats = [item for item in db.added if isinstance(item, AccountDailyStat)]
    notifications = [item for item in db.added if isinstance(item, Notification)]
    assert len(trades) == 1 and trades[0].exit_price is not None and trades[0].closed_at is not None
    assert len(stats) == 1 and stats[0].trades == 1 and stats[0].realized_pnl != 0
    assert [item.channel for item in notifications] == ["DASHBOARD", "TELEGRAM"]
    assert targets[-1].status == TargetStatus.EXECUTED
    assert evaluate_concurrent_positions([{"status": row.status, "symbol": row.symbol, "direction": row.direction.value}], [], "XAUUSD", Direction.BUY, ConcurrentLimits(1, 1, 1, 1)).allowed

    added_after_first = len(db.added)
    assert not await service._reconcile_broker_closed(account, row, broker, {})
    assert len(db.added) == added_after_first
    assert len([item for item in db.added if isinstance(item, Trade)]) == 1


@pytest.mark.asyncio
async def test_disconnected_broker_does_not_assume_position_closed() -> None:
    broker, account, row, targets, profile = broker_flat_fixture()
    broker.disconnect()
    db = FakeSession(targets, profile)
    assert not await ReconciliationService(db)._reconcile_broker_closed(account, row, broker, {})  # type: ignore[arg-type]
    assert row.status == "OPEN" and row.remaining_volume == Decimal("0.02")
    assert not any(isinstance(item, Trade) for item in db.added)


def test_tp4_reply_when_broker_already_flat_skips_second_close() -> None:
    broker = MockBrokerAdapter()
    broker.connect()
    broker.set_price("XAUUSD", Decimal("104"), Decimal("104.20"))
    plan = allocate_take_profits(Decimal("0.10"), [Decimal("101"), Decimal("102"), Decimal("103"), Decimal("104")], Decimal("0.01"), Decimal("0.01"))
    opened = broker.place_order(OrderRequest("tp4-flat", "XAUUSD", Direction.BUY, OrderType.MARKET, Decimal("0.10"), Decimal("99"), Decimal("104")))
    position = managed_position_from_plan(opened.broker_order_id or "", "signal", "XAUUSD", Direction.BUY, opened.fill_price or Decimal("100.20"), Decimal("99"), plan)
    position.targets = [TargetAllocation(target.sequence, target.price, target.requested_percentage, target.allocated_volume, TargetStatus.EXECUTED if target.sequence < 4 else target.status, target.merged_into_sequence) for target in position.targets]
    position.remaining_volume = Decimal("0.02")
    broker.positions.pop(position.position_id)
    recorder = LifecycleRecorder()
    update = SignalUpdate(action="TARGET_HIT", target_sequence=4, confidence="HIGH")
    application = apply_position_update(PositionManagementService(broker, recorder), position, update)
    assert application.successful
    assert application.results[0].code == "ALREADY_CLOSED"
    assert position.status == "CLOSED" and position.remaining_volume == 0
    assert [event["action"] for event in recorder.events] == ["BROKER_ALREADY_FLAT"]
