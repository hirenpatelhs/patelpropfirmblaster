from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.core.enums import Confidence, Direction, OrderType, RiskClassification, SignalStatus


class NormalizedSignal(BaseModel):
    source_id: UUID | str
    telegram_message_id: int
    symbol: str
    direction: Direction
    order_type: OrderType = OrderType.MARKET
    entry_price: Decimal | None = None
    entry_min: Decimal | None = None
    entry_max: Decimal | None = None
    stop_loss: Decimal | None = None
    take_profits: list[Decimal] = Field(default_factory=list)
    risk_hint: str | None = None
    risk_classification: RiskClassification = RiskClassification.NORMAL
    timestamp: datetime
    status: SignalStatus = SignalStatus.PARSED
    confidence: Confidence = Confidence.HIGH
    raw_text: str

    @model_validator(mode="after")
    def validate_price_geometry(self) -> "NormalizedSignal":
        if self.entry_min is not None and self.entry_max is not None and self.entry_min > self.entry_max:
            self.entry_min, self.entry_max = self.entry_max, self.entry_min
        reference = self.entry_price or self.entry_min or self.entry_max
        if reference is not None and self.stop_loss is not None:
            if self.direction == Direction.BUY and self.stop_loss >= reference:
                raise ValueError("BUY stop loss must be below entry")
            if self.direction == Direction.SELL and self.stop_loss <= reference:
                raise ValueError("SELL stop loss must be above entry")
        return self


class SignalUpdate(BaseModel):
    action: str
    symbol: str | None = None
    value: Decimal | None = None
    percentage: Decimal | None = None
    target_sequence: int | None = None
    confidence: Confidence
