from dataclasses import dataclass, field
from decimal import Decimal

from app.brokers.base import BrokerResult
from app.positions.manager import ManagedPosition, PositionManagementService
from app.schemas.signal import SignalUpdate


@dataclass(frozen=True)
class UpdateApplication:
    results: list[BrokerResult] = field(default_factory=list)

    @property
    def successful(self) -> bool:
        return all(result.accepted for result in self.results)


def apply_position_update(
    manager: PositionManagementService,
    position: ManagedPosition,
    update: SignalUpdate,
    break_even_offset: Decimal = Decimal("0"),
) -> UpdateApplication:
    results: list[BrokerResult] = []
    if update.action == "MOVE_BREAK_EVEN":
        results.append(manager.move_break_even(position, break_even_offset))
    elif update.action == "MOVE_STOP" and update.value is not None:
        results.append(manager.move_stop(position, update.value))
    elif update.action == "PARTIAL_CLOSE" and update.percentage is not None:
        results.append(manager.partial_close(position, update.percentage))
    elif update.action == "PARTIAL_CLOSE_AND_BREAK_EVEN" and update.percentage is not None:
        partial = manager.partial_close(position, update.percentage)
        results.append(partial)
        if partial.accepted:
            results.append(manager.move_break_even(position, break_even_offset))
    elif update.action == "CLOSE":
        results.append(manager.close(position))
    elif update.action == "TARGET_HIT" and update.target_sequence is not None:
        target = manager.trigger_target(position, update.target_sequence)
        results.append(target)
        if target.accepted and position.status == "OPEN":
            protection = manager.protect_after_target(position, update.target_sequence)
            if protection is not None:
                results.append(protection)
    elif update.action == "TARGET_HIT_AND_BREAK_EVEN" and update.target_sequence is not None:
        target = manager.trigger_target(position, update.target_sequence)
        results.append(target)
        if target.accepted and position.status == "OPEN":
            protection = manager.protect_after_target(position, update.target_sequence)
            if protection is not None:
                results.append(protection)
    elif update.action == "HOLD":
        manager.hold(position)
    elif update.action == "CANCEL_PENDING":
        manager.recorder.record("CANCEL_PENDING_REQUESTED", position, {"result": "NO_TRACKED_PENDING_ORDER"})
    else:
        results.append(BrokerResult(False, position.position_id, None, "UNSUPPORTED_UPDATE", "Trade update is incomplete or unsupported", {}))
    return UpdateApplication(results)
