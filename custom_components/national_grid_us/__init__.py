"""Set up the National Grid US integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .api import create_client
from .const import CONF_SELECTED_ACCOUNTS, DOMAIN, PLATFORMS
from .coordinator import NationalGridCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up National Grid US from a config entry."""
    username = entry.data.get(CONF_USERNAME, "")
    password = entry.data.get(CONF_PASSWORD, "")
    selected = entry.options.get(
        CONF_SELECTED_ACCOUNTS, entry.data.get(CONF_SELECTED_ACCOUNTS, [])
    )

    coordinator = NationalGridCoordinator(
        hass,
        entry,
        create_client(hass, username, password),
        {str(account) for account in selected},
    )

    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        await coordinator.client.close()
        raise

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
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
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)
