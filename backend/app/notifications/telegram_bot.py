import httpx
import structlog

from app.core.config import settings


logger = structlog.get_logger()


class TelegramNotifier:
    async def send(self, chat_id: str, message: str) -> bool:
        if not settings.telegram_bot_token:
            logger.warning("notification_skipped", reason="bot token not configured")
            return False
        url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(url, json={"chat_id": chat_id, "text": message})
            response.raise_for_status()
        return True
