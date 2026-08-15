from datetime import datetime, timezone
from typing import Awaitable, Callable
from uuid import uuid4

import structlog
from telethon import TelegramClient, events

from app.core.config import settings


logger = structlog.get_logger()
MessageHandler = Callable[[dict[str, object]], Awaitable[None]]


class TelegramSignalListener:
    def __init__(self, handler: MessageHandler) -> None:
        if not settings.telegram_api_id or not settings.telegram_api_hash:
            raise RuntimeError("Telegram user-client credentials are not configured")
        self.client = TelegramClient(str(settings.telegram_session_path), settings.telegram_api_id, settings.telegram_api_hash)
        self.handler = handler

    async def run(self) -> None:
        @self.client.on(events.NewMessage())
        @self.client.on(events.MessageEdited())
        async def on_message(event: events.NewMessage.Event) -> None:
            message = event.message
            payload = {
                "correlation_id": str(uuid4()),
                "telegram_message_id": message.id,
                "chat_id": str(event.chat_id),
                "sender_id": str(event.sender_id) if event.sender_id else None,
                "timestamp": message.date.astimezone(timezone.utc),
                "body": message.raw_text,
                "reply_to_message_id": message.reply_to_msg_id,
                "edited_at": message.edit_date.astimezone(timezone.utc) if message.edit_date else None,
            }
            await self.handler(payload)

        await self.client.start()
        logger.info("telegram_listener_connected", timestamp=datetime.now(timezone.utc).isoformat())
        await self.client.run_until_disconnected()
