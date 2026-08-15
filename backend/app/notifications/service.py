from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import Notification, SystemSetting
from app.notifications.telegram_bot import TelegramNotifier


def queue_notification(
    db: AsyncSession,
    severity: str,
    subject: str,
    body: str,
    correlation_id: str | None = None,
    *,
    telegram: bool = True,
) -> None:
    channels = ["DASHBOARD"]
    if telegram:
        channels.append("TELEGRAM")
    db.add_all([
        Notification(
            channel=channel,
            correlation_id=correlation_id,
            severity=severity,
            subject=subject,
            body=body,
            status="QUEUED",
        )
        for channel in channels
    ])


class NotificationService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def run(self, payload: dict[str, object]) -> None:
        queued = list((await self.db.scalars(select(Notification).where(Notification.status == "QUEUED").limit(100))).all())
        setting = await self.db.scalar(select(SystemSetting).where(SystemSetting.key == "notification_chat_id"))
        chat_id = str(setting.value.get("chat_id")) if setting and setting.value.get("chat_id") else None
        notifier = TelegramNotifier()
        for item in queued:
            if item.channel == "TELEGRAM" and chat_id:
                delivered = await notifier.send(chat_id, f"[{item.severity}] {item.subject}\n{item.body}")
                item.status = "SENT" if delivered else "FAILED"
            elif item.channel == "TELEGRAM":
                # Keep durable notifications queued until an explicit recipient
                # is configured. Never guess or fall back to the source channel.
                continue
            elif item.channel == "DASHBOARD":
                item.status = "DELIVERED"
            else:
                item.status = "FAILED"
            item.sent_at = datetime.now(timezone.utc) if item.status in {"SENT", "DELIVERED"} else None
        await self.db.commit()
