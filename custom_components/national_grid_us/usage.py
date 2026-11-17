"""Pure helpers that turn National Grid readings into usage totals.

Everything in this module is free of Home Assistant imports so the maths can be
unit tested in isolation.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo


def local_hour_start(value: datetime, tz: tzinfo) -> datetime:
    """Return the start of the local hour containing ``value``."""
    local = value.astimezone(tz)
    return local.replace(minute=0, second=0, microsecond=0)


def local_midnight(day: date, tz: tzinfo) -> datetime:
    """Return midnight at the start of ``day`` in ``tz``."""
    return datetime(day.year, day.month, day.day, tzinfo=tz)


def bucket_to_hours(
    intervals: Iterable[tuple[datetime, float]], tz: tzinfo
) -> dict[datetime, float]:
    """Sum interval readings into local hour buckets.

    ``intervals`` is an iterable of ``(interval_start, value)`` pairs, normally
    15-minute meter reads.
    """
    buckets: dict[datetime, float] = {}
    for start, value in intervals:
        key = local_hour_start(start, tz)
        buckets[key] = buckets.get(key, 0.0) + float(value)
    return buckets


def daily_totals(
    daily: Mapping[date, float],
    hourly: Mapping[datetime, float],
    tz: tzinfo,
) -> dict[date, float]:
    """Combine settled daily totals and intraday hourly buckets per local day.

    A day covered by hourly reads is taken from the hourly data alone, matching
    what the statistics import does, so a settled daily total is never added on
    top of the same day's 15-minute reads.
    """
    covered = {start.astimezone(tz).date() for start in hourly}
    totals = {day: float(value) for day, value in daily.items() if day not in covered}
    for start, value in hourly.items():
        day = start.astimezone(tz).date()
        totals[day] = totals.get(day, 0.0) + float(value)
    return totals


def sum_range(totals: Mapping[date, float], start: date, end: date) -> float:
    """Sum every day between ``start`` and ``end`` inclusive."""
    if end < start:
        return 0.0
    return sum(value for day, value in totals.items() if start <= day <= end)


def _period_sum(
    totals: Mapping[date, float],
    monthly: Mapping[date, float] | None,
    start: date,
    end: date,
) -> float:
    """Return a day-level sum, or the month's single figure when there is none.

    Meters without AMI readings only get one number per calendar month, keyed at
    the first of that month, so fall back to it only for whole-month periods.
    """
    if any(start <= day <= end for day in totals):
        return sum_range(totals, start, end)
    if monthly is None or start.day != 1:
        return 0.0
    return float(monthly.get(start, 0.0))


@dataclass(slots=True, frozen=True)
class PeriodTotals:
    """Usage totals for the periods the integration exposes as sensors."""

    this_hour: float
    today: float
    yesterday: float
    this_week: float
    this_month: float
    last_month: float
    week_start: date
    month_start: date
    last_month_start: date
    yesterday_date: date


def compute_period_totals(
    daily: Mapping[date, float],
    hourly: Mapping[datetime, float],
    tz: tzinfo,
    now: datetime,
    monthly: Mapping[date, float] | None = None,
) -> PeriodTotals:
    """Compute hourly/daily/weekly/monthly totals for ``now``.

    ``monthly`` holds one total per calendar month for meters that only publish
    a monthly figure, and is used for the month sensors when there are no daily
    readings in the period.
    """
    totals = daily_totals(daily, hourly, tz)
    today = now.astimezone(tz).date()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)
    last_month_end = month_start - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    return PeriodTotals(
        this_hour=hourly.get(local_hour_start(now, tz), 0.0),
        today=totals.get(today, 0.0),
        yesterday=totals.get(today - timedelta(days=1), 0.0),
        this_week=sum_range(totals, week_start, today),
        this_month=_period_sum(totals, monthly, month_start, today),
        last_month=_period_sum(totals, monthly, last_month_start, last_month_end),
        week_start=week_start,
        month_start=month_start,
        last_month_start=last_month_start,
        yesterday_date=today - timedelta(days=1),
    )
