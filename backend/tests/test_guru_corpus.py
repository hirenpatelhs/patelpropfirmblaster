import json
from pathlib import Path

import pytest

from app.signal_parser.parser import DeterministicSignalParser, ManualReviewRequired


CASES = [json.loads(line) for path in (Path(__file__).parent / "fixtures" / "guru").glob("*.jsonl") for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.mark.parametrize("case", CASES, ids=lambda case: f"message-{case['message_id']}")
def test_anonymized_guru_regression_case(case: dict[str, object]) -> None:
    parser = DeterministicSignalParser()
    expected = case["expected"]
    if case["expected_kind"] == "SIGNAL":
        signal = parser.parse(str(case["text"]), str(case["chat_id"]), int(case["message_id"]))
        assert signal.symbol == expected["symbol"]
        assert signal.direction.value == expected["direction"]
        assert signal.risk_classification.value == expected["risk_classification"]
        assert [str(target) for target in signal.take_profits] == expected["targets"]
        if "entry_price" in expected:
            assert str(signal.entry_price) == expected["entry_price"]
        if "stop_loss" in expected:
            assert str(signal.stop_loss) == expected["stop_loss"]
        if "risk_hint" in expected:
            assert signal.risk_hint == expected["risk_hint"]
    elif case["expected_kind"] == "MANUAL_REVIEW":
        with pytest.raises(ManualReviewRequired, match=str(expected["reason_contains"])):
            parser.parse_update(str(case["text"]))
    else:
        update = parser.parse_update(str(case["text"]))
        for key, value in expected.items():
            actual = getattr(update, key)
            assert getattr(actual, "value", actual) == value
