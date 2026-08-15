import asyncio
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Decision
from app.database.session import SessionLocal
from app.models.entities import Order, Position, PositionEvent, PositionTarget, Signal, SignalAccountDecision


@dataclass(frozen=True)
class ConsistencyIssue:
    code: str
    entity_id: str
    detail: str


async def inspect_consistency(db: AsyncSession) -> list[ConsistencyIssue]:
    issues: list[ConsistencyIssue] = []
    signals = list((await db.scalars(select(Signal))).all())
    decision_counts = dict((await db.execute(select(SignalAccountDecision.signal_id, func.count()).group_by(SignalAccountDecision.signal_id))).all())
    for signal in signals:
        if decision_counts.get(signal.id, 0) == 0:
            issues.append(ConsistencyIssue("SIGNAL_WITHOUT_DECISIONS", str(signal.id), "Signal has no account decision"))

    approved = list((await db.scalars(select(SignalAccountDecision).where(SignalAccountDecision.decision == Decision.APPROVED))).all())
    for decision in approved:
        order = await db.scalar(select(Order.id).where(Order.signal_id == decision.signal_id, Order.account_id == decision.account_id).limit(1))
        position = await db.scalar(select(Position.id).where(Position.signal_id == decision.signal_id, Position.account_id == decision.account_id).limit(1))
        if order is None or position is None:
            issues.append(ConsistencyIssue("APPROVED_WITHOUT_EXECUTION", str(decision.id), "Approved decision lacks an order or position"))

    positions = list((await db.scalars(select(Position))).all())
    targets = list((await db.scalars(select(PositionTarget))).all())
    targets_by_position: dict[object, list[PositionTarget]] = {}
    for target in targets:
        targets_by_position.setdefault(target.position_id, []).append(target)
    position_ids = {position.id for position in positions}
    for target in targets:
        if target.position_id not in position_ids:
            issues.append(ConsistencyIssue("ORPHANED_POSITION_TARGET", str(target.id), "Target references no position"))
    for position in positions:
        original, remaining = Decimal(position.original_volume), Decimal(position.remaining_volume)
        if remaining < 0 or remaining > original:
            issues.append(ConsistencyIssue("INVALID_REMAINING_VOLUME", str(position.id), f"original={original} remaining={remaining}"))
        if position.status == "CLOSED" and remaining != 0:
            issues.append(ConsistencyIssue("CLOSED_WITH_REMAINING_VOLUME", str(position.id), f"remaining={remaining}"))
        allocated = sum((Decimal(target.allocated_volume) for target in targets_by_position.get(position.id, [])), Decimal("0"))
        if allocated > original:
            issues.append(ConsistencyIssue("TARGET_ALLOCATION_EXCEEDS_VOLUME", str(position.id), f"allocated={allocated} original={original}"))
        events = list((await db.scalars(select(PositionEvent).where(PositionEvent.position_id == position.id).order_by(PositionEvent.timestamp))).all())
        closed_volume = Decimal("0")
        executions: Counter[int] = Counter()
        for event in events:
            if re.fullmatch(r"TP\d+", event.event) or event.event in {"MANUAL_PARTIAL", "POSITION_CLOSED"}:
                if event.payload.get("volume") is not None:
                    closed_volume += Decimal(str(event.payload["volume"]))
            if event.event == "VIRTUAL_TP_EXECUTED" and event.payload.get("sequence") is not None:
                executions[int(event.payload["sequence"])] += 1
        if events and closed_volume and original - closed_volume != remaining:
            issues.append(ConsistencyIssue("POSITION_EVENT_VOLUME_MISMATCH", str(position.id), f"events imply {original - closed_volume}, stored {remaining}"))
        for sequence, count in executions.items():
            if count > 1:
                issues.append(ConsistencyIssue("DUPLICATE_TARGET_EXECUTION", str(position.id), f"TP{sequence} has {count} execution events"))

    duplicate_keys = (await db.execute(select(SignalAccountDecision.execution_id, func.count()).where(SignalAccountDecision.execution_id.is_not(None)).group_by(SignalAccountDecision.execution_id).having(func.count() > 1))).all()
    for execution_id, count in duplicate_keys:
        issues.append(ConsistencyIssue("DUPLICATE_EXECUTION_KEY", str(execution_id), f"count={count}"))
    return issues


async def run() -> int:
    async with SessionLocal() as db:
        issues = await inspect_consistency(db)
    report = {"consistent": not issues, "issue_count": len(issues), "issues": [asdict(issue) for issue in issues]}
    print(json.dumps(report, indent=2))
    return 0 if not issues else 1


def main() -> None:
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
