"""Data coordinator for the National Grid US integration."""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, tzinfo

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from py_nationalgrid import (
    CannotConnectError,
    GraphQLError,
    InvalidAuthError,
    NationalGridClient,
    RestAPIError,
    RetryExhaustedError,
)

from .const import (
    AMI_HISTORY_DAYS,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    INTERVAL_HISTORY_DAYS,
    UTILITY_TIME_ZONE,
)
from .models import MeterUsage, NationalGridData
from .statistics import async_import_meter_statistics
from .usage import bucket_to_hours

_LOGGER = logging.getLogger(__name__)

# Errors that mean "this particular meter had no data right now" rather than
# "the whole account is unusable".
_METER_ERRORS = (
    CannotConnectError,
    GraphQLError,
    RestAPIError,
    RetryExhaustedError,
    TimeoutError,
)


def _utility_tz() -> tzinfo:
    """Return the timezone National Grid reports usage in."""
    tz = dt_util.get_time_zone(UTILITY_TIME_ZONE)
    if tz is None:  # pragma: no cover - defensive
        return dt_util.DEFAULT_TIME_ZONE
    return tz


def _parse_local_date(value: object) -> date | None:
    """Parse an AMI ``date`` field, which may or may not include a time."""
    if not isinstance(value, str) or not value:
        return None
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.date()
    return parsed.astimezone(_utility_tz()).date()


def _parse_local_datetime(value: object) -> datetime | None:
    """Parse an interval read timestamp into a local aware datetime."""
    if not isinstance(value, str) or not value:
        return None
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=_utility_tz())
    return parsed


class NationalGridCoordinator(DataUpdateCoordinator[NationalGridData]):
    """Fetch National Grid usage and push it into long term statistics."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: NationalGridClient,
        selected_accounts: set[str],
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=DEFAULT_UPDATE_INTERVAL,
        )
        self.entry = entry
        self.client = client
        self.selected_accounts = selected_accounts
        self.session = session
        self._stats_warned: set[str] = set()
        self._usage_warned: set[str] = set()

    async def async_force_refresh(self) -> None:
        """Re-fetch everything and refresh listeners."""
        await self.async_refresh()

    async def _async_update_data(self) -> NationalGridData:
        """Fetch accounts, meters and usage, then import statistics."""
        try:
            links = await self.client.get_linked_accounts()
        except InvalidAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except CannotConnectError as err:
            raise UpdateFailed(f"Unable to reach National Grid: {err}") from err
        except (GraphQLError, RetryExhaustedError) as err:
            status = getattr(err, "status", None)
            if status in (401, 403):
                raise ConfigEntryAuthFailed(str(err)) from err
            raise UpdateFailed(f"National Grid API error: {err}") from err
        except (RestAPIError, TimeoutError) as err:
            raise UpdateFailed(f"Unable to reach National Grid: {err}") from err

        available = {
            str(link["billingAccountId"]): link
            for link in links
            if link.get("billingAccountId")
        }

        wanted = (
            [account for account in available if account in self.selected_accounts]
            if self.selected_accounts
            else list(available)
        )
        if not wanted:
            raise UpdateFailed(
                "None of the selected billing accounts are linked to this login"
            )

        meters: dict[str, MeterUsage] = {}
        failures = 0

        for account_id in wanted:
            try:
                billing_account = await self.client.get_billing_account(account_id)
            except _METER_ERRORS as err:
                failures += 1
                _LOGGER.warning(
                    "Could not fetch billing account %s: %s", account_id, err
                )
                continue

            premise_number = billing_account.get("premiseNumber", "")
            for meter_info in billing_account.get("meter", {}).get("nodes", []):
                if not meter_info.get("servicePointNumber"):
                    continue
                meter = MeterUsage(
                    account_id=account_id,
                    service_point=str(meter_info["servicePointNumber"]),
                    meter_number=str(meter_info.get("meterNumber") or ""),
                    premise_number=str(premise_number),
                    meter_point_number=str(meter_info.get("meterPointNumber") or ""),
                    fuel_type=str(meter_info.get("fuelType") or "ELECTRIC").upper(),
                    is_smart_meter=bool(
                        meter_info.get("hasAmiSmartMeter")
                        or meter_info.get("isSmartMeter")
                    ),
                )
                await self._async_fetch_usage(meter)
                meters[meter.key] = meter

        if not meters and failures:
            raise UpdateFailed("Unable to fetch any National Grid meters; will retry")

        data = NationalGridData(meters=meters)
        await self._async_import_statistics(data)
        return data

    async def _async_fetch_usage(self, meter: MeterUsage) -> None:
        """Fill ``meter`` with settled daily usage and recent hourly reads."""
        if not meter.meter_number:
            return

        tz = _utility_tz()
        today = dt_util.now(tz).date()

        # National Grid serves daily AMI usage for gas meters as well, and does
        # not set the smart-meter flags on every meter that reports readings, so
        # ask for the data instead of trusting the flag.
        try:
            readings = await self.client.get_ami_energy_usages(
                meter_number=meter.meter_number,
                premise_number=meter.premise_number,
                service_point_number=meter.service_point,
                meter_point_number=meter.meter_point_number,
                date_from=today - timedelta(days=AMI_HISTORY_DAYS),
                date_to=today,
                fuel_type=meter.fuel_type,
            )
        except _METER_ERRORS as err:
            _LOGGER.warning(
                "Could not fetch AMI usage for meter %s: %s", meter.key, err
            )
            readings = []

        for reading in readings:
            day = _parse_local_date(reading.get("date"))
            quantity = reading.get("quantity")
            if day is None or quantity is None:
                continue
            meter.daily[day] = meter.daily.get(day, 0.0) + float(quantity)

        if meter.daily:
            self._usage_warned.discard(meter.key)
        elif meter.key not in self._usage_warned:
            # A meter the portal shows readings for should not be silent, so say
            # so once instead of hiding it behind a debug line.
            self._usage_warned.add(meter.key)
            _LOGGER.warning(
                "National Grid returned no daily %s usage for meter %s in the "
                "last %s days; the meter may not report AMI readings",
                meter.fuel_type,
                meter.key,
                AMI_HISTORY_DAYS,
            )

        if not meter.is_electric:
            # Gas meters do not expose interval reads; daily data is all there is.
            return

        start_day = today - timedelta(days=INTERVAL_HISTORY_DAYS)
        try:
            reads = await self.client.get_interval_reads(
                premise_number=meter.premise_number,
                service_point_number=meter.service_point,
                start_datetime=datetime.combine(start_day, time.min),
            )
        except _METER_ERRORS as err:
            _LOGGER.debug("No interval reads for meter %s: %s", meter.key, err)
            return

        intervals: list[tuple[datetime, float]] = []
        for read in reads:
            start = _parse_local_datetime(read.get("startTime"))
            value = read.get("value")
            if start is None or value is None:
                continue
            if start.astimezone(tz).date() < start_day:
                continue
            intervals.append((start, float(value)))

        meter.hourly = bucket_to_hours(intervals, tz)

    async def _async_import_statistics(self, data: NationalGridData) -> None:
        """Import usage into long term statistics, never failing the refresh."""
        for meter in data.meters.values():
            try:
                await async_import_meter_statistics(self.hass, meter)
            except HomeAssistantError as err:
                # Warn once per meter so a misconfiguration is visible without
                # spamming the log on every poll.
                if meter.key in self._stats_warned:
                    _LOGGER.debug(
                        "Statistics import for meter %s failed: %s", meter.key, err
                    )
                else:
                    self._stats_warned.add(meter.key)
                    _LOGGER.warning(
                        "Long-term statistics could not be imported for meter %s: %s",
                        meter.key,
                        err,
                    )
            except Exception:
                _LOGGER.exception(
                    "Unexpected error importing statistics for meter %s", meter.key
                )
