from datetime import datetime, timezone

from app.core.time import display_time, london_day_bounds_utc


def test_london_display_uses_bst_in_summer_and_gmt_in_winter() -> None:
    summer = display_time(datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc))
    winter = display_time(datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc))
    assert summer is not None and summer.isoformat() == "2026-08-15T13:00:00+01:00"
    assert winter is not None and winter.isoformat() == "2026-01-15T12:00:00+00:00"


def test_london_day_bounds_are_returned_in_utc() -> None:
    start, end = london_day_bounds_utc(datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc))
    assert start.isoformat() == "2026-08-14T23:00:00+00:00"
    assert end.isoformat().startswith("2026-08-15T22:59:59.999999")
