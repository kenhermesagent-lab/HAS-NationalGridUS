"""Tests for the diagnostics download."""

from __future__ import annotations

import json

from homeassistant.components.recorder import Recorder
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from py_nationalgrid import CannotConnectError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.national_grid_us.const import CONF_SELECTED_ACCOUNTS, DOMAIN
from custom_components.national_grid_us.diagnostics import (
    async_get_config_entry_diagnostics,
)
from tests.fake_client import ACCOUNT_ID, FakeNationalGridClient

ENTRY_DATA = {
    CONF_USERNAME: "user@example.com",
    CONF_PASSWORD: "secret",
    CONF_SELECTED_ACCOUNTS: [ACCOUNT_ID],
}


async def test_diagnostics_are_redacted(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    fake_client: FakeNationalGridClient,
    enable_custom_integrations: None,
) -> None:
    """Diagnostics describe the meter without leaking credentials or ids."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="user@example.com", data=ENTRY_DATA
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["entry"]["state"] == "LOADED"
    assert diagnostics["setup_error"] is None
    assert diagnostics["coordinator"]["meter_count"] == 1

    meter = diagnostics["meters"][0]
    assert meter["fuel_type"] == "ELECTRIC"
    assert meter["is_smart_meter"] is True
    assert meter["daily_points"] == 2
    assert meter["daily_total"] == 15.0
    assert meter["account"].startswith("id:")
    assert meter["statistic_ids"][0].startswith("national_grid_us:id:")

    dumped = json.dumps(diagnostics)
    assert "user@example.com" not in dumped
    assert "secret" not in dumped
    assert ACCOUNT_ID not in dumped


async def test_diagnostics_explain_setup_failure(
    hass: HomeAssistant,
    fake_client: FakeNationalGridClient,
    enable_custom_integrations: None,
) -> None:
    """A failed setup still produces diagnostics that say why it failed."""
    fake_client.fail_with = CannotConnectError("offline")

    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="user@example.com", data=ENTRY_DATA
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id) is False

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["coordinator"] is None
    assert diagnostics["meters"] == []
    assert diagnostics["entry"]["state"] == "SETUP_RETRY"
    assert diagnostics["setup_error"]["type"] == "ConfigEntryNotReady"
    assert "offline" in diagnostics["setup_error"]["message"]
