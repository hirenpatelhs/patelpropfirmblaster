from decimal import Decimal, ROUND_DOWN

from app.schemas.risk import InstrumentSpec, PositionSizeResult, RiskContext


LOSS_REDUCTION = Decimal("0.5")
RECOVERY_REDUCTION = Decimal("0.6")


def calculate_position_size(
    entry: Decimal,
    stop_loss: Decimal,
    instrument: InstrumentSpec,
    context: RiskContext,
) -> PositionSizeResult:
    stop_distance = abs(entry - stop_loss)
    if stop_distance <= 0:
        return PositionSizeResult(approved=False, reasons=["Stop-loss distance is invalid"])
    ticks = stop_distance / instrument.tick_size
    loss_per_lot = ticks * instrument.tick_value
    if loss_per_lot <= 0:
        return PositionSizeResult(approved=False, reasons=["Instrument tick specification is invalid"])

    risk_percent = context.risk_percent
    if context.consecutive_losses >= 2:
        risk_percent *= LOSS_REDUCTION
    if context.drawdown_recovery:
        risk_percent *= RECOVERY_REDUCTION
    base_risk = context.fixed_currency_risk or context.equity * risk_percent
    available_exposure = context.maximum_total_exposure - context.current_total_exposure
    risk_amount = min(
        base_risk,
        context.maximum_risk,
        context.remaining_daily_buffer,
        context.remaining_overall_buffer,
        available_exposure,
    )
    if risk_amount <= 0:
        return PositionSizeResult(approved=False, reasons=["No risk buffer remains"])
    raw_size = risk_amount / loss_per_lot
    steps = (raw_size / instrument.volume_step).to_integral_value(rounding=ROUND_DOWN)
    size = min(steps * instrument.volume_step, instrument.volume_max)
    if size < instrument.volume_min:
        return PositionSizeResult(
            approved=False,
            reasons=["Calculated size is below broker minimum"],
            risk_amount=risk_amount,
            risk_percent=risk_percent,
            loss_per_lot=loss_per_lot,
        )
    actual_risk = size * loss_per_lot
    return PositionSizeResult(
        approved=True,
        risk_amount=actual_risk.quantize(Decimal("0.01")),
        risk_percent=risk_percent,
        size=size,
        loss_per_lot=loss_per_lot.quantize(Decimal("0.01")),
    )
