"""Tests for setting up the National Grid US integration in Home Assistant."""

from __future__ import annotations

import pytest
from homeassistant.components.recorder import Recorder
from homeassistant.components.recorder.statistics import get_last_statistics
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from py_nationalgrid import CannotConnectError, GraphQLError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.national_grid_us.const import CONF_SELECTED_ACCOUNTS, DOMAIN
from tests.fake_client import ACCOUNT_ID, SERVICE_POINT, FakeNationalGridClient

ENTRY_DATA = {
    CONF_USERNAME: "user@example.com",
    CONF_PASSWORD: "secret",
    CONF_SELECTED_ACCOUNTS: [ACCOUNT_ID],
}
METER_KEY = f"{ACCOUNT_ID}_{SERVICE_POINT}"
USAGE_STAT_ID = f"{DOMAIN}:{METER_KEY}_electric_usage"


def _sensor_id(hass: HomeAssistant, metric: str) -> str | None:
    """Return the entity id of a usage sensor by unique id."""
    return er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{DOMAIN}_{METER_KEY}_electric_{metric}"
    )


async def _setup(hass: HomeAssistant) -> tuple[MockConfigEntry, bool]:
    """Add the entry and run the integration's setup path."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="user@example.com", data=ENTRY_DATA
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, result


async def test_setup_entry_loads_and_creates_entities(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    fake_client: FakeNationalGridClient,
    enable_custom_integrations: None,
) -> None:
    """A successful setup loads the entry and creates the meter's entities."""
    entry, result = await _setup(hass)

    assert result is True
    assert entry.state is ConfigEntryState.LOADED
    assert fake_client.calls == [
        "get_linked_accounts",
        "get_billing_account",
        "get_ami_energy_usages",
        "get_interval_reads",
    ]

    assert len(hass.states.async_entity_ids("sensor")) == 6
    assert len(hass.states.async_entity_ids("binary_sensor")) == 1
    assert len(hass.states.async_entity_ids("button")) == 1

    today_id = _sensor_id(hass, "today")
    yesterday_id = _sensor_id(hass, "yesterday")
    hour_id = _sensor_id(hass, "this_hour")
    assert today_id and yesterday_id and hour_id

    today = hass.states.get(today_id)
    assert today is not None
    assert float(today.state) == pytest.approx(1.0)  # four 15-minute reads
    assert today.attributes["unit_of_measurement"] == "kWh"
    assert today.attributes["device_class"] == "energy"
    assert today.attributes["state_class"] == "total"

    yesterday = hass.states.get(yesterday_id)
    assert yesterday is not None
    assert float(yesterday.state) == pytest.approx(10.0)

    hour = hass.states.get(hour_id)
    assert hour is not None
    assert float(hour.state) == pytest.approx(1.0)  # current-hour bucket


async def test_setup_creates_meter_device(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    fake_client: FakeNationalGridClient,
    enable_custom_integrations: None,
) -> None:
    """Sensors are grouped under a device holding the National Grid meter ids."""
    await _setup(hass)

    device = dr.async_get(hass).async_get_device({(DOMAIN, METER_KEY)})
    assert device is not None
    assert device.manufacturer == "National Grid"
    assert device.name == f"Electric meter {SERVICE_POINT}"
    assert device.serial_number == "M-1"
    assert device.model == "AMI smart meter"


async def test_setup_imports_long_term_statistics(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    fake_client: FakeNationalGridClient,
    enable_custom_integrations: None,
) -> None:
    """Settled daily usage and intraday reads land in one cumulative series."""
    await _setup(hass)
    await recorder_mock.async_block_till_done()

    stats = get_last_statistics(hass, 1, USAGE_STAT_ID, False, {"sum"})

    rows = stats.get(USAGE_STAT_ID)
    assert rows, f"no statistics written for {USAGE_STAT_ID}"
    # 5 kWh two days ago + 10 kWh yesterday + 1 kWh of today's interval reads
    assert rows[0]["sum"] == pytest.approx(16.0)


async def test_setup_retries_when_national_grid_is_unreachable(
    hass: HomeAssistant,
    fake_client: FakeNationalGridClient,
    enable_custom_integrations: None,
) -> None:
    """A connection failure leaves the entry in retry rather than erroring out."""
    fake_client.fail_with = CannotConnectError("offline")

    entry, result = await _setup(hass)

    assert result is False
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_starts_reauth_when_credentials_are_rejected(
    hass: HomeAssistant,
    fake_client: FakeNationalGridClient,
    enable_custom_integrations: None,
) -> None:
    """A 401 during setup marks the entry as needing re-authentication."""
    fake_client.fail_with = GraphQLError("unauthorized", endpoint="x", status=401)

    entry, result = await _setup(hass)

    assert result is False
    assert entry.state is ConfigEntryState.SETUP_ERROR


async def test_unload_entry(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    fake_client: FakeNationalGridClient,
    enable_custom_integrations: None,
) -> None:
    """Unloading removes the entities and closes the client."""
    entry, result = await _setup(hass)
    assert result is True

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    assert hass.data.get(DOMAIN) == {}
    assert fake_client.closed is True

    # Home Assistant keeps the entity registry entries and marks the states
    # unavailable until the next restart.
    sensor_ids = hass.states.async_entity_ids("sensor")
    assert sensor_ids
    assert all(
        hass.states.get(entity_id).state == STATE_UNAVAILABLE
        for entity_id in sensor_ids
    )


async def test_force_refresh_button_refetches(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    fake_client: FakeNationalGridClient,
    enable_custom_integrations: None,
) -> None:
    """Pressing the refresh button re-runs the coordinator update."""
    await _setup(hass)
    calls_before = len(fake_client.calls)

    entity_id = er.async_get(hass).async_get_entity_id(
        "button", DOMAIN, f"{DOMAIN}_{METER_KEY}_electric_refresh"
    )
    assert entity_id is not None

    await hass.services.async_call(
        "button", "press", {"entity_id": entity_id}, blocking=True
    )
    await hass.async_block_till_done()

    assert len(fake_client.calls) > calls_before
