from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.brokers.base import OrderRequest
from app.brokers.mock import MockBrokerAdapter
from app.core.enums import Direction, DrawdownType, OrderType
from app.monitoring.live_equity import trading_day_bounds_utc
from app.prop_rules.engine import RuleContext, evaluate_rules
from app.risk.daily_guard import DailyGuardInput, evaluate_daily_guard
from app.risk.drawdown import calculate_drawdown
from app.risk.live_equity import LiveEquityUnavailable, capture_live_equity, remaining_daily_buffer
from app.risk.trading_day import trading_date


def _daily(realized: Decimal, floating: Decimal, limit: Decimal = Decimal("2500")):
    return evaluate_daily_guard(DailyGuardInput(realized, floating, 1, 0, limit, None, 10, 5))


def test_mock_live_floating_loss_shrinks_buffer_and_fires_daily_brake() -> None:
    broker = MockBrokerAdapter()
    broker.connect()
    broker.set_price("XAUUSD", Decimal("100"), Decimal("100.20"))
    result = broker.place_order(OrderRequest("live-risk", "XAUUSD", Direction.BUY, OrderType.MARKET, Decimal("1.00"), Decimal("70"), Decimal("130")))
    assert result.accepted
    broker.set_price("XAUUSD", Decimal("75.20"), Decimal("75.40"))

    snapshot = capture_live_equity(broker)
    assert snapshot.floating_pnl == Decimal("-2500")
    assert snapshot.equity == Decimal("47500")
    assert remaining_daily_buffer(Decimal("2500"), Decimal("0"), snapshot.floating_pnl) == 0
    assert _daily(Decimal("0"), snapshot.floating_pnl).reasons == ["DAILY_LOSS_LOCK"]


def test_live_equity_drives_drawdown_and_rule_rejection() -> None:
    state = calculate_drawdown(
        DrawdownType.STATIC, Decimal("50000"), Decimal("50000"), Decimal("47000"),
        Decimal("50000"), Decimal("50000"), Decimal("3000"), Decimal("0"),
    )
    rules = evaluate_rules(RuleContext(
        symbol="XAUUSD", direction=Direction.BUY, automation_requested=False,
        proposed_risk=Decimal("1"), remaining_daily_buffer=Decimal("1000"),
        remaining_overall_buffer=max(Decimal("0"), state.remaining_buffer),
        open_positions=0, maximum_positions=1,
    ))
    assert state.breached and state.remaining_buffer == 0
    assert "Remaining overall drawdown buffer insufficient" in rules.reasons


def test_daily_scope_changes_at_profile_trading_day_boundary() -> None:
    before = datetime(2026, 8, 15, 22, 59, tzinfo=timezone.utc)
    after = datetime(2026, 8, 15, 23, 1, tzinfo=timezone.utc)
    old_day = trading_date(before, "Europe/London")
    new_day = trading_date(after, "Europe/London")
    assert old_day.isoformat() == "2026-08-15"
    assert new_day.isoformat() == "2026-08-16"
    old_bounds = trading_day_bounds_utc(old_day, "Europe/London")
    new_bounds = trading_day_bounds_utc(new_day, "Europe/London")
    assert old_bounds[1] < new_bounds[0]
    assert remaining_daily_buffer(Decimal("2500"), Decimal("-2500"), Decimal("0")) == 0
    assert remaining_daily_buffer(Decimal("2500"), Decimal("0"), Decimal("0")) == Decimal("2500")


def test_live_snapshot_is_fail_closed_when_broker_disconnects() -> None:
    broker = MockBrokerAdapter()
    with pytest.raises(LiveEquityUnavailable):
        capture_live_equity(broker)


@pytest.mark.integration
@pytest.mark.skip(reason="VPS opt-in: DEMO live-equity route integration")
def test_demo_position_movement_blocks_next_route_via_live_snapshot() -> None:
    """Opt-in DB/worker integration; unit path above exercises the same mock snapshot and guards."""
