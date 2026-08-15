import pytest

from app.models.entities import Notification
from app.notifications.service import NotificationService, queue_notification


class FakeScalars:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class FakeSession:
    def __init__(self, queued=None):
        self.added = []
        self.queued = queued or []
        self.committed = False

    def add_all(self, rows):
        self.added.extend(rows)

    async def scalars(self, _query):
        return FakeScalars(self.queued)

    async def scalar(self, _query):
        return None

    async def commit(self):
        self.committed = True


def test_queue_notification_creates_dashboard_and_telegram_delivery_rows():
    db = FakeSession()
    queue_notification(db, "INFO", "Position opened", "SHADOW XAUUSD", "correlation")  # type: ignore[arg-type]
    assert [row.channel for row in db.added] == ["DASHBOARD", "TELEGRAM"]
    assert all(row.status == "QUEUED" for row in db.added)
    assert all(row.correlation_id == "correlation" for row in db.added)


@pytest.mark.asyncio
async def test_telegram_notification_stays_queued_without_explicit_recipient():
    row = Notification(channel="TELEGRAM", severity="INFO", subject="Daily report", body="No recipient yet", status="QUEUED")
    db = FakeSession([row])
    await NotificationService(db).run({})  # type: ignore[arg-type]
    assert row.status == "QUEUED"
    assert row.sent_at is None
    assert db.committed
