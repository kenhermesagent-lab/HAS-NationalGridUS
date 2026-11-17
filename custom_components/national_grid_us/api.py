"""Thin helpers around the py-nationalgrid client."""

from __future__ import annotations

import aiohttp
from homeassistant.core import HomeAssistant
from py_nationalgrid import NationalGridClient, NationalGridConfig, create_cookie_jar

TIMEOUT_SECONDS = 60


def create_session() -> aiohttp.ClientSession:
    """Create an aiohttp session that can complete National Grid's sign-in.

    ``py-nationalgrid`` documents that Azure AD B2C needs a cookie jar created
    with ``quote_cookie=False``. Home Assistant's shared session uses the
    default jar, which breaks the B2C login flow, so the integration (and the
    config flow) must own a session built with the library's jar.
    """
    return aiohttp.ClientSession(
        cookie_jar=create_cookie_jar(),
        timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS),
    )


def create_client(
    hass: HomeAssistant,
    username: str,
    password: str,
    *,
    session: aiohttp.ClientSession | None = None,
) -> NationalGridClient:
    """Create a National Grid client bound to ``session``."""
    return NationalGridClient(
        config=NationalGridConfig(username=username, password=password),
        session=session or create_session(),
    )
