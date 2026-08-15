from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.brokers.base import OrderRequest
from app.brokers.mock import MockBrokerAdapter
from app.core.enums import Decision, Direction, DrawdownType, OrderType, RiskClassification, TargetStatus
from app.execution.shadow import ShadowAccount, ShadowTradingEngine
from app.monitoring.recovery import reconcile_positions
from app.positions.guard import ConcurrentLimits, evaluate_concurrent_positions
from app.positions.manager import LifecycleRecorder, PositionManagementService, managed_position_from_plan
from app.positions.resolution import ActivePositionRef, resolve_update
from app.positions.tp_allocation import allocate_take_profits
from app.positions.updates import apply_position_update
from app.risk.drawdown import calculate_drawdown, update_high_water
from app.risk.trading_day import trading_date
from app.schemas.signal import SignalUpdate
from app.signal_engine.fingerprint import signal_fingerprint
from app.signal_parser.parser import DeterministicSignalParser
from app.testing.replay_telegram import load_jsonl
from app.workers.leadership import RELEASE_SCRIPT, RENEW_SCRIPT, SchedulerLeadership
from app.workers.pipeline import existing_message_disposition, routing_enabled
from app.workers.queue import WorkQueue


parser = DeterministicSignalParser()


def connected_broker(symbol: str = "XAUUSD", bid: str = "100", ask: str = "100.20") -> MockBrokerAdapter:
    broker = MockBrokerAdapter()
    broker.connect()
    broker.set_price(symbol, Decimal(bid), Decimal(ask))
    return broker


def four_tp_signal(message_id: int = 1, text_prefix: str = ""):
    return parser.parse(
        f"{text_prefix}\nXAUUSD BUY NOW 100.20\nSL 99\nTP1 101\nTP2 102\nTP3 103\nTP4 104",
        "guru", message_id, datetime.now(timezone.utc),
    )


def test_normal_four_tp_signal_opens_one_position_not_four() -> None:
    broker = connected_broker()
    run = ShadowTradingEngine().route(four_tp_signal(), [ShadowAccount("account", broker)])
    assert len(run.decisions) == 1 and run.decisions[0].decision == Decision.APPROVED
    assert len(broker.positions) == 1
    position = next(iter(run.positions.values()))
    assert len(position.targets) == 4


def test_high_risk_reduces_risk_and_three_accounts_get_independent_outcomes() -> None:
    brokers = [connected_broker() for _ in range(3)]
    signal = four_tp_signal(text_prefix="HIGH RISK ⚠️")
    run = ShadowTradingEngine().route(signal, [
        ShadowAccount("evaluation", brokers[0], base_risk_percent=Decimal("0.003")),
        ShadowAccount("funded", brokers[1], base_risk_percent=Decimal("0.002")),
        ShadowAccount("daily-dd", brokers[2], base_risk_percent=Decimal("0.003"), blocking_reasons=["DAILY_LOSS_LOCK"]),
    ])
    assert [row.effective_risk_percent for row in run.decisions] == [Decimal("0.00150"), Decimal("0.00100"), Decimal("0.00150")]
    assert [row.decision for row in run.decisions] == [Decision.APPROVED, Decision.APPROVED, Decision.REJECTED]
    assert sum(len(broker.positions) for broker in brokers) == 2


def test_repost_and_worker_restart_keys_remain_idempotent() -> None:
    original = four_tp_signal(message_id=10)
    repost = four_tp_signal(message_id=11)
    assert signal_fingerprint(original) == signal_fingerprint(repost)
    assert existing_message_disposition(False, True) == "IGNORE_DUPLICATE"
    broker = connected_broker()
    request = OrderRequest("durable-signal:account", "XAUUSD", Direction.BUY, OrderType.MARKET, Decimal("0.10"), Decimal("99"), None)
    assert broker.place_order(request).broker_order_id == broker.place_order(request).broker_order_id
    assert len(broker.positions) == 1


def test_edits_before_and_after_entry_have_distinct_safe_dispositions() -> None:
    assert existing_message_disposition(True, False) == "REPARSE"
    assert existing_message_disposition(True, True) == "EXECUTED_EDIT"
    assert existing_message_disposition(False, False) == "IGNORE_DUPLICATE"


def test_messy_guru_signal_and_contextual_updates_parse_deterministically() -> None:
    signal = parser.parse(
        "XAUUSD BUY NOW 3351-3348\n\nSL 3343\n\nTP 3355\nTP 3360\nTP 3366\nTP OPEN\n\nHIGH RISK ⚠️",
        "guru", 100,
    )
    assert (signal.entry_min, signal.entry_max) == (Decimal("3348"), Decimal("3351"))
    assert signal.take_profits == [Decimal("3355"), Decimal("3360"), Decimal("3366")]
    assert signal.risk_classification == RiskClassification.HIGH_RISK
    compound = parser.parse_update("Gold running +40\nBook partial and BE")
    assert compound.action == "PARTIAL_CLOSE_AND_BREAK_EVEN" and compound.symbol == "XAUUSD"
    assert parser.parse_update("TP1 ✅\nHold guys").action == "TARGET_HIT"
    close = parser.parse_update("Close here")
    assert close.action == "CLOSE" and close.symbol is None


def test_move_be_and_half_close_apply_to_remaining_broker_step_volume() -> None:
    broker = connected_broker()
    run = ShadowTradingEngine().route(four_tp_signal(), [ShadowAccount("account", broker)])
    decision = run.decisions[0]
    position = run.positions[decision.position_id or ""]
    manager = ShadowTradingEngine.manager(ShadowAccount("account", broker), run, position.position_id)
    assert manager.move_break_even(position).accepted
    assert position.stop_loss == position.entry_price
    before = position.remaining_volume
    result = manager.partial_close(position, Decimal("0.5"))
    assert result.accepted
    closed = before - position.remaining_volume
    assert closed <= before * Decimal("0.5")
    assert closed % Decimal("0.01") == 0


def test_contextual_book_partial_and_be_executes_both_actions() -> None:
    broker = connected_broker()
    plan = allocate_take_profits(Decimal("0.10"), [Decimal("101"), Decimal("102")], Decimal("0.01"), Decimal("0.01"))
    opened = broker.place_order(OrderRequest("compound", "XAUUSD", Direction.BUY, OrderType.MARKET, Decimal("0.10"), Decimal("99"), None))
    position = managed_position_from_plan(opened.broker_order_id or "", "signal", "XAUUSD", Direction.BUY, opened.fill_price or Decimal("100.20"), Decimal("99"), plan)
    recorder = LifecycleRecorder()
    application = apply_position_update(PositionManagementService(broker, recorder), position, parser.parse_update("Gold running +40\nBook partial and BE"))
    assert application.successful and len(application.results) == 2
    assert position.remaining_volume == Decimal("0.05")
    assert position.stop_loss == position.entry_price
    assert [event["action"] for event in recorder.events] == ["MANUAL_PARTIAL", "BREAK_EVEN_MOVED"]


def test_fred_compound_tp1_and_sl_entry_executes_both_actions() -> None:
    broker = connected_broker()
    run = ShadowTradingEngine().route(four_tp_signal(), [ShadowAccount("account", broker)])
    position = next(iter(run.positions.values()))
    recorder = LifecycleRecorder()
    manager = PositionManagementService(broker, recorder)
    update = parser.parse_update("TP1 Hit ✅. SL entry.")
    application = apply_position_update(manager, position, update)
    assert application.successful and len(application.results) == 2
    assert position.targets[0].status == TargetStatus.EXECUTED
    assert position.stop_loss == position.entry_price


def test_close_gold_with_two_active_gold_signals_fails_ambiguous() -> None:
    now = datetime.now(timezone.utc)
    update = parser.parse_update("CLOSE GOLD NOW")
    result = resolve_update(update, "guru", None, [
        ActivePositionRef("position-a", "guru", 1, "XAUUSD", now),
        ActivePositionRef("position-b", "guru", 2, "XAUUSD", now),
    ])
    assert result.position_id is None and result.manual_review_required


def test_restart_restores_executed_tp_and_does_not_close_it_twice() -> None:
    broker = connected_broker()
    plan = allocate_take_profits(Decimal("0.10"), [Decimal("101"), Decimal("102"), Decimal("103"), Decimal("104")], Decimal("0.01"), Decimal("0.01"))
    opened = broker.place_order(OrderRequest("restart", "XAUUSD", Direction.BUY, OrderType.MARKET, Decimal("0.10"), Decimal("99"), None))
    position = managed_position_from_plan(opened.broker_order_id or "", "signal", "XAUUSD", Direction.BUY, opened.fill_price or Decimal("100.20"), Decimal("99"), plan)
    manager = PositionManagementService(broker)
    broker.set_price("XAUUSD", Decimal("101.20"), Decimal("101.40"))
    assert len(manager.monitor(position)) == 2
    assert position.stop_loss == position.entry_price
    remaining = position.remaining_volume

    restored_broker = connected_broker(bid="101.20", ask="101.40")
    restored_broker.positions[position.position_id] = dict(broker.positions[position.position_id])
    restored = position.__class__(**{**position.__dict__, "targets": list(position.targets), "journal": list(position.journal)})
    restored_recorder = LifecycleRecorder()
    restored_manager = PositionManagementService(restored_broker, restored_recorder)
    assert restored_manager.monitor(restored) == []
    assert restored.remaining_volume == remaining
    restored_broker.set_price("XAUUSD", Decimal("102.20"), Decimal("102.40"))
    assert len(restored_manager.monitor(restored)) == 2
    assert [target.status for target in restored.targets[:2]] == [TargetStatus.EXECUTED, TargetStatus.EXECUTED]
    assert restored.stop_loss == Decimal("101")


@pytest.mark.parametrize("mapped", ["GOLD", "XAUUSD.a"])
def test_account_symbol_mapping_preserves_canonical_signal(mapped: str) -> None:
    broker = connected_broker(mapped)
    signal = four_tp_signal()
    run = ShadowTradingEngine().route(signal, [ShadowAccount("mapped", broker, symbol_mappings={"XAUUSD": mapped})])
    assert run.decisions[0].decision == Decision.APPROVED
    assert next(iter(run.positions.values())).symbol == mapped
    assert signal.symbol == "XAUUSD"


def test_buy_ask_sell_bid_and_exit_sides() -> None:
    broker = connected_broker()
    buy = broker.place_order(OrderRequest("buy", "XAUUSD", Direction.BUY, OrderType.MARKET, Decimal("0.10"), Decimal("99"), None))
    sell = broker.place_order(OrderRequest("sell", "XAUUSD", Direction.SELL, OrderType.MARKET, Decimal("0.10"), Decimal("101"), None))
    assert buy.fill_price == Decimal("100.20") and sell.fill_price == Decimal("100")
    assert broker.close_position(buy.broker_order_id or "").fill_price == Decimal("100")
    assert broker.close_position(sell.broker_order_id or "").fill_price == Decimal("100.20")


@pytest.mark.parametrize("volume", [Decimal("0.01"), Decimal("0.02")])
def test_tiny_position_tp_plan_is_valid(volume: Decimal) -> None:
    plan = allocate_take_profits(volume, [Decimal("101"), Decimal("102"), Decimal("103"), Decimal("104")], Decimal("0.01"), Decimal("0.01"))
    assert sum((target.allocated_volume for target in plan.targets), Decimal("0")) == volume
    assert all(target.allocated_volume == 0 or target.allocated_volume >= Decimal("0.01") for target in plan.targets)
    assert len(plan.executable) == int(volume / Decimal("0.01"))


def test_concurrent_position_limit_counts_position_not_virtual_targets() -> None:
    decision = evaluate_concurrent_positions(
        [{"id": "one", "symbol": "XAUUSD", "direction": "BUY", "status": "OPEN", "virtual_targets": 4}], [],
        "XAUUSD", Direction.BUY, ConcurrentLimits(1, 1, 1, 2),
    )
    assert not decision.allowed
    assert "MAX_POSITIONS reached" in decision.reasons


def test_daily_timezone_rollover_and_high_water_survive_restart() -> None:
    now = datetime(2026, 8, 14, 23, 30, tzinfo=timezone.utc)
    assert trading_date(now, "Europe/London").isoformat() == "2026-08-15"
    assert trading_date(now, "America/New_York").isoformat() == "2026-08-14"
    high = update_high_water(Decimal("51000"), Decimal("51200"), Decimal("50500"), Decimal("51100"))
    assert high.balance == Decimal("51000") and high.equity == Decimal("51200")
    state = calculate_drawdown(DrawdownType.TRAILING_EQUITY, Decimal("50000"), Decimal("50500"), Decimal("50000"), high.balance, high.equity, Decimal("3000"), Decimal("500"))
    restored = calculate_drawdown(DrawdownType.TRAILING_EQUITY, Decimal("50000"), Decimal("50500"), Decimal("50000"), high.balance, high.equity, Decimal("3000"), Decimal("500"))
    assert restored == state and state.threshold == Decimal("48200")


def test_emergency_stop_blocks_routes() -> None:
    assert routing_enabled(None)
    assert not routing_enabled({"global_trading_enabled": False})


def test_price_gap_through_tp1_and_tp2_executes_both_once_in_order() -> None:
    broker = connected_broker()
    run = ShadowTradingEngine().route(four_tp_signal(), [ShadowAccount("account", broker)])
    position = next(iter(run.positions.values()))
    recorder = run.recorders[position.position_id]
    manager = PositionManagementService(broker, recorder)
    broker.set_price("XAUUSD", Decimal("102.50"), Decimal("102.70"))
    assert len(manager.monitor(position)) == 4
    assert manager.monitor(position) == []
    executed = [event["sequence"] for event in recorder.events if event["action"] == "VIRTUAL_TP_EXECUTED"]
    assert executed == [1, 2]
    assert position.stop_loss == Decimal("101")


def test_broker_side_take_profit_is_final_tp4() -> None:
    broker = connected_broker()
    run = ShadowTradingEngine().route(four_tp_signal(), [ShadowAccount("account", broker)])
    position = next(iter(run.positions.values()))
    assert broker.positions[position.position_id]["take_profit"] == Decimal("104")


def test_fred_full_virtual_plan_closes_40_20_20_20_and_trails_stop() -> None:
    broker = connected_broker()
    plan = allocate_take_profits(Decimal("0.10"), [Decimal("101"), Decimal("102"), Decimal("103"), Decimal("104")], Decimal("0.01"), Decimal("0.01"))
    opened = broker.place_order(OrderRequest("fred-plan", "XAUUSD", Direction.BUY, OrderType.MARKET, Decimal("0.10"), Decimal("99"), Decimal("104")))
    position = managed_position_from_plan(opened.broker_order_id or "", "signal", "XAUUSD", Direction.BUY, opened.fill_price or Decimal("100.20"), Decimal("99"), plan)
    manager = PositionManagementService(broker)
    remaining: list[Decimal] = []
    stops: list[Decimal] = []
    for price in (Decimal("101"), Decimal("102"), Decimal("103"), Decimal("104")):
        broker.set_price("XAUUSD", price, price + Decimal("0.20"))
        assert all(result.accepted for result in manager.monitor(position))
        remaining.append(position.remaining_volume)
        stops.append(position.stop_loss)
    assert [target.allocated_volume for target in position.targets] == [Decimal("0.04"), Decimal("0.02"), Decimal("0.02"), Decimal("0.02")]
    assert remaining == [Decimal("0.06"), Decimal("0.04"), Decimal("0.02"), Decimal("0")]
    assert stops[:3] == [position.entry_price, Decimal("101"), Decimal("101")]
    assert position.status == "CLOSED"


def test_stop_guard_rejects_backward_moves_for_buy_and_sell() -> None:
    broker = connected_broker()
    buy_opened = broker.place_order(OrderRequest("guard-buy", "XAUUSD", Direction.BUY, OrderType.MARKET, Decimal("0.10"), Decimal("99"), None))
    sell_opened = broker.place_order(OrderRequest("guard-sell", "XAUUSD", Direction.SELL, OrderType.MARKET, Decimal("0.10"), Decimal("102"), None))
    plan = allocate_take_profits(Decimal("0.10"), [Decimal("101")], Decimal("0.01"), Decimal("0.01"))
    buy = managed_position_from_plan(buy_opened.broker_order_id or "", "buy", "XAUUSD", Direction.BUY, buy_opened.fill_price or Decimal("100.20"), Decimal("99"), plan)
    sell = managed_position_from_plan(sell_opened.broker_order_id or "", "sell", "XAUUSD", Direction.SELL, sell_opened.fill_price or Decimal("100"), Decimal("102"), plan)
    manager = PositionManagementService(broker)
    assert manager.move_stop(buy, Decimal("98")).code == "WORSENS_STOP"
    assert manager.move_stop(sell, Decimal("103")).code == "WORSENS_STOP"


@pytest.mark.asyncio
async def test_missing_telegram_heartbeat_fails_closed(monkeypatch) -> None:
    from app.workers import pipeline

    class FakeRedis:
        @classmethod
        def from_url(cls, *_args, **_kwargs):
            return cls()

        async def get(self, _key):
            return None

        async def aclose(self):
            return None

    monkeypatch.setattr(pipeline, "Redis", FakeRedis)
    assert not await pipeline.telegram_listener_healthy()


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def eval(self, script, _keys, key, token, *args):
        if self.values.get(key) != token:
            return 0
        if script == RENEW_SCRIPT:
            return 1
        if script == RELEASE_SCRIPT:
            del self.values[key]
            return 1
        return 0


@pytest.mark.asyncio
async def test_only_one_scheduler_leader_and_non_owner_cannot_release() -> None:
    redis = FakeRedis()
    first = SchedulerLeadership(redis, ttl_seconds=5)  # type: ignore[arg-type]
    second = SchedulerLeadership(redis, ttl_seconds=5)  # type: ignore[arg-type]
    assert await first.acquire()
    assert not await second.acquire()
    assert not await second.release()
    assert await first.renew()
    assert await first.release()
    assert await second.acquire()


class FakeAckRedis:
    def __init__(self) -> None:
        self.acked: list[str] = []
        self.deleted: list[str] = []

    async def xack(self, _stream, _group, message_id):
        self.acked.append(message_id)

    async def xdel(self, _stream, message_id):
        self.deleted.append(message_id)


@pytest.mark.asyncio
async def test_worker_crash_leaves_event_pending_and_retry_is_idempotent() -> None:
    broker = connected_broker()
    queue = WorkQueue("test-stream", "test-group")
    redis = FakeAckRedis()
    queue.redis = redis  # type: ignore[assignment]
    attempts = 0

    async def crash_then_complete(_payload):
        nonlocal attempts
        attempts += 1
        broker.place_order(OrderRequest("same-durable-execution", "XAUUSD", Direction.BUY, OrderType.MARKET, Decimal("0.10"), Decimal("99"), None))
        if attempts == 1:
            raise RuntimeError("simulated process loss after submission")

    fields = {"job": '{"kind":"execute","payload":{"signal_id":"one"}}'}
    await queue._process("1-0", fields, {"execute": crash_then_complete})
    assert redis.acked == [] and len(broker.positions) == 1
    await queue._process("1-0", fields, {"execute": crash_then_complete})
    assert redis.acked == ["1-0"] and redis.deleted == ["1-0"]
    assert len(broker.positions) == 1


def test_historical_jsonl_fixture_enters_as_real_telegram_event_payloads() -> None:
    fixture = Path(__file__).parent / "fixtures" / "messy_guru_messages.jsonl"
    events = load_jsonl(fixture, "replay-chat")
    assert len(events) == 4
    assert events[0]["chat_id"] == "replay-chat"
    assert events[1]["reply_to_message_id"] == 1001
