import asyncio
import os
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from redis.asyncio import Redis
from sqlalchemy import func, select, text

from app.core.config import settings
from app.database.session import SessionLocal
from app.models.entities import AuditLog, Notification, Order, Position, PositionEvent, PositionTarget, Signal, SignalAccountDecision, TelegramMessage, Trade, TradingAccount
from app.monitoring.service import ReconciliationService
from app.testing.check_consistency import inspect_consistency
from app.workers.pipeline import TelegramPipeline, broker_registry
from app.workers.leadership import SchedulerLeadership
from app.workers.queue import WorkQueue


pytestmark = pytest.mark.integration
BACKEND_ROOT = Path(__file__).resolve().parents[2]
CHILD_PROCESSES: list[subprocess.Popen] = []


@pytest.fixture(autouse=True)
def cleanup_child_processes():
    yield
    for process in CHILD_PROCESSES:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
    CHILD_PROCESSES.clear()


async def wait_until(predicate, timeout: float = 15, interval: float = 0.2):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        value = await predicate()
        if value:
            return value
        await asyncio.sleep(interval)
    raise AssertionError("Timed out waiting for integration condition")


def worker_process(module: str = "app.workers.main") -> subprocess.Popen:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(BACKEND_ROOT)
    environment["WORKER_CLAIM_IDLE_MS"] = "1000"
    process = subprocess.Popen([sys.executable, "-m", module], cwd=BACKEND_ROOT, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    CHILD_PROCESSES.append(process)
    return process


@pytest.mark.asyncio
async def test_real_versions_and_leadership_ownership(clean_infrastructure):
    async with SessionLocal() as db:
        pg_version = await db.scalar(text("SHOW server_version"))
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    redis_version = (await redis.info("server"))["redis_version"]
    assert pg_version and redis_version
    first, second = SchedulerLeadership(redis, ttl_seconds=3), SchedulerLeadership(redis, ttl_seconds=3)
    assert await first.acquire() and not await second.acquire()
    assert not await second.release()
    assert await first.renew()
    await asyncio.sleep(3.2)
    assert await second.acquire()
    await second.release()
    await redis.aclose()


@pytest.mark.asyncio
async def test_two_workers_single_leader_and_failover(clean_infrastructure):
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    worker_a = worker_process()
    await wait_until(lambda: redis.get("ppb:lock:scheduler"))
    first_token = await redis.get("ppb:lock:scheduler")
    worker_b = worker_process()

    async def two_consumers():
        try:
            consumers = await redis.xinfo_consumers("ppb:jobs:stream", "ppb:workers")
            return len(consumers) >= 2
        except Exception:
            return False

    await wait_until(two_consumers)
    assert await redis.get("ppb:lock:scheduler") == first_token
    worker_a.terminate()
    worker_a.wait(timeout=10)

    async def new_leader():
        token = await redis.get("ppb:lock:scheduler")
        return token if token and token != first_token else None

    assert await wait_until(new_leader, timeout=15)
    worker_b.terminate()
    worker_b.wait(timeout=10)
    await redis.aclose()


@pytest.mark.asyncio
async def test_inflight_crash_reclaim_has_exactly_one_durable_execution(seeded_shadow):
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    worker_a = worker_process("tests.integration.crash_worker")
    queue = WorkQueue(claim_idle_ms=1000)
    await queue.enqueue("telegram_message", {
        "correlation_id": "integration-crash-reclaim", "telegram_message_id": 7001, "chat_id": seeded_shadow["chat_id"],
        "sender_id": "test", "timestamp": datetime.now(timezone.utc).isoformat(),
        "body": "XAUUSD BUY NOW 3344.20 SL 3334 TP1 3350 TP2 3358 TP3 3365 TP4 3375",
        "reply_to_message_id": None, "edited_at": None,
    })
    await wait_until(lambda: redis.get("ppb:test:handler_committed"), timeout=20)
    worker_a.kill()
    worker_a.wait(timeout=10)
    pending = await redis.xpending("ppb:jobs:stream", "ppb:workers")
    assert pending["pending"] == 1
    worker_b = worker_process()

    async def no_pending():
        state = await redis.xpending("ppb:jobs:stream", "ppb:workers")
        return state["pending"] == 0

    await wait_until(no_pending, timeout=20)
    async with SessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(TelegramMessage)) == 1
        assert await db.scalar(select(func.count()).select_from(Signal)) == 1
        assert await db.scalar(select(func.count()).select_from(SignalAccountDecision)) == 1
        assert await db.scalar(select(func.count()).select_from(Order)) == 1
        assert await db.scalar(select(func.count()).select_from(Position)) == 1
        assert await db.scalar(select(func.count()).select_from(Trade)) == 1
        assert await db.scalar(select(func.count()).select_from(PositionTarget)) == 4
        assert await db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "SHADOW_POSITION_OPENED")) == 1
    worker_b.terminate()
    worker_b.wait(timeout=10)
    await redis.aclose()


async def submit_signal(seeded_shadow, message_id: int = 8001) -> None:
    async with SessionLocal() as db:
        await TelegramPipeline(db).process({
            "correlation_id": f"integration-signal-{message_id}", "telegram_message_id": message_id,
            "chat_id": seeded_shadow["chat_id"], "sender_id": "test", "timestamp": datetime.now(timezone.utc).isoformat(),
            "body": "XAUUSD BUY NOW 3344.20 SL 3334 TP1 3350 TP2 3358 TP3 3365 TP4 3375",
            "reply_to_message_id": None, "edited_at": None,
        })


@pytest.mark.asyncio
async def test_postgres_skip_locked_prevents_duplicate_tp_execution(seeded_shadow):
    await submit_signal(seeded_shadow)
    async with SessionLocal() as db:
        account = await db.get(TradingAccount, seeded_shadow["account_id"])
        position = await db.scalar(select(Position))
    assert account and position
    broker = broker_registry.get(account)
    broker.set_price(position.broker_symbol, position.entry_price + 6, position.entry_price + Decimal("6.2"))

    async def monitor_once():
        async with SessionLocal() as session:
            await ReconciliationService(session).run({"account_id": str(account.id), "correlation_id": position.correlation_id})

    await asyncio.gather(monitor_once(), monitor_once())
    async with SessionLocal() as db:
        target = await db.scalar(select(PositionTarget).where(PositionTarget.position_id == position.id, PositionTarget.sequence == 1))
        executions = await db.scalar(select(func.count()).select_from(PositionEvent).where(PositionEvent.position_id == position.id, PositionEvent.event == "VIRTUAL_TP_EXECUTED"))
        assert target and target.status.value == "EXECUTED"
        assert executions == 1


@pytest.mark.asyncio
async def test_full_process_state_recovery_preserves_tp_and_break_even(seeded_shadow):
    await submit_signal(seeded_shadow, 8101)
    async with SessionLocal() as db:
        account = await db.get(TradingAccount, seeded_shadow["account_id"])
        position = await db.scalar(select(Position))
    assert account and position
    broker = broker_registry.get(account)
    broker.set_price(position.broker_symbol, position.entry_price + 6, position.entry_price + Decimal("6.2"))
    async with SessionLocal() as db:
        await ReconciliationService(db).run({"account_id": str(account.id)})
    async with SessionLocal() as db:
        await TelegramPipeline(db).process({
            "correlation_id": "integration-be", "telegram_message_id": 8102, "chat_id": seeded_shadow["chat_id"],
            "sender_id": "test", "timestamp": datetime.now(timezone.utc).isoformat(), "body": "MOVE SL BE",
            "reply_to_message_id": 8101, "edited_at": None,
        })
    async with SessionLocal() as db:
        before = await db.scalar(select(Position))
        targets_before = list((await db.scalars(select(PositionTarget).where(PositionTarget.position_id == before.id).order_by(PositionTarget.sequence))).all())
        assert before and before.stop_loss == before.entry_price and targets_before[0].status.value == "EXECUTED"
        remaining = before.remaining_volume

    broker_registry._brokers.clear()
    async with SessionLocal() as db:
        await ReconciliationService(db).run({"account_id": str(account.id), "reason": "full_process_restart"})
    async with SessionLocal() as db:
        restored = await db.scalar(select(Position))
        assert restored and restored.remaining_volume == remaining and restored.stop_loss == restored.entry_price
        account = await db.get(TradingAccount, account.id)
    restored_broker = broker_registry.get(account)
    restored_broker.set_price(restored.broker_symbol, restored.entry_price + 14, restored.entry_price + Decimal("14.2"))
    async with SessionLocal() as db:
        await ReconciliationService(db).run({"account_id": str(account.id), "reason": "next_price"})
    async with SessionLocal() as db:
        targets_after = list((await db.scalars(select(PositionTarget).where(PositionTarget.position_id == restored.id).order_by(PositionTarget.sequence))).all())
        assert [target.status.value for target in targets_after[:2]] == ["EXECUTED", "EXECUTED"]
        tp1_count = await db.scalar(select(func.count()).select_from(PositionEvent).where(PositionEvent.position_id == restored.id, PositionEvent.event == "VIRTUAL_TP_EXECUTED", PositionEvent.payload["sequence"].as_integer() == 1))
        assert tp1_count == 1


@pytest.mark.asyncio
async def test_correlation_chain_and_consistency_report(seeded_shadow):
    await submit_signal(seeded_shadow, 8201)
    correlation_id = "integration-signal-8201"
    async with SessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(TelegramMessage).where(TelegramMessage.correlation_id == correlation_id)) == 1
        assert await db.scalar(select(func.count()).select_from(Signal).where(Signal.correlation_id == correlation_id)) == 1
        assert await db.scalar(select(func.count()).select_from(SignalAccountDecision).where(SignalAccountDecision.correlation_id == correlation_id)) == 1
        assert await db.scalar(select(func.count()).select_from(Order).where(Order.correlation_id == correlation_id)) == 1
        assert await db.scalar(select(func.count()).select_from(Position).where(Position.correlation_id == correlation_id)) == 1
        assert await db.scalar(select(func.count()).select_from(Trade).where(Trade.correlation_id == correlation_id)) == 1
        assert await db.scalar(select(func.count()).select_from(Notification).where(Notification.correlation_id == correlation_id)) >= 1
        assert await db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.correlation_id == correlation_id)) >= 1
        assert await inspect_consistency(db) == []
