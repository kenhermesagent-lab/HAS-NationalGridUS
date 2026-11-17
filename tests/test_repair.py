"""Tests for the repair issue raised when National Grid rejects the sign-in."""

from __future__ import annotations

from homeassistant.components.recorder import Recorder
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from py_nationalgrid import InvalidAuthError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.national_grid_us.const import (
    CONF_SELECTED_ACCOUNTS,
    DOMAIN,
    SETUP_ERRORS_KEY,
)
from tests.fake_client import ACCOUNT_ID, FakeNationalGridClient

ENTRY_DATA = {
    CONF_USERNAME: "user@example.com",
    CONF_PASSWORD: "secret",
    CONF_SELECTED_ACCOUNTS: [ACCOUNT_ID],
}


def _issue(hass: HomeAssistant, entry: MockConfigEntry) -> ir.IssueEntry | None:
    """Return the sign-in repair issue for an entry, if it exists."""
    return ir.async_get(hass).async_get_issue(DOMAIN, f"auth_failed_{entry.entry_id}")


async def test_auth_failure_creates_repair_issue(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    fake_client: FakeNationalGridClient,
    enable_custom_integrations: None,
) -> None:
    """A rejected login leaves a repair issue pointing at diagnostics."""
    fake_client.fail_with = InvalidAuthError("bad credentials")

    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="user@example.com", data=ENTRY_DATA
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id) is False
    await hass.async_block_till_done()

    issue = _issue(hass, entry)
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.ERROR
    assert issue.translation_key == "auth_failed"
    assert issue.is_fixable is False
    assert issue.translation_placeholders
    # The entry title holds the login email and must not be copied into Repairs.
    assert "user@example.com" not in issue.translation_placeholders["account"]


async def test_auth_failure_keeps_setup_error_for_diagnostics(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    fake_client: FakeNationalGridClient,
    enable_custom_integrations: None,
) -> None:
    """The failure that raised the issue is recorded for the diagnostics dump."""
    fake_client.fail_with = InvalidAuthError("bad credentials")

    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="user@example.com", data=ENTRY_DATA
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id) is False
    await hass.async_block_till_done()

    setup_errors = hass.data[SETUP_ERRORS_KEY]
    assert setup_errors[entry.entry_id]["type"] == "ConfigEntryAuthFailed"


async def test_successful_setup_clears_repair_issue(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    fake_client: FakeNationalGridClient,
    enable_custom_integrations: None,
) -> None:
    """Re-authenticating removes the repair issue."""
    fake_client.fail_with = InvalidAuthError("bad credentials")

    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="user@example.com", data=ENTRY_DATA
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id) is False
    await hass.async_block_till_done()
    assert _issue(hass, entry) is not None

    fake_client.fail_with = None
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state.name == "LOADED"
    assert _issue(hass, entry) is None


async def test_unload_clears_repair_issue(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    fake_client: FakeNationalGridClient,
    enable_custom_integrations: None,
) -> None:
    """Unloading the entry drops a stale sign-in issue."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="user@example.com", data=ENTRY_DATA
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    ir.async_create_issue(
        hass,
        DOMAIN,
        f"auth_failed_{entry.entry_id}",
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key="auth_failed",
    )
    assert _issue(hass, entry) is not None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert _issue(hass, entry) is None
