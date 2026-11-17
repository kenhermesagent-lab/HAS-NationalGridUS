"""Repairs flow that re-authenticates a National Grid US entry.

The auth-failed repair issue is fixable, so Home Assistant offers a button in
the Repairs panel. Clicking it lands here, and the password is validated and
applied by the same helper the config flow's reauth step uses. The repairs flow
manager drops the issue once the flow creates an entry.
"""

from __future__ import annotations

from typing import Any

from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .config_flow import async_reauthenticate_entry, reauth_password_schema


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

        # The repairs flow manager passes the issue data as the first input, so
        # only a submission that actually carries a password counts as one.
        if user_input is not None and CONF_PASSWORD in user_input:
            error = await async_reauthenticate_entry(
                self.hass, self._entry, user_input[CONF_PASSWORD]
            )
            if error is None:
                return self.async_create_entry(data={})
            errors["base"] = error

        return self.async_show_form(
            step_id="init",
            data_schema=reauth_password_schema(),
            description_placeholders={CONF_USERNAME: self._entry.data[CONF_USERNAME]},
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
