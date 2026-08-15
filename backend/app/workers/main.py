import asyncio
from datetime import datetime, timedelta, timezone

import structlog

from app.core.config import settings
from app.database.session import SessionLocal
from app.risk.trading_day import trading_date
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
        reconcile_elapsed = settings.demo_monitor_interval_seconds
        last_daily_report = None
        try:
            await queue.enqueue("reconcile", {"reason": "leader_startup_recovery"})
            while leadership.held:
                await asyncio.sleep(2)
                if not await leadership.renew():
                    logger.warning("scheduler_leadership_lost")
                    break
                tick += 1
                reconcile_elapsed += 2
                if reconcile_elapsed >= settings.demo_monitor_interval_seconds:
                    await queue.enqueue("reconcile", {"reason": "position_monitor"})
                    reconcile_elapsed = 0
                if tick % 3 == 0:
                    await queue.enqueue("notify", {})
                if tick % 30 == 0:
                    await queue.enqueue("aggregate", {})
                report_date = trading_date(datetime.now(timezone.utc), settings.application_timezone) - timedelta(days=1)
                if report_date != last_daily_report:
                    await queue.enqueue("aggregate", {"date": report_date.isoformat(), "notify_daily": True})
                    last_daily_report = report_date
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
