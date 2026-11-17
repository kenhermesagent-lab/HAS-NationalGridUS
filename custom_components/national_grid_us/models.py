"""Data models shared by the National Grid US integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, tzinfo

from .const import FUEL_ELECTRIC, fuel_unit, fuel_unit_class
from .usage import PeriodTotals, compute_period_totals


@dataclass(slots=True)
class MeterUsage:
    """Usage data for a single meter (service point)."""

    account_id: str
    service_point: str
    meter_number: str
    premise_number: str
    meter_point_number: str
    fuel_type: str
    is_smart_meter: bool
    # Settled daily totals from the AMI GraphQL endpoint, keyed by local date.
    daily: dict[date, float] = field(default_factory=dict)
    # Near real-time 15-minute reads aggregated into local hours.
    hourly: dict[datetime, float] = field(default_factory=dict)

    @property
    def key(self) -> str:
        """Return a stable identifier for this meter."""
        return f"{self.account_id}_{self.service_point}"

    @property
    def is_electric(self) -> bool:
        """Return True for electricity meters."""
        return self.fuel_type.upper() == FUEL_ELECTRIC

    @property
    def unit(self) -> str:
        """Return the unit of measurement for this meter."""
        return fuel_unit(self.fuel_type)

    @property
    def unit_class(self) -> str:
        """Return the statistics unit class for this meter."""
        return fuel_unit_class(self.fuel_type)

    def period_totals(self, tz: tzinfo, now: datetime) -> PeriodTotals:
        """Return the hourly/daily/weekly/monthly totals for this meter."""
        return compute_period_totals(self.daily, self.hourly, tz, now)


@dataclass(slots=True)
class NationalGridData:
    """Coordinator payload."""

    meters: dict[str, MeterUsage] = field(default_factory=dict)
