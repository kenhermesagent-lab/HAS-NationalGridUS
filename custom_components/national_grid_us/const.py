"""Constants for the National Grid US integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

from homeassistant.const import UnitOfEnergy

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
UNIT_THERM: Final = "therm"

UNIT_CLASS_ENERGY: Final = "energy"
UNIT_CLASS_VOLUME: Final = "volume"

# National Grid's API reports gas as therms (``usageType: THERMS``); the meter
# reads CCF and the bill converts with this factor. Home Assistant only gained a
# native therm unit in 2026.11, so older versions get therms converted to CCF
# rather than carrying a number labelled with the wrong unit.
THERM_PER_CCF: Final = 1.03775

CURRENCY_USD: Final = "USD"

# What a month's cost figure covers. A statement is the whole bill for the
# period, delivery and fixed charges included; the cost endpoint reports the
# energy portion only, which is all that exists before a statement is issued.
COST_SOURCE_BILLED: Final = "billed"
COST_SOURCE_ENERGY: Final = "energy"

# Company codes accepted by the cost endpoint. The first one that answers for
# an account is remembered, because the code is not exposed anywhere else.
COMPANY_CODES: Final = ("KEDLI", "KEDNY", "NMPC", "NECO", "MECO", "NARR")

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


def has_therm_unit() -> bool:
    """Return True when this Home Assistant version knows the therm unit."""
    return UNIT_THERM in {str(unit) for unit in UnitOfEnergy}


def fuel_unit(fuel_type: str) -> str:
    """Return the unit National Grid reports a fuel type in."""
    if fuel_type.upper() == FUEL_GAS:
        return UNIT_THERM if has_therm_unit() else UNIT_CCF
    return UNIT_KWH


def fuel_unit_class(fuel_type: str) -> str:
    """Return the statistics unit class used for a fuel type."""
    if fuel_type.upper() == FUEL_GAS:
        return UNIT_CLASS_ENERGY if has_therm_unit() else UNIT_CLASS_VOLUME
    return UNIT_CLASS_ENERGY


def fuel_conversion(fuel_type: str) -> float:
    """Return the factor that takes API values to the unit we report."""
    if fuel_type.upper() == FUEL_GAS and not has_therm_unit():
        return 1 / THERM_PER_CCF
    return 1.0
