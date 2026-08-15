from decimal import Decimal

from app.core.enums import DrawdownType
from app.risk.consistency import consistency_percentage
from app.risk.daily_guard import DailyGuardInput, evaluate_daily_guard
from app.risk.drawdown import calculate_drawdown
from app.risk.position_sizing import calculate_position_size
from app.schemas.risk import InstrumentSpec, RiskContext


SPEC = InstrumentSpec(symbol="XAUUSD", tick_size=Decimal("0.01"), tick_value=Decimal("1"), volume_min=Decimal("0.01"), volume_max=Decimal("10"), volume_step=Decimal("0.01"), contract_size=Decimal("100"))


def context(**overrides):
    values = dict(equity=Decimal("50000"), risk_percent=Decimal("0.0035"), maximum_risk=Decimal("200"), remaining_daily_buffer=Decimal("1440"), remaining_overall_buffer=Decimal("4000"), maximum_total_exposure=Decimal("1000"))
    values.update(overrides)
    return RiskContext(**values)


def test_position_sizing_is_deterministic() -> None:
    result = calculate_position_size(Decimal("3344"), Decimal("3336"), SPEC, context())
    assert result.approved
    assert result.size == Decimal("0.21")
    assert result.risk_amount == Decimal("168.00")


def test_size_reduces_after_two_losses_and_never_increases() -> None:
    normal = calculate_position_size(Decimal("3344"), Decimal("3336"), SPEC, context())
    reduced = calculate_position_size(Decimal("3344"), Decimal("3336"), SPEC, context(consecutive_losses=2))
    assert reduced.size < normal.size


def test_near_daily_limit_caps_risk() -> None:
    result = calculate_position_size(Decimal("3344"), Decimal("3336"), SPEC, context(remaining_daily_buffer=Decimal("10")))
    assert result.approved
    assert result.risk_amount <= Decimal("10")


def test_trailing_equity_drawdown_locks_at_safety_threshold() -> None:
    state = calculate_drawdown(DrawdownType.TRAILING_EQUITY, Decimal("50000"), Decimal("50500"), Decimal("48900"), Decimal("51000"), Decimal("52000"), Decimal("2500"), Decimal("500"))
    assert state.safety_threshold == Decimal("50000")
    assert state.breached


def test_daily_guard_locks_after_losses() -> None:
    decision = evaluate_daily_guard(DailyGuardInput(Decimal("-100"), Decimal("0"), 2, 2, Decimal("1000"), Decimal("700"), 4, 2))
    assert not decision.allowed
    assert "MAX_CONSECUTIVE_LOSSES" in decision.reasons


def test_consistency() -> None:
    assert consistency_percentage([Decimal("300"), Decimal("200"), Decimal("500")]) == Decimal("50.00")
