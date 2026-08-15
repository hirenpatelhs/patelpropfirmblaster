import asyncio

from app.telegram.listener import TelegramSignalListener
from app.workers.queue import WorkQueue


async def main() -> None:
    queue = WorkQueue()
    await TelegramSignalListener(lambda payload: queue.enqueue("telegram_message", payload)).run()


if __name__ == "__main__":
    asyncio.run(main())
