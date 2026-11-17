"""Import National Grid usage into Home Assistant long-term statistics.

Home Assistant stores long term statistics in hourly buckets; the dashboard then
aggregates those buckets into daily, weekly, monthly and yearly views. This
module appends new hourly (or, for settled historical days, one-per-day) points
to a cumulative series per meter so the Energy dashboard can use it directly.
"""

from __future__ import annotations

import inspect
import logging
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any, cast

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder import models as recorder_models
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .const import COST_SOURCE_BILLED, CURRENCY_USD, DOMAIN, UTILITY_TIME_ZONE
from .models import MeterUsage
from .usage import local_midnight

_LOGGER = logging.getLogger(__name__)


def statistics_id(
    meter: MeterUsage, *, returning: bool = False, cost: bool = False
) -> str:
    """Return the statistics id for a meter's usage, return or cost series."""
    if cost:
        suffix = "cost"
    else:
        suffix = "return" if returning else "usage"
    return (
        f"{DOMAIN}:"
        f"{meter.account_id}_{meter.service_point}_{meter.fuel_type.lower()}_{suffix}"
    )


def _build_metadata(
    statistic_id: str, name: str, unit: str, unit_class: str | None
) -> StatisticMetaData:
    """Build statistics metadata that works across Home Assistant versions.

    ``unit_class``/``mean_type`` replaced ``has_mean``/``unit_of_measurement``
    in the 2025.10 recorder API, so only send the keys the running version
    knows about.
    """
    metadata: dict[str, Any] = {
        "has_sum": True,
        "name": name,
        "source": DOMAIN,
        "statistic_id": statistic_id,
        "unit_of_measurement": unit,
    }

    annotations = getattr(StatisticMetaData, "__annotations__", {})
    if "has_mean" in annotations:
        metadata["has_mean"] = False

    mean_type = getattr(recorder_models, "StatisticMeanType", None)
    if mean_type is not None:
        metadata["mean_type"] = mean_type.NONE

    if unit_class is not None and "unit_class" in annotations:
        metadata["unit_class"] = unit_class

    return cast(StatisticMetaData, metadata)


async def _async_last_sum(
    hass: HomeAssistant, statistic_id: str
) -> tuple[float, datetime | None]:
    """Return the last stored cumulative sum and its start for a statistic."""
    try:
        # ``get_last_statistics`` reads the database synchronously, so it has to
        # run on the recorder's executor instead of the event loop.
        result = await get_instance(hass).async_add_executor_job(
            get_last_statistics, hass, 1, statistic_id, False, {"sum"}
        )
        if inspect.isawaitable(result):
            result = await result
    except (HomeAssistantError, KeyError) as err:
        _LOGGER.debug("No previous statistics for %s: %s", statistic_id, err)
        return 0.0, None

    rows = result.get(statistic_id)
    if not rows:
        return 0.0, None

    row = rows[0]
    last_sum = row.get("sum")
    start = row.get("start")
    if last_sum is None or start is None:
        return 0.0, None

    return float(last_sum), dt_util.utc_from_timestamp(start)


def _split_points(
    meter: MeterUsage, tz: tzinfo
) -> tuple[dict[datetime, float], dict[datetime, float]]:
    """Return ``(consumption, return)`` points for a meter.

    Days that already have 15-minute interval coverage are taken from the
    interval data only, so a settled daily total never double counts the same
    day of intraday reads.
    """
    interval_days = {start.astimezone(tz).date() for start in meter.hourly}

    raw: dict[datetime, float] = {}
    for day, value in meter.daily.items():
        if day in interval_days:
            continue
        raw[local_midnight(day, tz)] = value
    for start, value in meter.hourly.items():
        raw[start] = value

    # Meters without AMI readings report one figure per calendar month, imported
    # at the start of that month unless finer-grained readings already cover it.
    covered_days = interval_days | set(meter.daily)
    for month, value in meter.monthly.items():
        month_end = (month + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        if any(month <= day <= month_end for day in covered_days):
            continue
        raw[local_midnight(month, tz)] = value

    consumption: dict[datetime, float] = {}
    returned: dict[datetime, float] = {}
    for start, value in raw.items():
        if value < 0:
            returned[start] = abs(value)
        else:
            consumption[start] = value
    return consumption, returned


async def _async_import_series(
    hass: HomeAssistant,
    statistic_id: str,
    name: str,
    unit: str,
    unit_class: str | None,
    points: dict[datetime, float],
) -> None:
    """Append new points to a cumulative statistics series."""
    if not points:
        return

    last_sum, last_start = await _async_last_sum(hass, statistic_id)

    statistics: list[StatisticData] = []
    running = last_sum
    for start in sorted(points):
        start_utc = start.astimezone(UTC)
        if last_start is not None and start_utc <= last_start:
            continue
        value = round(float(points[start]), 4)
        running = round(running + value, 4)
        statistics.append(StatisticData(start=start_utc, state=value, sum=running))

    if not statistics:
        return

    # External integrations must use async_add_external_statistics: the
    # internal async_import_statistics rejects statistic ids that are not
    # recorder-domain entity ids. Both are plain callbacks today, but await a
    # coroutine if a future version returns one.
    result = async_add_external_statistics(
        hass,
        _build_metadata(statistic_id, name, unit, unit_class),
        statistics,
    )
    if inspect.isawaitable(result):
        await result
    _LOGGER.debug("Imported %s new statistic(s) for %s", len(statistics), statistic_id)


def _cost_points(meter: MeterUsage, tz: tzinfo) -> dict[datetime, float]:
    """Return one billed-cost point per month, keyed at the month's midnight.

    Only whole statements are imported. An energy-only figure covers part of a
    period and excludes delivery and fixed charges, so mixing the two in one
    cumulative series would make the totals meaningless.
    """
    return {
        local_midnight(month, tz): value
        for month, value in meter.monthly_cost.items()
        if meter.cost_source.get(month) == COST_SOURCE_BILLED
    }


async def async_import_meter_statistics(hass: HomeAssistant, meter: MeterUsage) -> None:
    """Import every available reading for ``meter`` into long term statistics."""
    tz = dt_util.get_time_zone(UTILITY_TIME_ZONE)
    if tz is None:  # pragma: no cover - tz database always has this zone
        return

    consumption, returned = _split_points(meter, tz)
    label = f"{meter.account_id} {meter.service_point}".strip()

    await _async_import_series(
        hass,
        statistics_id(meter),
        f"National Grid {meter.fuel_type.title()} Usage {label}",
        meter.unit,
        meter.unit_class,
        consumption,
    )
    await _async_import_series(
        hass,
        statistics_id(meter, returning=True),
        f"National Grid {meter.fuel_type.title()} Return {label}",
        meter.unit,
        meter.unit_class,
        returned,
    )
    await _async_import_series(
        hass,
        statistics_id(meter, cost=True),
        f"National Grid {meter.fuel_type.title()} Billed Cost {label}",
        CURRENCY_USD,
        None,
        _cost_points(meter, tz),
    )
