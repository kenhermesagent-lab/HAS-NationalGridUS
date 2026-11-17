"""Tests for the statistics importer's point selection."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from custom_components.national_grid_us.models import MeterUsage
from custom_components.national_grid_us.statistics import (
    _split_points,
    statistics_id,
)

TZ = ZoneInfo("America/New_York")


def _meter(**kwargs: object) -> MeterUsage:
    return MeterUsage(
        account_id="1234567890",
        service_point="987654321",
        meter_number="M1",
        premise_number="555",
        meter_point_number="1",
        fuel_type="ELECTRIC",
        is_smart_meter=True,
        **kwargs,  # type: ignore[arg-type]
    )


def test_statistics_id_is_lowercase() -> None:
    """Statistic ids must be lowercase and namespaced by the domain."""
    assert (
        statistics_id(_meter())
        == "national_grid_us:1234567890_987654321_electric_usage"
    )
    assert (
        statistics_id(_meter(), returning=True)
        == "national_grid_us:1234567890_987654321_electric_return"
    )


def test_split_points_skips_days_covered_by_intervals() -> None:
    """A settled daily total is dropped when hourly reads already cover the day."""
    meter = _meter(
        daily={date(2026, 11, 13): 10.0, date(2026, 11, 16): 40.0},
        hourly={datetime(2026, 11, 16, 10, 0, tzinfo=TZ): 1.5},
    )

    consumption, returned = _split_points(meter, TZ)

    assert consumption == {
        datetime(2026, 11, 13, tzinfo=TZ): 10.0,
        datetime(2026, 11, 16, 10, 0, tzinfo=TZ): 1.5,
    }
    assert returned == {}


def test_split_points_separates_export() -> None:
    """Negative readings go to the return-to-grid series as positive values."""
    meter = _meter(
        daily={date(2026, 11, 12): 5.0, date(2026, 11, 13): -2.0},
    )

    consumption, returned = _split_points(meter, TZ)

    assert consumption == {datetime(2026, 11, 12, tzinfo=TZ): 5.0}
    assert returned == {datetime(2026, 11, 13, tzinfo=TZ): 2.0}
