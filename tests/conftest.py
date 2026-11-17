"""Test setup for the National Grid US integration."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.fake_client import FakeNationalGridClient

pytest_plugins = "pytest_homeassistant_custom_component"

# ``enable_custom_integrations`` is requested by each test instead of being
# autouse: it depends on the ``hass`` fixture, and the recorder fixtures must
# create their database *before* ``hass`` exists. Requesting it after
# ``recorder_mock`` in the test signature keeps both working.


class FakeSession:
    """Stand-in for the aiohttp session the integration creates."""

    def __init__(self) -> None:
        """Initialise the fake."""
        self.closed = False

    async def close(self) -> None:
        """Record that the session was closed."""
        self.closed = True


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> FakeNationalGridClient:
    """Replace the real National Grid client and session with fakes."""
    client = FakeNationalGridClient()

    monkeypatch.setattr(
        "custom_components.national_grid_us.create_client",
        lambda *args, **kwargs: client,
    )
    monkeypatch.setattr(
        "custom_components.national_grid_us.config_flow.create_client",
        lambda *args, **kwargs: client,
    )
    monkeypatch.setattr(
        "custom_components.national_grid_us.create_session", FakeSession
    )
    monkeypatch.setattr(
        "custom_components.national_grid_us.config_flow.create_session", FakeSession
    )
    return client
