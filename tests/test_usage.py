"""Tests for the pure usage helpers."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from custom_components.national_grid_us.usage import (
    bucket_to_hours,
    compute_period_totals,
    daily_totals,
)

TZ = ZoneInfo("America/New_York")


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=TZ)


def test_bucket_to_hours_sums_quarter_hours() -> None:
    """Four 15-minute reads inside one hour become a single hourly bucket."""
    start = _dt("2026-11-16T10:00:00")
    intervals = [
        (start, 0.25),
        (start + timedelta(minutes=15), 0.25),
        (start + timedelta(minutes=30), 0.5),
        (start + timedelta(minutes=45), 0.0),
    ]

    assert bucket_to_hours(intervals, TZ) == {start: 1.0}


def test_bucket_to_hours_splits_across_hours() -> None:
    """Reads in different hours land in different buckets."""
    intervals = [
        (_dt("2026-11-16T10:45:00"), 0.4),
        (_dt("2026-11-16T11:00:00"), 0.6),
    ]

    buckets = bucket_to_hours(intervals, TZ)

    assert buckets[_dt("2026-11-16T10:00:00")] == 0.4
    assert buckets[_dt("2026-11-16T11:00:00")] == 0.6


def test_daily_totals_combines_sources() -> None:
    """Settled daily totals and hourly reads are summed per local day."""
    daily = {date(2026, 11, 13): 10.0}
    hourly = {
        _dt("2026-11-16T10:00:00"): 1.0,
        _dt("2026-11-16T11:00:00"): 2.0,
    }

    totals = daily_totals(daily, hourly, TZ)

    assert totals[date(2026, 11, 13)] == 10.0
    assert totals[date(2026, 11, 16)] == 3.0


def test_period_totals_cover_hour_day_week_month() -> None:
    """Period totals follow calendar boundaries, not rolling windows."""
    now = _dt("2026-11-17T10:30:00")

    daily = {
        date(2026, 10, 5): 100.0,  # last month
        date(2026, 10, 31): 50.0,  # last month
        date(2026, 11, 1): 7.0,  # this month, previous week
        date(2026, 11, 15): 3.0,  # this month, sunday of previous week
        date(2026, 11, 16): 4.0,  # this week (monday)
    }
    hourly = {
        _dt("2026-11-17T09:00:00"): 1.0,
        _dt("2026-11-17T10:00:00"): 0.5,
    }

    totals = compute_period_totals(daily, hourly, TZ, now)

    assert totals.this_hour == 0.5
    assert totals.today == 1.5  # hourly reads only
    assert totals.yesterday == 4.0
    assert totals.this_week == 5.5  # monday + today
    assert totals.this_month == 15.5
    assert totals.last_month == 150.0
    assert totals.week_start == date(2026, 11, 16)
    assert totals.month_start == date(2026, 11, 1)
    assert totals.last_month_start == date(2026, 10, 1)


def test_period_totals_without_data_are_zero() -> None:
    """A meter with no readings reports zeros rather than failing."""
    totals = compute_period_totals({}, {}, TZ, _dt("2026-11-17T10:30:00"))

    assert totals.today == 0.0
    assert totals.this_week == 0.0
    assert totals.this_month == 0.0
