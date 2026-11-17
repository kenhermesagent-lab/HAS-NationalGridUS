"""Coordinator and sensor tests using a stubbed National Grid client."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from custom_components.national_grid_us.binary_sensor import (
    NationalGridSmartMeterSensor,
)
from custom_components.national_grid_us.const import (
    MONTHLY_HISTORY_MONTHS,
    fuel_conversion,
)
from custom_components.national_grid_us.coordinator import NationalGridCoordinator
from custom_components.national_grid_us.models import NationalGridData
from custom_components.national_grid_us.sensor import NationalGridUsageSensor

TZ = ZoneInfo("America/New_York")


class FakeClient:
    """Minimal stand-in for py_nationalgrid.NationalGridClient."""

    async def get_bills(self, account_number: str) -> list[dict[str, Any]]:
        return []

    async def get_energy_usage_costs(
        self, account_number: str, query_date: Any, company_code: str, **kwargs: Any
    ) -> list[dict[str, Any]]:
        return []

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


class FakeMonthlyClient(FakeClient):
    """A gas meter whose usage only exists as account-level monthly totals."""

    def __init__(self, meter_count: int = 1) -> None:
        self._meter_count = meter_count
        self.monthly_request: tuple[str, int, int] | None = None

    async def get_billing_account(self, account_number: str) -> dict[str, Any]:
        return {
            "premiseNumber": 555000111,
            "meter": {
                "nodes": [
                    {
                        "meterNumber": f"M-{index}",
                        "servicePointNumber": 888 + index,
                        "meterPointNumber": index,
                        "fuelType": "GAS",
                        "isSmartMeter": False,
                        "hasAmiSmartMeter": False,
                    }
                    for index in range(self._meter_count)
                ]
            },
        }

    async def get_ami_energy_usages(self, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    async def get_energy_usages(
        self, account_number: str, from_month: int, first: int = 12, **kwargs: Any
    ) -> list[dict[str, Any]]:
        self.monthly_request = (account_number, from_month, first)
        return [
            {"usage": 12.5, "usageType": "ACTUAL", "usageYearMonth": 202610},
            {"usage": 3.5, "usageType": "ACTUAL", "usageYearMonth": 202611},
            {"usage": None, "usageType": "ESTIMATED", "usageYearMonth": 202609},
            {"usage": 99.0, "usageType": "ACTUAL", "usageYearMonth": "2026-13"},
        ]


class FakeBilledClient(FakeMonthlyClient):
    """A gas meter whose cost only exists as billed statements."""

    async def get_bills(self, account_number: str) -> list[dict[str, Any]]:
        return [
            {
                "statementDate": "2026-11-05T00:00:00-05:00",
                "currentChargesAmount": 41.5,
                "status": "UNPAID",
            },
            {
                "statementDate": "2026-10-05T00:00:00-05:00",
                "currentChargesAmount": 33.25,
                "status": "PAID",
            },
            {"statementDate": None, "currentChargesAmount": 10.0},
        ]


class FakeCostEndpointClient(FakeBilledClient):
    """A gas meter whose cost endpoint answers for one company code."""

    async def get_energy_usage_costs(
        self, account_number: str, query_date: Any, company_code: str, **kwargs: Any
    ) -> list[dict[str, Any]]:
        if company_code != "KEDLI":
            return []
        return [
            {"date": "2026-11-10", "fuelType": "GAS", "amount": 5.5, "month": 202611},
            {"date": "2026-11-11", "fuelType": "GAS", "amount": 1.5, "month": 202611},
            {
                "date": "2026-11-12",
                "fuelType": "ELECTRIC",
                "amount": 99.0,
                "month": 202611,
            },
        ]


class FakeOpenMonthClient(FakeMonthlyClient):
    """A gas meter whose current month has no statement yet."""

    def __init__(self, meter_count: int = 1) -> None:
        super().__init__(meter_count)
        self.codes: set[str] = {"KEDLI"}

    async def get_bills(self, account_number: str) -> list[dict[str, Any]]:
        return [
            {
                "statementDate": "2026-10-05T00:00:00-05:00",
                "currentChargesAmount": 33.25,
                "status": "PAID",
            }
        ]

    async def get_energy_usage_costs(
        self, account_number: str, query_date: Any, company_code: str, **kwargs: Any
    ) -> list[dict[str, Any]]:
        if company_code not in self.codes:
            return []
        return [
            {"date": "2026-11-10", "fuelType": "GAS", "amount": 5.5, "month": 202611},
            {"date": "2026-11-11", "fuelType": "GAS", "amount": 1.5, "month": 202611},
        ]


class FakeLateCodeClient(FakeOpenMonthClient):
    """A gas meter whose cost endpoint starts answering only later."""

    def __init__(self, meter_count: int = 1) -> None:
        super().__init__(meter_count)
        self.codes = set()


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
    coordinator._company_codes = {}
    coordinator._cost_warned = set()

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
    assert meter.unit in {"CCF", "therm"}
    scale = fuel_conversion("GAS")
    assert meter.daily == {
        date(2026, 11, 15): 0.4 * scale,
        date(2026, 11, 16): 0.2 * scale,
    }
    assert meter.hourly == {}


async def test_monthly_usage_fills_in_for_meter_without_ami_readings() -> None:
    """A non-AMI gas meter reports its CCF from account monthly totals."""
    client = FakeMonthlyClient()
    coordinator = _build_coordinator(client)

    data = await coordinator._async_update_data()

    meter = data.meters["1234567890_888"]
    assert meter.daily == {}
    assert meter.hourly == {}
    assert meter.ami_rows == 0
    assert meter.monthly_rows == 4
    # Junk months and rows without a usage value are dropped.
    scale = fuel_conversion("GAS")
    assert meter.monthly == {
        date(2026, 10, 1): 12.5 * scale,
        date(2026, 11, 1): 3.5 * scale,
    }
    assert client.monthly_request is not None
    assert client.monthly_request[0] == "1234567890"
    assert client.monthly_request[2] == MONTHLY_HISTORY_MONTHS


async def test_monthly_usage_is_not_spread_over_several_meters() -> None:
    """Account-level totals are not attributed when the account has two meters."""
    coordinator = _build_coordinator(FakeMonthlyClient(meter_count=2))

    data = await coordinator._async_update_data()

    assert len(data.meters) == 2
    assert all(meter.monthly == {} for meter in data.meters.values())


async def test_cost_comes_from_bills_for_a_single_meter_account() -> None:
    """Billed statements become the meter's monthly cost."""
    coordinator = _build_coordinator(FakeBilledClient())

    data = await coordinator._async_update_data()

    meter = data.meters["1234567890_888"]
    assert meter.monthly_cost == {date(2026, 11, 1): 41.5, date(2026, 10, 1): 33.25}
    assert meter.cost_source == {
        date(2026, 11, 1): "billed",
        date(2026, 10, 1): "billed",
    }
    assert meter.cost_rows == 3


async def test_a_statement_beats_the_energy_endpoint() -> None:
    """A billed total wins over the energy-only figure for the same month."""
    coordinator = _build_coordinator(FakeCostEndpointClient())

    data = await coordinator._async_update_data()

    meter = data.meters["1234567890_888"]
    assert meter.monthly_cost == {date(2026, 10, 1): 33.25, date(2026, 11, 1): 41.5}
    assert meter.cost_source == {
        date(2026, 10, 1): "billed",
        date(2026, 11, 1): "billed",
    }


async def test_energy_cost_fills_a_month_without_a_statement() -> None:
    """An open month is energy-only, and says so."""
    coordinator = _build_coordinator(FakeOpenMonthClient())

    data = await coordinator._async_update_data()

    meter = data.meters["1234567890_888"]
    assert meter.monthly_cost == {date(2026, 10, 1): 33.25, date(2026, 11, 1): 7.0}
    assert meter.cost_source == {
        date(2026, 10, 1): "billed",
        date(2026, 11, 1): "energy",
    }


async def test_company_code_is_retried_after_a_failed_probe() -> None:
    """A probe that found nothing must not be cached forever."""
    client = FakeLateCodeClient()
    coordinator = _build_coordinator(client)

    first = await coordinator._async_update_data()
    # No code answered, so the open month has no cost yet.
    assert first.meters["1234567890_888"].monthly_cost == {date(2026, 10, 1): 33.25}

    client.codes = {"KEDLI"}
    second = await coordinator._async_update_data()

    meter = second.meters["1234567890_888"]
    assert meter.monthly_cost == {date(2026, 10, 1): 33.25, date(2026, 11, 1): 7.0}
    assert meter.cost_source[date(2026, 11, 1)] == "energy"


async def test_billed_cost_is_not_attributed_to_a_multi_meter_account() -> None:
    """Bill totals carry no fuel split, so two meters get no bill cost."""
    coordinator = _build_coordinator(FakeBilledClient(meter_count=2))

    data = await coordinator._async_update_data()

    assert all(meter.monthly_cost == {} for meter in data.meters.values())
    assert all(meter.cost_source == {} for meter in data.meters.values())


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
