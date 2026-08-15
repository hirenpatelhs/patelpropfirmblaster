from decimal import Decimal

from pydantic import BaseModel, Field


class InstrumentSpec(BaseModel):
    symbol: str
    tick_size: Decimal = Field(gt=0)
    tick_value: Decimal = Field(gt=0)
    volume_min: Decimal = Field(gt=0)
    volume_max: Decimal = Field(gt=0)
    volume_step: Decimal = Field(gt=0)
    contract_size: Decimal = Field(gt=0)


class RiskContext(BaseModel):
    equity: Decimal = Field(gt=0)
    risk_percent: Decimal = Field(gt=0, le=Decimal("0.05"))
    fixed_currency_risk: Decimal | None = Field(default=None, gt=0)
    maximum_risk: Decimal = Field(gt=0)
    remaining_daily_buffer: Decimal = Field(gt=0)
    remaining_overall_buffer: Decimal = Field(gt=0)
    current_total_exposure: Decimal = Field(default=Decimal("0"), ge=0)
    maximum_total_exposure: Decimal = Field(gt=0)
    consecutive_losses: int = Field(default=0, ge=0)
    drawdown_recovery: bool = False


class PositionSizeResult(BaseModel):
    approved: bool
    reasons: list[str] = Field(default_factory=list)
    risk_amount: Decimal = Decimal("0")
    risk_percent: Decimal = Decimal("0")
    size: Decimal = Decimal("0")
    loss_per_lot: Decimal = Decimal("0")
