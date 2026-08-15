import asyncio

from app.database.session import SessionLocal
from app.workers.pipeline import TelegramPipeline
from app.workers.queue import WorkQueue


async def main() -> None:
    queue = WorkQueue(claim_idle_ms=1000)

    async def commit_then_hang(payload: dict[str, object]) -> None:
        async with SessionLocal() as db:
            await TelegramPipeline(db).process(payload)
        await queue.redis.set("ppb:test:handler_committed", "1", ex=60)
        await asyncio.sleep(60)

    await queue.run({"telegram_message": commit_then_hang})


if __name__ == "__main__":
    asyncio.run(main())
