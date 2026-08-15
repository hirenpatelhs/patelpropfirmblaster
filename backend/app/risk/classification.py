from decimal import Decimal

from app.core.enums import RiskClassification


DEFAULT_RISK_MULTIPLIERS: dict[RiskClassification, Decimal] = {
    RiskClassification.NORMAL: Decimal("1.00"),
    RiskClassification.MEDIUM_RISK: Decimal("0.75"),
    RiskClassification.HIGH_RISK: Decimal("0.50"),
    RiskClassification.VERY_HIGH_RISK: Decimal("0.00"),
}


def effective_risk_percent(
    base_risk_percent: Decimal,
    classification: RiskClassification,
    configured: dict[str, object] | None = None,
) -> Decimal:
    """Risk labels may only preserve or reduce the account's configured base risk."""
    configured_value = (configured or {}).get(classification.value)
    try:
        multiplier = Decimal(str(configured_value)) if configured_value is not None else DEFAULT_RISK_MULTIPLIERS[classification]
    except (ValueError, ArithmeticError):
        multiplier = DEFAULT_RISK_MULTIPLIERS[classification]
    multiplier = max(Decimal("0"), min(multiplier, Decimal("1")))
    return (base_risk_percent * multiplier).quantize(Decimal("0.00001"))
