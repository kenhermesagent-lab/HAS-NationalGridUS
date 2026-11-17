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
    MONTHLY_HISTORY_MONTHS,
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


def _parse_usage_month(value: object) -> date | None:
    """Parse an ``energyUsages`` ``usageYearMonth`` into the month's first day."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, str):
        text = value.strip()
    else:
        return None
    if len(text) != 6 or not text.isdigit():
        return None
    year, month = int(text[:4]), int(text[4:])
    if not 1 <= month <= 12:
        return None
    return date(year, month, 1)


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
            meter_nodes = [
                node
                for node in billing_account.get("meter", {}).get("nodes", [])
                if node.get("servicePointNumber")
            ]
            for meter_info in meter_nodes:
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
                await self._async_fetch_usage(meter, single_meter=len(meter_nodes) == 1)
                meters[meter.key] = meter

        if not meters and failures:
            raise UpdateFailed("Unable to fetch any National Grid meters; will retry")

        data = NationalGridData(meters=meters)
        await self._async_import_statistics(data)
        return data

    async def _async_fetch_usage(
        self, meter: MeterUsage, *, single_meter: bool
    ) -> None:
        """Fill ``meter`` with daily, hourly or, failing both, monthly usage."""
        if not meter.meter_number:
            return

        tz = _utility_tz()
        today = dt_util.now(tz).date()

        await self._async_fetch_daily(meter, today)
        if meter.is_electric:
            await self._async_fetch_intervals(meter, today, tz)
        if not meter.daily and not meter.hourly:
            await self._async_fetch_monthly(meter, today, single_meter=single_meter)

        if meter.has_readings:
            self._usage_warned.discard(meter.key)
        elif meter.key not in self._usage_warned:
            # A meter the portal shows readings for should not be silent, so say
            # so once instead of hiding it behind a debug line.
            self._usage_warned.add(meter.key)
            _LOGGER.warning(
                "National Grid returned no usage for %s meter %s: the daily, "
                "interval and monthly requests all came back empty",
                meter.fuel_type,
                meter.key,
            )

    async def _async_fetch_daily(self, meter: MeterUsage, today: date) -> None:
        """Add settled daily AMI readings to ``meter``."""

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

        meter.ami_rows = len(readings)
        for reading in readings:
            day = _parse_local_date(reading.get("date"))
            quantity = reading.get("quantity")
            if day is None or quantity is None:
                continue
            meter.daily[day] = meter.daily.get(day, 0.0) + float(quantity)

    async def _async_fetch_intervals(
        self, meter: MeterUsage, today: date, tz: tzinfo
    ) -> None:
        """Add near real-time reads, bucketed into local hours.

        Only electricity meters publish interval reads; gas meters return none.
        """
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

    async def _async_fetch_monthly(
        self, meter: MeterUsage, today: date, *, single_meter: bool
    ) -> None:
        """Add account-level monthly totals when the meter has no readings.

        The ``energyUsages`` query is keyed by account and its rows carry no fuel
        type, so they are only attributed to a meter when the account has exactly
        one. That is what makes non-AMI gas meters, which publish nothing else,
        report their CCF at all.
        """
        if not single_meter:
            _LOGGER.debug(
                "Skipping monthly usage for meter %s: the account has several "
                "meters and the totals carry no fuel type",
                meter.key,
            )
            return

        first_month = (today.replace(day=1) - timedelta(days=366)).replace(day=1)
        try:
            rows = await self.client.get_energy_usages(
                meter.account_id,
                int(first_month.strftime("%Y%m")),
                first=MONTHLY_HISTORY_MONTHS,
            )
        except _METER_ERRORS as err:
            _LOGGER.debug("No monthly usage for meter %s: %s", meter.key, err)
            return

        meter.monthly_rows = len(rows)
        for row in rows:
            month = _parse_usage_month(row.get("usageYearMonth"))
            usage = row.get("usage")
            if month is None or usage is None:
                continue
            meter.monthly[month] = meter.monthly.get(month, 0.0) + float(usage)

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
