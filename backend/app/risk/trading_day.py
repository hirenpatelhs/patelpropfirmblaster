from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def trading_date(now: datetime, timezone_name: str) -> date:
    """Return the profile-local trading date used to scope daily locks and stats."""
    aware = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown daily reset timezone: {timezone_name}") from exc
    return aware.astimezone(zone).date()
