from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN
from typing import Any

from app.brokers.base import BrokerAdapter, BrokerResult
from app.core.enums import Direction, TargetStatus
from app.positions.tp_allocation import AllocationPlan, TargetAllocation


@dataclass
class ManagedPosition:
    position_id: str
    signal_id: str
    symbol: str
    direction: Direction
    entry_price: Decimal
    stop_loss: Decimal
    original_volume: Decimal
    remaining_volume: Decimal
    targets: list[TargetAllocation]
    status: str = "OPEN"
    realized_pnl: Decimal = Decimal("0")
    initial_risk: Decimal = Decimal("0")
    journal: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class LifecycleRecorder:
    events: list[dict[str, Any]] = field(default_factory=list)
    notifications: list[dict[str, Any]] = field(default_factory=list)
    audits: list[dict[str, Any]] = field(default_factory=list)

    def record(self, action: str, position: ManagedPosition, payload: dict[str, Any]) -> None:
        item = {"action": action, "position_id": position.position_id, **payload}
        self.events.append(item)
        self.notifications.append(item)
        self.audits.append(item)
        position.journal.append(item)


class PositionManagementService:
    def __init__(self, broker: BrokerAdapter, recorder: LifecycleRecorder | None = None) -> None:
        self.broker = broker
        self.recorder = recorder or LifecycleRecorder()

    def monitor(self, position: ManagedPosition) -> list[BrokerResult]:
        if position.status != "OPEN":
            return []
        current_price = self.broker.get_exit_price(position.symbol, position.direction)
        if current_price is None:
            return []
        results: list[BrokerResult] = []
        for index, target in enumerate(position.targets):
            if target.status != TargetStatus.WAITING:
                continue
            crossed = current_price >= target.price if position.direction == Direction.BUY else current_price <= target.price
            if not crossed:
                continue
            result = self._close_volume(position, target.allocated_volume, f"TP{target.sequence}")
            results.append(result)
            status = TargetStatus.EXECUTED if result.accepted else TargetStatus.FAILED
            position.targets[index] = TargetAllocation(target.sequence, target.price, target.requested_percentage, target.allocated_volume, status, target.merged_into_sequence)
            self.recorder.record("VIRTUAL_TP_EXECUTED" if result.accepted else "VIRTUAL_TP_FAILED", position, {"sequence": target.sequence, "price": str(target.price), "volume": str(target.allocated_volume), "code": result.code})
            if result.accepted and position.status == "OPEN":
                protection = self.protect_after_target(position, target.sequence)
                if protection is not None:
                    results.append(protection)
            if not result.accepted or position.status != "OPEN":
                break
        return results

    def trigger_target(self, position: ManagedPosition, sequence: int) -> BrokerResult:
        for index, target in enumerate(position.targets):
            if target.sequence != sequence:
                continue
            if target.status != TargetStatus.WAITING:
                return BrokerResult(False, position.position_id, None, "TARGET_NOT_WAITING", "Target was already handled", {})
            if target.allocated_volume >= position.remaining_volume:
                if not self.broker.health_check():
                    return BrokerResult(False, position.position_id, None, "DISCONNECTED", "Broker health is unavailable; position closure was not assumed", {})
                broker_ids = {str(item.get("id") or item.get("ticket")) for item in self.broker.get_open_positions()}
                if not self.broker.health_check():
                    return BrokerResult(False, position.position_id, None, "DISCONNECTED", "Broker health failed during position lookup; closure was not assumed", {})
                if position.position_id not in broker_ids:
                    snapshot = self.broker.get_closed_position(position.position_id) or {}
                    exit_price = snapshot.get("price") or self.broker.get_exit_price(position.symbol, position.direction) or target.price
                    closed_volume = position.remaining_volume
                    position.remaining_volume = Decimal("0")
                    position.status = "CLOSED"
                    self._update_pnl(position, Decimal(str(exit_price)), closed_volume)
                    position.targets[index] = TargetAllocation(target.sequence, target.price, target.requested_percentage, target.allocated_volume, TargetStatus.EXECUTED, target.merged_into_sequence)
                    self.recorder.record("BROKER_ALREADY_FLAT", position, {"sequence": target.sequence, "price": str(exit_price), "volume": str(closed_volume), "source": "GURU_UPDATE"})
                    return BrokerResult(True, position.position_id, Decimal(str(exit_price)), "ALREADY_CLOSED", "Broker had already closed the final target", {"deduplicated": True})
            result = self._close_volume(position, target.allocated_volume, f"TP{target.sequence}")
            status = TargetStatus.EXECUTED if result.accepted else TargetStatus.FAILED
            position.targets[index] = TargetAllocation(target.sequence, target.price, target.requested_percentage, target.allocated_volume, status, target.merged_into_sequence)
            self.recorder.record("VIRTUAL_TP_EXECUTED" if result.accepted else "VIRTUAL_TP_FAILED", position, {"sequence": target.sequence, "price": str(target.price), "volume": str(target.allocated_volume), "source": "GURU_UPDATE", "code": result.code})
            return result
        return BrokerResult(False, position.position_id, None, "TARGET_NOT_FOUND", "Target sequence does not exist", {})

    def hold(self, position: ManagedPosition) -> None:
        self.recorder.record("HOLD_CONFIRMED", position, {})

    def protect_after_target(self, position: ManagedPosition, sequence: int) -> BrokerResult | None:
        """Apply the Fred milestone stop policy without ever loosening a stop."""
        if sequence == 1:
            desired = position.entry_price
        elif sequence == 2:
            first_target = next((target for target in position.targets if target.sequence == 1), None)
            if first_target is None:
                return None
            desired = first_target.price
        else:
            return None
        already_protected = desired <= position.stop_loss if position.direction == Direction.BUY else desired >= position.stop_loss
        if already_protected:
            return None
        return self.move_stop(position, desired)

    def move_break_even(self, position: ManagedPosition, offset: Decimal = Decimal("0")) -> BrokerResult:
        proposed = position.entry_price + offset if position.direction == Direction.BUY else position.entry_price - offset
        if position.direction == Direction.BUY and proposed < position.stop_loss:
            return BrokerResult(False, position.position_id, None, "WORSENS_STOP", "Break-even would worsen the stop", {})
        if position.direction == Direction.SELL and proposed > position.stop_loss:
            return BrokerResult(False, position.position_id, None, "WORSENS_STOP", "Break-even would worsen the stop", {})
        result = self.broker.modify_stop_loss(position.position_id, proposed)
        if result.accepted:
            before = position.stop_loss
            position.stop_loss = proposed
            self.recorder.record("BREAK_EVEN_MOVED", position, {"before": str(before), "after": str(proposed)})
        return result

    def move_stop(self, position: ManagedPosition, stop_loss: Decimal) -> BrokerResult:
        if position.direction == Direction.BUY and stop_loss < position.stop_loss:
            return BrokerResult(False, position.position_id, None, "WORSENS_STOP", "Stop cannot be moved downward", {})
        if position.direction == Direction.SELL and stop_loss > position.stop_loss:
            return BrokerResult(False, position.position_id, None, "WORSENS_STOP", "Stop cannot be moved upward", {})
        result = self.broker.modify_stop_loss(position.position_id, stop_loss)
        if result.accepted:
            before = position.stop_loss
            position.stop_loss = stop_loss
            self.recorder.record("STOP_MOVED", position, {"before": str(before), "after": str(stop_loss)})
        return result

    def partial_close(self, position: ManagedPosition, percentage_of_remaining: Decimal) -> BrokerResult:
        if percentage_of_remaining <= 0 or percentage_of_remaining >= 1:
            return BrokerResult(False, position.position_id, None, "INVALID_PARTIAL", "Partial percentage must be between zero and one", {})
        spec = self.broker.get_symbol_info(position.symbol)
        if spec is None:
            return BrokerResult(False, position.position_id, None, "NO_SYMBOL_SPEC", "Symbol specification unavailable", {})
        requested = position.remaining_volume * percentage_of_remaining
        units = (requested / spec.volume_step).to_integral_value(rounding=ROUND_DOWN)
        volume = units * spec.volume_step
        if volume < spec.volume_min:
            return BrokerResult(False, position.position_id, None, "BELOW_MIN_VOLUME", "Partial close is below broker minimum", {})
        return self._close_volume(position, volume, "MANUAL_PARTIAL")

    def close(self, position: ManagedPosition) -> BrokerResult:
        if position.status != "OPEN":
            return BrokerResult(False, position.position_id, None, "ALREADY_CLOSED", "Position is already closed", {})
        result = self.broker.close_position(position.position_id)
        if result.accepted:
            closed = position.remaining_volume
            position.remaining_volume = Decimal("0")
            position.status = "CLOSED"
            self._update_pnl(position, result.fill_price, closed)
            self.recorder.record("POSITION_CLOSED", position, {"volume": str(closed), "price": str(result.fill_price)})
        return result

    def _close_volume(self, position: ManagedPosition, requested_volume: Decimal, reason: str) -> BrokerResult:
        if position.status != "OPEN" or requested_volume <= 0:
            return BrokerResult(False, position.position_id, None, "INVALID_CLOSE", "Position or volume is not closable", {})
        volume = min(requested_volume, position.remaining_volume)
        if volume == position.remaining_volume:
            result = self.broker.close_position(position.position_id)
        else:
            result = self.broker.partial_close(position.position_id, volume=volume)
        if result.accepted:
            position.remaining_volume -= volume
            self._update_pnl(position, result.fill_price, volume)
            if position.remaining_volume <= 0:
                position.remaining_volume = Decimal("0")
                position.status = "CLOSED"
            self.recorder.record(reason, position, {"volume": str(volume), "remaining_volume": str(position.remaining_volume), "price": str(result.fill_price)})
        return result

    def _update_pnl(self, position: ManagedPosition, exit_price: Decimal | None, volume: Decimal) -> None:
        if exit_price is None:
            return
        movement = exit_price - position.entry_price if position.direction == Direction.BUY else position.entry_price - exit_price
        spec = self.broker.get_symbol_info(position.symbol)
        if spec is None:
            position.realized_pnl += movement * volume
            return
        position.realized_pnl += (movement / spec.tick_size) * spec.tick_value * volume


def managed_position_from_plan(position_id: str, signal_id: str, symbol: str, direction: Direction, entry: Decimal, stop: Decimal, plan: AllocationPlan) -> ManagedPosition:
    return ManagedPosition(position_id, signal_id, symbol, direction, entry, stop, plan.original_volume, plan.original_volume, list(plan.targets), initial_risk=abs(entry - stop) * plan.original_volume)
