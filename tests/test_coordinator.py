"""Coordinator and sensor tests using a stubbed National Grid client."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from custom_components.national_grid_us.binary_sensor import (
    NationalGridSmartMeterSensor,
)
from custom_components.national_grid_us.coordinator import NationalGridCoordinator
from custom_components.national_grid_us.models import NationalGridData
from custom_components.national_grid_us.sensor import NationalGridUsageSensor

TZ = ZoneInfo("America/New_York")


class FakeClient:
    """Minimal stand-in for py_nationalgrid.NationalGridClient."""

    async def get_linked_accounts(self) -> list[dict[str, Any]]:
        return [
            {"billingAccountId": "1234567890", "billingAccount": {}},
            {"billingAccountId": "999", "billingAccount": {}},
        ]

    async def get_billing_account(self, account_number: str) -> dict[str, Any]:
        return {
            "premiseNumber": 555000111,
            "region": "NY",
            "meter": {
                "nodes": [
                    {
                        "meterNumber": "M-1",
                        "servicePointNumber": 777,
                        "meterPointNumber": 1,
                        "fuelType": "ELECTRIC",
                        "isSmartMeter": True,
                        "hasAmiSmartMeter": True,
                    }
                ]
            },
        }

    async def get_ami_energy_usages(self, **kwargs: Any) -> list[dict[str, Any]]:
        return [
            {"date": "2026-11-15", "fuelType": "ELECTRIC", "quantity": 12.5},
            {
                "date": "2026-11-16T00:00:00-05:00",
                "fuelType": "ELECTRIC",
                "quantity": 3.0,
            },
            {"date": "not-a-date", "fuelType": "ELECTRIC", "quantity": 99.0},
        ]

    async def get_interval_reads(self, **kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "startTime": "2026-11-17T09:00:00-05:00",
                "endTime": "2026-11-17T09:15:00-05:00",
                "value": 0.25,
            },
            {
                "startTime": "2026-11-17T09:15:00-05:00",
                "endTime": "2026-11-17T09:30:00-05:00",
                "value": 0.25,
            },
            {
                "startTime": "2026-11-17T09:30:00-05:00",
                "endTime": "2026-11-17T09:45:00-05:00",
                "value": 0.5,
            },
        ]


class FakeGasClient(FakeClient):
    """A gas meter that the account API does not flag as a smart meter."""

    async def get_billing_account(self, account_number: str) -> dict[str, Any]:
        return {
            "premiseNumber": 555000111,
            "meter": {
                "nodes": [
                    {
                        "meterNumber": "M-2",
                        "servicePointNumber": 888,
                        "meterPointNumber": 2,
                        "fuelType": "GAS",
                        "isSmartMeter": False,
                        "hasAmiSmartMeter": False,
                    }
                ]
            },
        }

    async def get_ami_energy_usages(self, **kwargs: Any) -> list[dict[str, Any]]:
        return [
            {"date": "2026-11-15", "fuelType": "GAS", "quantity": 0.4},
            {"date": "2026-11-16", "fuelType": "GAS", "quantity": 0.2},
        ]

    async def get_interval_reads(self, **kwargs: Any) -> list[dict[str, Any]]:
        raise AssertionError("gas meters have no interval reads")


class FakeCoordinator:
    """Coordinator stub for entity property tests."""

    last_update_success = True

    def __init__(self, data: NationalGridData) -> None:
        self.data = data


def _build_coordinator(
    client: Any | None = None,
) -> NationalGridCoordinator:
    coordinator = object.__new__(NationalGridCoordinator)
    coordinator.client = client or FakeClient()  # type: ignore[assignment]
    coordinator.selected_accounts = {"1234567890"}
    coordinator._stats_warned = set()
    coordinator._usage_warned = set()

    async def _noop_import(data: NationalGridData) -> None:
        return None

    coordinator._async_import_statistics = _noop_import  # type: ignore[method-assign]
    return coordinator


async def test_update_data_parses_usage() -> None:
    """AMI and interval payloads are parsed into daily and hourly usage."""
    coordinator = _build_coordinator()

    data = await coordinator._async_update_data()

    assert list(data.meters) == ["1234567890_777"]
    meter = data.meters["1234567890_777"]
    assert meter.fuel_type == "ELECTRIC"
    assert meter.is_smart_meter is True
    assert meter.premise_number == "555000111"
    assert meter.unit == "kWh"

    # Both plain and timestamped AMI dates are accepted; junk is ignored.
    assert meter.daily == {date(2026, 11, 15): 12.5, date(2026, 11, 16): 3.0}

    # Three 15-minute reads in the 09:00 hour become one bucket.
    assert meter.hourly == {datetime(2026, 11, 17, 9, 0, tzinfo=TZ): 1.0}


async def test_update_data_fetches_usage_for_meter_without_smart_flag() -> None:
    """A gas meter the API does not flag as smart still reports daily usage."""
    coordinator = _build_coordinator(FakeGasClient())

    data = await coordinator._async_update_data()

    meter = data.meters["1234567890_888"]
    assert meter.is_smart_meter is False
    assert meter.unit == "CCF"
    assert meter.daily == {date(2026, 11, 15): 0.4, date(2026, 11, 16): 0.2}
    assert meter.hourly == {}


async def test_binary_sensor_follows_available_readings() -> None:
    """The diagnostic sensor reports received readings, not the API flag."""
    data = await _build_coordinator(FakeGasClient())._async_update_data()
    sensor = NationalGridSmartMeterSensor(
        FakeCoordinator(data),
        "1234567890_888",  # type: ignore[arg-type]
    )

    assert sensor.is_on is True


async def test_sensor_values_and_periods() -> None:
    """Sensors read the right period from the coordinator payload."""
    coordinator = _build_coordinator()
    data = await coordinator._async_update_data()
    stub = FakeCoordinator(data)

    today_sensor = NationalGridUsageSensor(stub, "1234567890_777", "today")  # type: ignore[arg-type]
    hour_sensor = NationalGridUsageSensor(stub, "1234567890_777", "this_hour")  # type: ignore[arg-type]

    assert today_sensor.unique_id == "national_grid_us_1234567890_777_electric_today"
    assert today_sensor.native_unit_of_measurement == "kWh"
    assert today_sensor.device_class.value == "energy"
    assert today_sensor.native_value is not None
    assert hour_sensor.native_value is not None
    assert hour_sensor.last_reset is not None
    assert hour_sensor.last_reset.minute == 0
