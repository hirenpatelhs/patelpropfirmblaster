import argparse
import asyncio
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from app.core.enums import AccountStatus, Decision, RiskClassification, TradingMode
from app.database.session import SessionLocal
from app.models.entities import AuditLog, Position, PositionEvent, Signal, SignalAccountDecision, SignalUpdate, TelegramMessage, TelegramSource, TradingAccount
from app.workers.queue import WorkQueue


def load_jsonl(path: Path, default_chat_id: str | None = None) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row: dict[str, Any] = json.loads(line)
            message_id = int(row["message_id"])
            body_value = row.get("text", row.get("body"))
            if body_value is None:
                raise ValueError
            body = str(body_value)
            chat_id = str(row.get("chat_id") or default_chat_id or "")
            if not chat_id:
                raise ValueError
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid replay record on line {line_number}") from exc
        events.append({
            "telegram_message_id": message_id,
            "chat_id": chat_id,
            "sender_id": str(row.get("sender_id", "historical-replay")),
            "timestamp": row.get("timestamp") or datetime.now(timezone.utc).isoformat(),
            "body": body,
            "original_text": row.get("original_text"),
            "reply_to_message_id": row.get("reply_to_message_id"),
            "edited_at": row.get("edited_at"),
        })
    return events


async def assert_shadow_only(chat_id: str) -> TelegramSource:
    async with SessionLocal() as db:
        source = await db.scalar(select(TelegramSource).where(TelegramSource.telegram_chat_id == chat_id, TelegramSource.enabled.is_(True)))
        if source is None:
            raise RuntimeError(f"Replay chat_id {chat_id!r} must match an enabled TelegramSource")
        accounts = list((await db.scalars(select(TradingAccount).where(TradingAccount.status == AccountStatus.ACTIVE))).all())
        if source.allowed_account_ids:
            allowed = set(source.allowed_account_ids)
            accounts = [account for account in accounts if str(account.id) in allowed]
        unsafe = [account.name for account in accounts if account.trading_mode != TradingMode.SHADOW]
        if unsafe:
            raise RuntimeError(f"Historical replay refused: non-SHADOW accounts are eligible: {', '.join(unsafe)}")
        return source


async def build_report(correlation_ids: list[str], input_events: int) -> dict[str, Any]:
    async with SessionLocal() as db:
        messages = list((await db.scalars(select(TelegramMessage).where(TelegramMessage.correlation_id.in_(correlation_ids)))).all())
        signals = list((await db.scalars(select(Signal).where(Signal.correlation_id.in_(correlation_ids)))).all())
        decisions = list((await db.scalars(select(SignalAccountDecision).where(SignalAccountDecision.correlation_id.in_(correlation_ids)))).all())
        positions = list((await db.scalars(select(Position).where(Position.correlation_id.in_(correlation_ids)))).all())
        events = list((await db.scalars(select(PositionEvent).where(PositionEvent.correlation_id.in_(correlation_ids)))).all())
        audits = list((await db.scalars(select(AuditLog).where(AuditLog.correlation_id.in_(correlation_ids)))).all())
        signal_ids = [signal.id for signal in signals]
        updates = list((await db.scalars(select(SignalUpdate).where(SignalUpdate.signal_id.in_(signal_ids)))).all()) if signal_ids else []
        accounts = {str(account.id): account.name for account in (await db.scalars(select(TradingAccount))).all()}
    decision_counts = Counter(f"{accounts.get(str(row.account_id), str(row.account_id))}:{row.decision.value}" for row in decisions)
    risk_counts = Counter(signal.risk_classification.value for signal in signals)
    audit_counts = Counter(audit.action for audit in audits)
    event_counts = Counter(event.event for event in events)
    return {
        "messages_processed": input_events,
        "messages_persisted": len(messages),
        "new_signals": len(signals),
        "updates": len(updates),
        "ignored_messages": audit_counts["TELEGRAM_MESSAGE_UNRECOGNIZED"] + audit_counts["DUPLICATE_SIGNAL_IGNORED"],
        "manual_review_messages": audit_counts["SIGNAL_UPDATE_MANUAL_REVIEW_REQUIRED"],
        "signals_accepted": sum(row.decision == Decision.APPROVED for row in decisions),
        "signals_rejected": sum(row.decision == Decision.REJECTED for row in decisions),
        "decisions_by_account": dict(sorted(decision_counts.items())),
        "normal_risk_signals": risk_counts[RiskClassification.NORMAL.value],
        "high_risk_signals": risk_counts[RiskClassification.HIGH_RISK.value] + risk_counts[RiskClassification.VERY_HIGH_RISK.value],
        "positions_created": len(positions),
        "tp_events": event_counts["VIRTUAL_TP_EXECUTED"],
        "partial_closes": event_counts["MANUAL_PARTIAL"],
        "break_even_events": event_counts["BREAK_EVEN_MOVED"],
        "final_closes": event_counts["POSITION_CLOSED"],
        "parser_failures": audit_counts["TELEGRAM_MESSAGE_UNRECOGNIZED"],
        "ambiguous_updates": audit_counts["SIGNAL_UPDATE_MANUAL_REVIEW_REQUIRED"],
        "correlation_ids": correlation_ids,
    }


def print_human_report(report: dict[str, Any]) -> None:
    print("PPB historical replay summary")
    print("=" * 29)
    for key, value in report.items():
        if key != "correlation_ids":
            print(f"{key.replace('_', ' ').title()}: {value}")


async def replay(path: Path, chat_id: str | None, enqueue: bool, wait_seconds: int = 30, report_path: Path | None = None) -> dict[str, Any]:
    events = load_jsonl(path, chat_id)
    if not enqueue:
        report = {"mode": "DRY_RUN", "events": len(events), "chat_ids": sorted({str(event["chat_id"]) for event in events})}
        print(json.dumps(report, indent=2))
        return report
    for event_chat_id in {str(event["chat_id"]) for event in events}:
        await assert_shadow_only(event_chat_id)
    queue = WorkQueue()
    replayed_at = datetime.now(timezone.utc).isoformat()
    batch_id = uuid4().hex
    correlation_ids: list[str] = []
    for index, event in enumerate(events):
        correlation_id = f"replay:{batch_id}:{index}"
        correlation_ids.append(correlation_id)
        event["correlation_id"] = correlation_id
        event["historical_timestamp"] = event["timestamp"]
        event["timestamp"] = replayed_at
        if event.get("edited_at"):
            event["historical_edited_at"] = event["edited_at"]
            event["edited_at"] = replayed_at
        await queue.enqueue("telegram_message", event)
    deadline = asyncio.get_running_loop().time() + wait_seconds
    report = await build_report(correlation_ids, len(events))
    while report["messages_persisted"] + report["manual_review_messages"] < len(events) and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.5)
        report = await build_report(correlation_ids, len(events))
    print_human_report(report)
    machine = json.dumps(report, indent=2)
    print(machine)
    if report_path:
        report_path.write_text(machine + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay anonymized Telegram JSONL into PPB's actual SHADOW worker pipeline")
    parser.add_argument("path", type=Path)
    parser.add_argument("--chat-id", help="Fallback chat_id when records do not contain one")
    parser.add_argument("--enqueue", action="store_true", help="Required to write events; default is validation-only dry run")
    parser.add_argument("--wait-seconds", type=int, default=30)
    parser.add_argument("--report", type=Path, help="Write machine-readable JSON output")
    args = parser.parse_args()
    asyncio.run(replay(args.path, args.chat_id, args.enqueue, args.wait_seconds, args.report))


if __name__ == "__main__":
    main()
