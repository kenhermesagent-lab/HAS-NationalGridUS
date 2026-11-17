"""Thin helpers around the py-nationalgrid client."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from py_nationalgrid import NationalGridClient, NationalGridConfig


def create_client(
    hass: HomeAssistant, username: str, password: str
) -> NationalGridClient:
    """Create a National Grid client that reuses Home Assistant's session."""
    return NationalGridClient(
        config=NationalGridConfig(username=username, password=password),
        session=async_get_clientsession(hass),
    )
