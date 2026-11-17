"""Tests for the aiohttp session used for National Grid sign-in."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from custom_components.national_grid_us.api import create_client, create_session


async def test_session_uses_b2c_cookie_jar() -> None:
    """Azure AD B2C needs unquoted cookies, or the sign-in flow fails."""
    session = create_session()
    try:
        assert session.cookie_jar._quote_cookie is False
    finally:
        await session.close()


async def test_create_client_uses_supplied_session(hass: HomeAssistant) -> None:
    """The client must reuse the session that has the B2C cookie jar."""
    session = create_session()
    try:
        client = create_client(hass, "user@example.com", "secret", session=session)
        assert client._session is session
        assert client.config.username == "user@example.com"
    finally:
        await session.close()
