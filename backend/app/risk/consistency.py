from decimal import Decimal


def consistency_percentage(daily_profits: list[Decimal]) -> Decimal:
    positive = [value for value in daily_profits if value > 0]
    total = sum(positive, Decimal("0"))
    if total <= 0:
        return Decimal("0")
    return (max(positive) / total * Decimal("100")).quantize(Decimal("0.01"))
