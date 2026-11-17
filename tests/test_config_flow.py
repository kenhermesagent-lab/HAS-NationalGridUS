"""Tests for the National Grid US config flow."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from py_nationalgrid import CannotConnectError, GraphQLError, InvalidAuthError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.national_grid_us.const import CONF_SELECTED_ACCOUNTS, DOMAIN
from tests.fake_client import ACCOUNT_ID, FakeNationalGridClient

CREDENTIALS = {CONF_USERNAME: "user@example.com", CONF_PASSWORD: "secret"}


async def _start_user_flow(hass: HomeAssistant) -> dict[str, Any]:
    """Open the integration's user step and return the flow result."""
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_user_flow_creates_entry(
    hass: HomeAssistant,
    fake_client: FakeNationalGridClient,
    enable_custom_integrations: None,
) -> None:
    """A valid login reaches the account picker and creates an entry."""
    result = await _start_user_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], CREDENTIALS
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "select_accounts"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SELECTED_ACCOUNTS: [ACCOUNT_ID]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "National Grid (user@example.com)"
    assert result["data"] == {
        CONF_USERNAME: "user@example.com",
        CONF_PASSWORD: "secret",
        CONF_SELECTED_ACCOUNTS: [ACCOUNT_ID],
    }

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.unique_id == "user@example.com"
    assert fake_client.calls[:2] == ["get_linked_accounts", "get_linked_accounts"]


async def test_user_flow_strips_whitespace_from_username(
    hass: HomeAssistant,
    fake_client: FakeNationalGridClient,
    enable_custom_integrations: None,
) -> None:
    """A pasted email with trailing spaces still logs in and sets the unique id."""
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_USERNAME: "  user@example.com  ", CONF_PASSWORD: "secret"},
    )
    assert result["step_id"] == "select_accounts"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SELECTED_ACCOUNTS: [ACCOUNT_ID]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_USERNAME] == "user@example.com"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (InvalidAuthError("bad credentials"), "invalid_auth"),
        (GraphQLError("unauthorized", endpoint="x", status=401), "invalid_auth"),
        (CannotConnectError("offline"), "cannot_connect"),
        (TimeoutError(), "cannot_connect"),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant,
    fake_client: FakeNationalGridClient,
    error: Exception,
    expected: str,
    enable_custom_integrations: None,
) -> None:
    """Login failures show the matching error on the form."""
    fake_client.fail_with = error

    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], CREDENTIALS
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": expected}
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_user_flow_without_accounts(
    hass: HomeAssistant,
    fake_client: FakeNationalGridClient,
    enable_custom_integrations: None,
) -> None:
    """A login with no linked billing accounts shows a form error."""
    fake_client.no_accounts = True

    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], CREDENTIALS
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_accounts"}


async def test_user_flow_rejects_duplicate_account(
    hass: HomeAssistant,
    fake_client: FakeNationalGridClient,
    enable_custom_integrations: None,
) -> None:
    """The same National Grid login cannot be added twice."""
    MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={**CREDENTIALS, CONF_SELECTED_ACCOUNTS: [ACCOUNT_ID]},
    ).add_to_hass(hass)

    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], CREDENTIALS
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow_updates_accounts(
    hass: HomeAssistant,
    fake_client: FakeNationalGridClient,
    enable_custom_integrations: None,
) -> None:
    """The options flow saves a new account selection."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={**CREDENTIALS, CONF_SELECTED_ACCOUNTS: [ACCOUNT_ID]},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SELECTED_ACCOUNTS: [ACCOUNT_ID]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_SELECTED_ACCOUNTS: [ACCOUNT_ID]}


async def test_reauth_repairs_stored_password(
    hass: HomeAssistant,
    fake_client: FakeNationalGridClient,
    enable_custom_integrations: None,
) -> None:
    """Re-authentication replaces the stored password."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={**CREDENTIALS, CONF_SELECTED_ACCOUNTS: [ACCOUNT_ID]},
    )
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "new-password"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"

    assert entry.data[CONF_PASSWORD] == "new-password"
    assert entry.data[CONF_SELECTED_ACCOUNTS] == [ACCOUNT_ID]
