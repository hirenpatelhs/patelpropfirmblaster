import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import func, select

from app.core.config import settings
from app.database.session import SessionLocal
from app.models.entities import Order, Position, Signal, TelegramMessage
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


def tracked_process(arguments: list[str], environment: dict[str, str]) -> subprocess.Popen:
    process = subprocess.Popen(arguments, cwd=BACKEND_ROOT, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    CHILD_PROCESSES.append(process)
    return process


def control_command(name: str) -> list[str]:
    raw = os.getenv(name)
    if not raw:
        pytest.skip(f"{name} is required for this disruptive VPS-only test")
    value = json.loads(raw)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise RuntimeError(f"{name} must be a JSON array of command arguments")
    return value


def run_control(name: str) -> None:
    subprocess.run(control_command(name), check=True, timeout=30)


def worker_process(module: str = "app.workers.main") -> subprocess.Popen:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(BACKEND_ROOT)
    environment["WORKER_CLAIM_IDLE_MS"] = "1000"
    return tracked_process([sys.executable, "-m", module], environment)


async def wait_until(predicate, timeout: float = 30):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        try:
            value = await predicate()
        except Exception:
            value = None
        if value:
            return value
        await asyncio.sleep(0.25)
    raise AssertionError("Timed out waiting for disruptive integration condition")


def signal_payload(chat_id: str, message_id: int, correlation_id: str) -> dict[str, object]:
    return {
        "correlation_id": correlation_id, "telegram_message_id": message_id, "chat_id": chat_id, "sender_id": "integration",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "body": "XAUUSD BUY NOW 3344.20 SL 3334 TP1 3350 TP2 3358 TP3 3365 TP4 3375",
        "reply_to_message_id": None, "edited_at": None,
    }


@pytest.mark.asyncio
async def test_postgres_outage_leaves_stream_pending_then_recovers(seeded_shadow):
    stop_name, start_name = "PPB_TEST_POSTGRES_STOP_JSON", "PPB_TEST_POSTGRES_START_JSON"
    control_command(stop_name)
    control_command(start_name)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    worker = worker_process()
    await wait_until(lambda: redis.get("ppb:lock:scheduler"))
    run_control(stop_name)
    try:
        await WorkQueue(claim_idle_ms=1000).enqueue("telegram_message", signal_payload(seeded_shadow["chat_id"], 9001, "postgres-outage"))

        async def pending():
            state = await redis.xpending("ppb:jobs:stream", "ppb:workers")
            return state["pending"] >= 1

        await wait_until(pending)
    finally:
        run_control(start_name)

    async def persisted_once():
        async with SessionLocal() as db:
            return await db.scalar(select(func.count()).select_from(TelegramMessage).where(TelegramMessage.correlation_id == "postgres-outage")) == 1

    await wait_until(persisted_once, timeout=45)
    async with SessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(Position)) == 1
        assert await db.scalar(select(func.count()).select_from(Order)) == 1
    worker.terminate()
    worker.wait(timeout=10)
    await redis.aclose()


@pytest.mark.asyncio
async def test_redis_restart_preserves_database_truth(seeded_shadow):
    stop_name, start_name = "PPB_TEST_REDIS_STOP_JSON", "PPB_TEST_REDIS_START_JSON"
    control_command(stop_name)
    control_command(start_name)
    from app.workers.pipeline import TelegramPipeline
    async with SessionLocal() as db:
        await TelegramPipeline(db).process(signal_payload(seeded_shadow["chat_id"], 9101, "redis-restart"))
    worker = worker_process()
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    await wait_until(lambda: redis.get("ppb:lock:scheduler"))
    run_control(stop_name)
    worker.wait(timeout=20)
    run_control(start_name)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    replacement = worker_process()
    await wait_until(lambda: redis.get("ppb:lock:scheduler"), timeout=20)
    async with SessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(Signal).where(Signal.correlation_id == "redis-restart")) == 1
        assert await db.scalar(select(func.count()).select_from(Position).where(Position.correlation_id == "redis-restart")) == 1
    replacement.terminate()
    replacement.wait(timeout=10)
    await redis.aclose()


@pytest.mark.asyncio
async def test_api_restart_does_not_own_trading_state(seeded_shadow):
    from app.workers.pipeline import TelegramPipeline
    async with SessionLocal() as db:
        await TelegramPipeline(db).process(signal_payload(seeded_shadow["chat_id"], 9201, "api-restart"))

    def start_api():
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(BACKEND_ROOT)
        return tracked_process([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "18765"], environment)

    async def healthy():
        async with httpx.AsyncClient(timeout=1) as client:
            response = await client.get("http://127.0.0.1:18765/health")
            return response.status_code == 200

    first = start_api()
    await wait_until(healthy)
    first.terminate()
    first.wait(timeout=10)
    second = start_api()
    await wait_until(healthy)
    async with SessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(Position).where(Position.correlation_id == "api-restart")) == 1
    second.terminate()
    second.wait(timeout=10)


@pytest.mark.asyncio
async def test_duplicate_listener_delivery_is_idempotent(seeded_shadow):
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    worker = worker_process()
    payload = signal_payload(seeded_shadow["chat_id"], 9301, "listener-reconnect")
    queue = WorkQueue(claim_idle_ms=1000)
    await queue.enqueue("telegram_message", payload)
    await queue.enqueue("telegram_message", payload)

    async def processed():
        async with SessionLocal() as db:
            return await db.scalar(select(func.count()).select_from(TelegramMessage).where(TelegramMessage.telegram_message_id == 9301)) == 1

    await wait_until(processed)
    await asyncio.sleep(1.5)
    async with SessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(Signal).where(Signal.telegram_message_id == 9301)) == 1
        assert await db.scalar(select(func.count()).select_from(Position).where(Position.correlation_id == "listener-reconnect")) == 1
    worker.terminate()
    worker.wait(timeout=10)
    await redis.aclose()
