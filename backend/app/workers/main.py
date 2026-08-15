import asyncio

import structlog

from app.database.session import SessionLocal
from app.workers.queue import WorkQueue
from app.workers.leadership import SchedulerLeadership
from app.workers.pipeline import TelegramPipeline


logger = structlog.get_logger()


async def telegram_message(payload: dict[str, object]) -> None:
    async with SessionLocal() as db:
        await TelegramPipeline(db).process(payload)


async def route_signal(payload: dict[str, object]) -> None:
    from uuid import UUID
    async with SessionLocal() as db:
        await TelegramPipeline(db).route_saved(UUID(str(payload["signal_id"])))


async def reconcile(payload: dict[str, object]) -> None:
    # Import locally so the queue remains lightweight and each run owns a fresh
    # database transaction.
    from app.monitoring.service import ReconciliationService
    async with SessionLocal() as db:
        await ReconciliationService(db).run(payload)


async def aggregate(payload: dict[str, object]) -> None:
    from app.monitoring.service import AggregateService
    async with SessionLocal() as db:
        await AggregateService(db).run(payload)


async def notify(payload: dict[str, object]) -> None:
    from app.notifications.service import NotificationService
    async with SessionLocal() as db:
        await NotificationService(db).run(payload)


async def scheduled_jobs(queue: WorkQueue) -> None:
    """Elect one scheduler across processes or hosts and renew its lease."""
    leadership = SchedulerLeadership(queue.redis)
    while True:
        if not await leadership.acquire():
            await asyncio.sleep(2)
            continue
        logger.info("scheduler_leadership_acquired", token=leadership.token)
        tick = 0
        try:
            await queue.enqueue("reconcile", {"reason": "leader_startup_recovery"})
            while leadership.held:
                await asyncio.sleep(2)
                if not await leadership.renew():
                    logger.warning("scheduler_leadership_lost")
                    break
                tick += 1
                await queue.enqueue("reconcile", {"reason": "position_monitor"})
                if tick % 3 == 0:
                    await queue.enqueue("notify", {})
                if tick % 30 == 0:
                    await queue.enqueue("aggregate", {})
        finally:
            await leadership.release()


async def main() -> None:
    queue = WorkQueue()
    handlers = {"telegram_message": telegram_message, "route_signal": route_signal, "execute": route_signal, "reconcile": reconcile, "notify": notify, "aggregate": aggregate}
    async with asyncio.TaskGroup() as group:
        group.create_task(scheduled_jobs(queue))
        group.create_task(queue.run(handlers))


if __name__ == "__main__":
    asyncio.run(main())
