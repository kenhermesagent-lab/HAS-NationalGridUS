"""Config flow for the National Grid US integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers import selector
from py_nationalgrid import (
    CannotConnectError,
    GraphQLError,
    InvalidAuthError,
    NationalGridClient,
    RetryExhaustedError,
)

from .api import create_client
from .const import CONF_SELECTED_ACCOUNTS, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def _async_get_accounts(
    client: NationalGridClient,
) -> list[str]:
    """Log in and return the billing account numbers linked to the login."""
    links = await client.get_linked_accounts()
    accounts = [
        str(link["billingAccountId"]) for link in links if link.get("billingAccountId")
    ]
    return sorted(dict.fromkeys(accounts))


def _account_selector(accounts: list[str]) -> selector.SelectSelector:
    """Build the multi-select used to pick billing accounts."""
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[
                selector.SelectOptionDict(value=account, label=f"Account {account}")
                for account in accounts
            ],
            multiple=True,
            mode=selector.SelectSelectorMode.LIST,
        )
    )


class NationalGridConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the National Grid US config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow."""
        self._accounts: list[str] = []
        self._username: str = ""
        self._password: str = ""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for National Grid credentials."""
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            password = user_input[CONF_PASSWORD]
            client = create_client(self.hass, username, password)
            try:
                accounts = await _async_get_accounts(client)
            except InvalidAuthError:
                errors["base"] = "invalid_auth"
            except GraphQLError as err:
                if err.status in (401, 403):
                    errors["base"] = "invalid_auth"
                else:
                    _LOGGER.debug("National Grid login failed: %s", err)
                    errors["base"] = "cannot_connect"
            except (CannotConnectError, RetryExhaustedError, TimeoutError):
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error logging in to National Grid")
                errors["base"] = "unknown"
            else:
                if not accounts:
                    errors["base"] = "no_accounts"
                else:
                    await self.async_set_unique_id(username.lower())
                    self._abort_if_unique_id_configured()
                    self._accounts = accounts
                    self._username = username
                    self._password = password
                    return await self.async_step_select_accounts()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_select_accounts(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick which billing accounts to monitor."""
        if user_input is not None:
            return self.async_create_entry(
                title=f"National Grid ({self._username})",
                data={
                    CONF_USERNAME: self._username,
                    CONF_PASSWORD: self._password,
                    CONF_SELECTED_ACCOUNTS: user_input[CONF_SELECTED_ACCOUNTS],
                },
            )

        return self.async_show_form(
            step_id="select_accounts",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SELECTED_ACCOUNTS,
                        default=self._accounts,
                    ): _account_selector(self._accounts),
                }
            ),
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Handle re-authentication when credentials stop working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the password again."""
        errors: dict[str, str] = {}
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        assert entry is not None
        username = entry.data[CONF_USERNAME]

        if user_input is not None:
            password = user_input[CONF_PASSWORD]
            client = create_client(self.hass, username, password)
            try:
                accounts = await _async_get_accounts(client)
            except (InvalidAuthError, GraphQLError) as err:
                if isinstance(err, GraphQLError) and err.status not in (401, 403):
                    errors["base"] = "cannot_connect"
                else:
                    errors["base"] = "invalid_auth"
            except (CannotConnectError, RetryExhaustedError, TimeoutError):
                errors["base"] = "cannot_connect"
            else:
                data = {**entry.data, CONF_PASSWORD: password}
                selected = [
                    account
                    for account in data.get(CONF_SELECTED_ACCOUNTS, [])
                    if account in accounts
                ]
                if selected:
                    data[CONF_SELECTED_ACCOUNTS] = selected
                self.hass.config_entries.async_update_entry(entry, data=data)
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
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

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return NationalGridOptionsFlow(config_entry)


class NationalGridOptionsFlow(OptionsFlow):
    """Change which billing accounts are monitored."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialise the options flow."""
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the account picker."""
        if user_input is not None:
            return self.async_create_entry(
                data={CONF_SELECTED_ACCOUNTS: user_input[CONF_SELECTED_ACCOUNTS]}
            )

        current = self._entry.options.get(
            CONF_SELECTED_ACCOUNTS,
            self._entry.data.get(CONF_SELECTED_ACCOUNTS, []),
        )

        # Refresh the list of linked accounts, but never block the form when
        # the login can't be reached right now - the stored selection is enough
        # to edit.
        accounts = set(current)
        try:
            accounts |= set(
                await _async_get_accounts(
                    create_client(
                        self.hass,
                        self._entry.data[CONF_USERNAME],
                        self._entry.data[CONF_PASSWORD],
                    )
                )
            )
        except Exception:
            _LOGGER.debug(
                "Could not refresh linked accounts for the options form",
                exc_info=True,
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SELECTED_ACCOUNTS, default=list(current)
                    ): _account_selector(sorted(accounts)),
                }
            ),
        )
