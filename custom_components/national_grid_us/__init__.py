"""Set up the National Grid US integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.util import dt as dt_util

from .api import create_client, create_session
from .const import (
    CONF_SELECTED_ACCOUNTS,
    DOMAIN,
    PLATFORMS,
    SETUP_ERRORS_KEY,
)
from .coordinator import NationalGridCoordinator
from .redact import redact_text

_LOGGER = logging.getLogger(__name__)


def _record_setup_error(
    hass: HomeAssistant, entry: ConfigEntry, err: Exception
) -> None:
    """Remember why setup failed so the diagnostics download can show it."""
    hass.data.setdefault(SETUP_ERRORS_KEY, {})[entry.entry_id] = {
        "type": type(err).__name__,
        "message": redact_text(str(err)),
        "time": dt_util.now().isoformat(),
    }


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up National Grid US from a config entry."""
    username = entry.data.get(CONF_USERNAME, "")
    password = entry.data.get(CONF_PASSWORD, "")
    selected = entry.options.get(
        CONF_SELECTED_ACCOUNTS, entry.data.get(CONF_SELECTED_ACCOUNTS, [])
    )

    session = create_session()
    coordinator = NationalGridCoordinator(
        hass,
        entry,
        create_client(hass, username, password, session=session),
        {str(account) for account in selected},
        session=session,
    )

    try:
        await coordinator.async_config_entry_first_refresh()
    except (ConfigEntryNotReady, ConfigEntryAuthFailed) as err:
        _record_setup_error(hass, entry, err)
        await session.close()
        raise

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    hass.data.setdefault(SETUP_ERRORS_KEY, {}).pop(entry.entry_id, None)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: NationalGridCoordinator | None = hass.data.get(DOMAIN, {}).pop(
            entry.entry_id, None
        )
        if coordinator is not None:
            await coordinator.client.close()
            if coordinator.session is not None:
                await coordinator.session.close()
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)
