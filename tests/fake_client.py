"""A fake National Grid client for the Home Assistant test harness."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.util import dt as dt_util

TZ_NAME = "America/New_York"

ACCOUNT_ID = "1234567890"
SERVICE_POINT = 777
PREMISE = 555000111
METER_NUMBER = "M-1"


class FakeNationalGridClient:
    """Stand-in for ``py_nationalgrid.NationalGridClient``.

    Mirrors the real client's public surface used by the integration. Behaviour
    can be changed per test by setting ``fail_with`` or ``no_accounts``.
    """

    def __init__(self) -> None:
        """Initialise an empty fake."""
        self.fail_with: Exception | None = None
        self.no_accounts = False
        self.closed = False
        self.calls: list[str] = []
        self.daily_usage: dict[str, float] | None = None
        self.interval_usage: list[float] | None = None

    def _check(self, name: str) -> None:
        """Record the call and raise the configured error, if any."""
        self.calls.append(name)
        if self.fail_with is not None:
            raise self.fail_with

    async def get_linked_accounts(self) -> list[dict[str, Any]]:
        """Return the linked billing accounts."""
        self._check("get_linked_accounts")
        if self.no_accounts:
            return []
        return [{"billingAccountId": ACCOUNT_ID, "billingAccount": {}}]

    async def get_billing_account(self, account_number: str) -> dict[str, Any]:
        """Return one billing account with a single electric AMI meter."""
        self._check("get_billing_account")
        return {
            "premiseNumber": PREMISE,
            "regionAbbreviation": "NY",
            "meter": {
                "nodes": [
                    {
                        "meterNumber": METER_NUMBER,
                        "servicePointNumber": SERVICE_POINT,
                        "meterPointNumber": 1,
                        "fuelType": "ELECTRIC",
                        "isSmartMeter": True,
                        "hasAmiSmartMeter": True,
                    }
                ]
            },
        }

    async def get_ami_energy_usages(self, **kwargs: Any) -> list[dict[str, Any]]:
        """Return settled daily usage for the previous two days."""
        self._check("get_ami_energy_usages")
        today = dt_util.now(dt_util.get_time_zone(TZ_NAME)).date()
        usage = self.daily_usage
        if usage is None:
            usage = {
                (today - timedelta(days=1)).isoformat(): 10.0,
                (today - timedelta(days=2)).isoformat(): 5.0,
            }
        return [
            {"date": day, "fuelType": "ELECTRIC", "quantity": quantity}
            for day, quantity in usage.items()
        ]

    async def get_interval_reads(self, **kwargs: Any) -> list[dict[str, Any]]:
        """Return four 15-minute reads inside the current hour."""
        self._check("get_interval_reads")
        tz = dt_util.get_time_zone(TZ_NAME)
        hour_start = dt_util.now(tz).replace(minute=0, second=0, microsecond=0)
        values = self.interval_usage or [0.25, 0.25, 0.25, 0.25]
        reads = []
        for index, value in enumerate(values):
            start = hour_start + timedelta(minutes=15 * index)
            reads.append(
                {
                    "startTime": start.isoformat(),
                    "endTime": (start + timedelta(minutes=15)).isoformat(),
                    "value": value,
                }
            )
        return reads

    async def close(self) -> None:
        """Mark the client as closed."""
        self.closed = True
