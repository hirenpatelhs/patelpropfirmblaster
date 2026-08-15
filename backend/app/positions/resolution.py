from dataclasses import dataclass
from datetime import datetime

from app.schemas.signal import SignalUpdate


@dataclass(frozen=True)
class ActivePositionRef:
    position_id: str
    source_id: str
    signal_message_id: int
    symbol: str
    opened_at: datetime


@dataclass(frozen=True)
class UpdateResolution:
    position_id: str | None
    reason: str
    manual_review_required: bool = False


def resolve_update(
    update: SignalUpdate,
    source_id: str,
    reply_to_message_id: int | None,
    positions: list[ActivePositionRef],
) -> UpdateResolution:
    candidates = [position for position in positions if position.source_id == source_id]
    if reply_to_message_id is not None:
        replied = [position for position in candidates if position.signal_message_id == reply_to_message_id]
        if len(replied) == 1:
            return UpdateResolution(replied[0].position_id, "REPLY_TO_SIGNAL")
        if len(replied) > 1:
            return UpdateResolution(None, "AMBIGUOUS_REPLY", True)
    if update.symbol:
        symbol_matches = [position for position in candidates if position.symbol == update.symbol]
        if len(symbol_matches) == 1:
            return UpdateResolution(symbol_matches[0].position_id, "SOURCE_AND_SYMBOL")
        if len(symbol_matches) > 1:
            return UpdateResolution(None, "AMBIGUOUS_SOURCE_AND_SYMBOL", True)
    if len(candidates) == 1:
        return UpdateResolution(candidates[0].position_id, "ONLY_ACTIVE_SOURCE_POSITION")
    if not candidates:
        return UpdateResolution(None, "NO_ACTIVE_POSITION")
    return UpdateResolution(None, "AMBIGUOUS_ACTIVE_POSITIONS", True)
