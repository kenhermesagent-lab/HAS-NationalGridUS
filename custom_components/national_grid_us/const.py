"""Constants for the National Grid US integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "national_grid_us"
MANUFACTURER: Final = "National Grid"
CONFIGURATION_URL: Final = "https://myaccount.nationalgrid.com"

PLATFORMS: Final = ("sensor", "binary_sensor", "button")

CONF_SELECTED_ACCOUNTS: Final = "selected_accounts"

# Why the last setup attempt failed, kept so diagnostics can explain it even
# though the config entry never finished setting up.
SETUP_ERRORS_KEY: Final = f"{DOMAIN}_setup_errors"

# National Grid publishes new AMI readings roughly every hour and finalizes
# them with a 1-2 day delay, so polling every 30 minutes is plenty.
DEFAULT_UPDATE_INTERVAL: Final = timedelta(minutes=30)

# How far back to ask for settled daily AMI usage. The API keeps roughly 45
# days in "hot" storage; anything older triggers a gateway timeout.
AMI_HISTORY_DAYS: Final = 45

# How far back to ask for 15-minute interval reads. This endpoint only serves
# a very short live window (the National Grid portal itself uses 24 hours).
INTERVAL_HISTORY_DAYS: Final = 2

# How many months of account-level usage to request when a meter has no AMI
# readings. These totals carry no fuel type, so they are only attributed to a
# meter when the account has exactly one.
MONTHLY_HISTORY_MONTHS: Final = 13

FUEL_ELECTRIC: Final = "ELECTRIC"
FUEL_GAS: Final = "GAS"

UNIT_KWH: Final = "kWh"
UNIT_CCF: Final = "CCF"

UNIT_CLASS_ENERGY: Final = "energy"
UNIT_CLASS_VOLUME: Final = "volume"

# fuel type -> (unit of measurement, statistic unit class)
FUEL_UNITS: Final = {
    FUEL_ELECTRIC: (UNIT_KWH, UNIT_CLASS_ENERGY),
    FUEL_GAS: (UNIT_CCF, UNIT_CLASS_VOLUME),
}

# National Grid reports usage in the timezone of the service address. Interval
# reads are timezone aware; settled daily totals are bucketed in this zone.
UTILITY_TIME_ZONE: Final = "America/New_York"

METRIC_THIS_HOUR: Final = "this_hour"
METRIC_TODAY: Final = "today"
METRIC_YESTERDAY: Final = "yesterday"
METRIC_THIS_WEEK: Final = "this_week"
METRIC_THIS_MONTH: Final = "this_month"
METRIC_LAST_MONTH: Final = "last_month"

SENSOR_METRICS: Final = (
    METRIC_THIS_HOUR,
    METRIC_TODAY,
    METRIC_YESTERDAY,
    METRIC_THIS_WEEK,
    METRIC_THIS_MONTH,
    METRIC_LAST_MONTH,
)


def fuel_unit(fuel_type: str) -> str:
    """Return the unit of measurement used for a fuel type."""
    return FUEL_UNITS.get(fuel_type.upper(), FUEL_UNITS[FUEL_ELECTRIC])[0]


def fuel_unit_class(fuel_type: str) -> str:
    """Return the statistics unit class used for a fuel type."""
    return FUEL_UNITS.get(fuel_type.upper(), FUEL_UNITS[FUEL_ELECTRIC])[1]
