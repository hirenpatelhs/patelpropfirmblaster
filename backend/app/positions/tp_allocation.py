from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN

from app.core.enums import TargetStatus


PRESETS: dict[str, list[Decimal]] = {
    "EQUAL": [],
    "PROTECT": [Decimal("0.40"), Decimal("0.30"), Decimal("0.20"), Decimal("0.10")],
    "RUNNER": [Decimal("0.20"), Decimal("0.20"), Decimal("0.20"), Decimal("0.40")],
}


@dataclass(frozen=True)
class TargetAllocation:
    sequence: int
    price: Decimal
    requested_percentage: Decimal
    allocated_volume: Decimal
    status: TargetStatus
    merged_into_sequence: int | None = None


@dataclass(frozen=True)
class AllocationPlan:
    original_volume: Decimal
    targets: list[TargetAllocation]
    explanation: str

    @property
    def executable(self) -> list[TargetAllocation]:
        return [target for target in self.targets if target.status == TargetStatus.WAITING]


def _requested_percentages(count: int, preset: str, custom: list[Decimal] | None) -> list[Decimal]:
    if count <= 0:
        return []
    if preset == "CUSTOM" and custom:
        values = [max(Decimal("0"), value) for value in custom[:count]]
    elif preset in PRESETS and PRESETS[preset]:
        template = PRESETS[preset]
        values = template[:count]
        if count > len(template):
            values += [Decimal("0") for _ in range(count - len(template))]
    else:
        values = [Decimal("1") / Decimal(count) for _ in range(count)]
    total = sum(values, Decimal("0"))
    if total <= 0:
        return [Decimal("1") / Decimal(count) for _ in range(count)]
    return [value / total for value in values]


def allocate_take_profits(
    position_volume: Decimal,
    prices: list[Decimal],
    volume_min: Decimal,
    volume_step: Decimal,
    preset: str = "PROTECT",
    custom: list[Decimal] | None = None,
) -> AllocationPlan:
    if position_volume <= 0 or volume_min <= 0 or volume_step <= 0:
        raise ValueError("Position and broker volume rules must be positive")
    total_units = int((position_volume / volume_step).to_integral_value(rounding=ROUND_DOWN))
    min_units = max(1, int((volume_min / volume_step).to_integral_value(rounding=ROUND_DOWN)))
    if total_units < min_units:
        raise ValueError("Position volume is below broker minimum")
    if not prices:
        return AllocationPlan(position_volume, [], "No take-profit targets received.")

    percentages = _requested_percentages(len(prices), preset.upper(), custom)
    executable_count = min(len(prices), total_units // min_units)
    if executable_count <= 0:
        raise ValueError("No executable take-profit partial is possible")
    kept = list(range(max(0, executable_count - 1))) + [len(prices) - 1]
    kept = sorted(set(kept))
    allocations: dict[int, int] = {}
    remaining_units = total_units
    for kept_index, target_index in enumerate(kept):
        remaining_targets = len(kept) - kept_index - 1
        if remaining_targets == 0:
            units = remaining_units
        else:
            requested_units = int((Decimal(total_units) * percentages[target_index]).to_integral_value(rounding=ROUND_DOWN))
            units = max(min_units, requested_units)
            units = min(units, remaining_units - remaining_targets * min_units)
        allocations[target_index] = units
        remaining_units -= units

    next_kept: dict[int, int] = {}
    for index in range(len(prices)):
        later = [candidate for candidate in kept if candidate > index]
        next_kept[index] = later[0] if later else kept[-1]
    targets: list[TargetAllocation] = []
    for index, target_price in enumerate(prices):
        if index in allocations:
            targets.append(TargetAllocation(index + 1, target_price, percentages[index], Decimal(allocations[index]) * volume_step, TargetStatus.WAITING))
        else:
            merged_index = next_kept[index]
            targets.append(TargetAllocation(index + 1, target_price, percentages[index], Decimal("0"), TargetStatus.MERGED, merged_index + 1))
    explanation = f"{len(prices)} targets received; {len(kept)} executable partials due to broker volume step." if len(kept) < len(prices) else f"{len(prices)} targets received; all are executable."
    return AllocationPlan(Decimal(total_units) * volume_step, targets, explanation)
