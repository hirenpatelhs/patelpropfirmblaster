from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.core.enums import Confidence, Direction
from app.signal_parser.parser import DeterministicSignalParser, ParseError


parser = DeterministicSignalParser()


def test_acceptance_signal_normalizes_range_and_targets() -> None:
    signal = parser.parse("XAUUSD BUY 3342-3345\nSL 3334\nTP1 3350\nTP2 3358\nTP3 3365", uuid4(), 42, datetime.now(timezone.utc))
    assert signal.symbol == "XAUUSD"
    assert signal.direction == Direction.BUY
    assert str(signal.entry_min) == "3342"
    assert str(signal.entry_max) == "3345"
    assert [str(value) for value in signal.take_profits] == ["3350", "3358", "3365"]
    assert signal.confidence == Confidence.HIGH


def test_spelled_out_take_profit_keeps_high_confidence() -> None:
    signal = parser.parse("XAUUSD BUY 3342 SL 3334 TAKE PROFIT 3350", uuid4(), 43, datetime.now(timezone.utc))
    assert signal.take_profits == [Decimal("3350")]
    assert signal.confidence == Confidence.HIGH


def test_unlabelled_small_number_after_direction_is_not_entry_price() -> None:
    signal = parser.parse("XAUUSD BUY 0.1 SL 3334 TP 3350", uuid4(), 44, datetime.now(timezone.utc))
    assert signal.entry_price is None
    assert signal.entry_min is None
    assert signal.entry_max is None


@pytest.mark.parametrize("text,action", [("Move SL BE", "MOVE_BREAK_EVEN"), ("Close 50%", "PARTIAL_CLOSE"), ("Close gold now", "CLOSE")])
def test_updates(text: str, action: str) -> None:
    assert parser.parse_update(text).action == action


def test_ambiguous_text_is_rejected() -> None:
    with pytest.raises(ParseError):
        parser.parse("maybe buy something", uuid4(), 1)
