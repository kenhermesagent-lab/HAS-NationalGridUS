"""Tests for the unit the integration reports gas usage in.

National Grid's API reports gas in therms. Home Assistant gained a native therm
unit in 2026.11, so the integration reports therms there and converts to CCF on
older versions rather than labelling therms as CCF.
"""

from __future__ import annotations

import pytest
from homeassistant.components.sensor import SensorDeviceClass

from custom_components.national_grid_us import const, sensor
from custom_components.national_grid_us.models import MeterUsage, NationalGridData
from custom_components.national_grid_us.sensor import NationalGridUsageSensor


def test_gas_reports_therms_when_home_assistant_knows_the_unit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A therm-capable Home Assistant gets the API numbers unchanged."""
    monkeypatch.setattr(const, "has_therm_unit", lambda: True)

    assert const.fuel_unit("GAS") == "therm"
    assert const.fuel_unit_class("GAS") == "energy"
    assert const.fuel_conversion("GAS") == 1.0


def test_gas_falls_back_to_ccf_without_a_therm_unit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Older versions convert therms to CCF so the label matches the numbers."""
    monkeypatch.setattr(const, "has_therm_unit", lambda: False)

    assert const.fuel_unit("GAS") == "CCF"
    assert const.fuel_unit_class("GAS") == "volume"
    assert const.fuel_conversion("GAS") == pytest.approx(1 / const.THERM_PER_CCF)


def test_gas_sensor_device_class_matches_the_reported_unit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Therms are an energy unit; CCF belongs to the gas device class."""
    meter = MeterUsage(
        account_id="1234567890",
        service_point="82201612",
        meter_number="06472784",
        premise_number="555000111",
        meter_point_number="1",
        fuel_type="GAS",
        is_smart_meter=False,
        monthly_cost={},
    )

    class Stub:
        last_update_success = True

        def __init__(self) -> None:
            self.data = NationalGridData(meters={meter.key: meter})

    entity = NationalGridUsageSensor(Stub(), meter.key, "this_month")  # type: ignore[arg-type]

    monkeypatch.setattr(sensor, "has_therm_unit", lambda: True)
    assert entity.device_class is SensorDeviceClass.ENERGY

    monkeypatch.setattr(sensor, "has_therm_unit", lambda: False)
    assert entity.device_class is SensorDeviceClass.GAS


def test_electric_is_always_kwh(monkeypatch: pytest.MonkeyPatch) -> None:
    """Electricity keeps kWh and no conversion on either version."""
    monkeypatch.setattr(const, "has_therm_unit", lambda: False)

    assert const.fuel_unit("ELECTRIC") == "kWh"
    assert const.fuel_unit_class("ELECTRIC") == "energy"
    assert const.fuel_conversion("ELECTRIC") == 1.0
