from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from app.core.config import settings


DISPLAY_ZONE = ZoneInfo(settings.application_timezone)


def display_time(value: datetime | None) -> datetime | None:
    """Convert an aware/UTC timestamp for API display without changing storage."""
    if value is None:
        return None
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(DISPLAY_ZONE)


def london_day_bounds_utc(now: datetime | None = None) -> tuple[datetime, datetime]:
    current = now or datetime.now(timezone.utc)
    local = display_time(current)
    assert local is not None
    start_local = datetime.combine(local.date(), time.min, tzinfo=DISPLAY_ZONE)
    end_local = datetime.combine(local.date(), time.max, tzinfo=DISPLAY_ZONE)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)
