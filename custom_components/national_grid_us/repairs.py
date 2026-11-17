"""Repairs flow that re-authenticates a National Grid US entry.

The auth-failed repair issue is fixable, so Home Assistant offers a button in
the Repairs panel. Clicking it lands here: the user re-enters the password, the
entry is updated and reloaded, and the issue is dropped by the repairs flow
manager once the flow creates an entry.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import selector

from .config_flow import _async_get_accounts, _login_error
from .const import CONF_SELECTED_ACCOUNTS

_LOGGER = logging.getLogger(__name__)


class ReauthRepairFlow(RepairsFlow):
    """Ask for the password again from the Repairs panel."""

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialise the flow for one config entry."""
        self._entry = entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        """Show the re-authentication form and store the new password."""
        errors: dict[str, str] = {}
        username = self._entry.data[CONF_USERNAME]

        # The repairs flow manager passes the issue data as the first input, so
        # only a submission that actually carries a password counts as one.
        if user_input is not None and CONF_PASSWORD in user_input:
            password = user_input[CONF_PASSWORD]
            try:
                accounts = await _async_get_accounts(self.hass, username, password)
            except Exception as err:
                errors["base"] = _login_error(err)
                if errors["base"] == "unknown":
                    _LOGGER.exception("Unexpected error re-authenticating from Repairs")
            else:
                data = {**self._entry.data, CONF_PASSWORD: password}
                selected = [
                    account
                    for account in data.get(CONF_SELECTED_ACCOUNTS, [])
                    if account in accounts
                ]
                if selected:
                    data[CONF_SELECTED_ACCOUNTS] = selected
                self.hass.config_entries.async_update_entry(self._entry, data=data)
                await self.hass.config_entries.async_reload(self._entry.entry_id)
                return self.async_create_entry(data={})

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    )
                }
            ),
            description_placeholders={CONF_USERNAME: username},
            errors=errors,
        )


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create the fix flow for the sign-in repair issue."""
    entry: ConfigEntry | None = None
    if data is not None and isinstance(entry_id := data.get("entry_id"), str):
        entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None:
        raise ValueError(f"unknown repair issue {issue_id}")
    return ReauthRepairFlow(entry)
