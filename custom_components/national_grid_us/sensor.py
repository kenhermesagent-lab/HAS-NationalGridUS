"""Usage sensors for the National Grid US integration."""

from __future__ import annotations

from datetime import date, datetime, tzinfo

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    COST_SOURCE_BILLED,
    COST_SOURCE_ENERGY,
    CURRENCY_USD,
    DOMAIN,
    MANUFACTURER,
    METRIC_LAST_MONTH,
    METRIC_THIS_HOUR,
    METRIC_THIS_MONTH,
    METRIC_THIS_WEEK,
    METRIC_TODAY,
    METRIC_YESTERDAY,
    SENSOR_METRICS,
    UTILITY_TIME_ZONE,
    has_therm_unit,
)
from .coordinator import NationalGridCoordinator
from .models import MeterUsage
from .usage import local_midnight


def _utility_tz() -> tzinfo:
    """Return the timezone National Grid reports usage in."""
    return dt_util.get_time_zone(UTILITY_TIME_ZONE) or dt_util.DEFAULT_TIME_ZONE


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up usage and cost sensors for every meter."""
    coordinator: NationalGridCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = [
        NationalGridUsageSensor(coordinator, meter_key, metric)
        for meter_key in coordinator.data.meters
        for metric in SENSOR_METRICS
    ]
    entities.extend(
        NationalGridCostSensor(coordinator, meter_key)
        for meter_key in coordinator.data.meters
    )
    async_add_entities(entities)


class NationalGridUsageSensor(CoordinatorEntity[NationalGridCoordinator], SensorEntity):
    """A single usage total (hour, day, week, month) for one meter."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 3

    def __init__(
        self,
        coordinator: NationalGridCoordinator,
        meter_key: str,
        metric: str,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator)
        self._meter_key = meter_key
        self._metric = metric
        self._attr_translation_key = metric
        meter = coordinator.data.meters[meter_key]
        self._attr_unique_id = (
            f"{DOMAIN}_{meter_key}_{meter.fuel_type.lower()}_{metric}"
        )

    @property
    def _meter(self) -> MeterUsage | None:
        """Return the meter this sensor belongs to."""
        return self.coordinator.data.meters.get(self._meter_key)

    @property
    def available(self) -> bool:
        """Return True when the meter is still present in the latest data."""
        return super().available and self._meter is not None

    @property
    def device_info(self) -> DeviceInfo | None:
        """Group all sensors of a meter under one device."""
        meter = self._meter
        if meter is None:
            return None
        return DeviceInfo(
            identifiers={(DOMAIN, meter.key)},
            manufacturer=MANUFACTURER,
            name=f"{meter.fuel_type.title()} meter {meter.service_point}",
            model="AMI smart meter" if meter.is_smart_meter else "Meter",
            serial_number=meter.meter_number or None,
        )

    @property
    def device_class(self) -> SensorDeviceClass | None:
        """Return the device class matching the unit this meter reports.

        National Grid reports gas in therms, which Home Assistant accepts under
        the energy device class only; versions without a therm unit get CCF and
        the gas device class.
        """
        meter = self._meter
        if meter is None:
            return None
        if meter.is_electric or has_therm_unit():
            return SensorDeviceClass.ENERGY
        return SensorDeviceClass.GAS

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the unit National Grid reports in."""
        meter = self._meter
        return meter.unit if meter else None

    @property
    def native_value(self) -> float | None:
        """Return the usage total for this sensor's period."""
        meter = self._meter
        if meter is None:
            return None
        totals = meter.period_totals(_utility_tz(), dt_util.now())
        return round(getattr(totals, self._metric), 3)

    @property
    def last_reset(self) -> datetime | None:
        """Return the start of the period this sensor resets on."""
        meter = self._meter
        if meter is None:
            return None
        tz = _utility_tz()
        now = dt_util.now(tz)
        totals = meter.period_totals(tz, now)

        if self._metric == METRIC_THIS_HOUR:
            return now.replace(minute=0, second=0, microsecond=0)
        if self._metric == METRIC_TODAY:
            return local_midnight(now.date(), tz)
        if self._metric == METRIC_YESTERDAY:
            return local_midnight(totals.yesterday_date, tz)
        if self._metric == METRIC_THIS_WEEK:
            return local_midnight(totals.week_start, tz)
        if self._metric == METRIC_THIS_MONTH:
            return local_midnight(totals.month_start, tz)
        if self._metric == METRIC_LAST_MONTH:
            return local_midnight(totals.last_month_start, tz)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Expose which period the value covers."""
        meter = self._meter
        if meter is None:
            return None
        totals = meter.period_totals(_utility_tz(), dt_util.now())

        if self._metric == METRIC_THIS_WEEK:
            return {"week_start": totals.week_start.isoformat()}
        if self._metric == METRIC_THIS_MONTH:
            return {"month_start": totals.month_start.isoformat()}
        if self._metric == METRIC_LAST_MONTH:
            return {"month_start": totals.last_month_start.isoformat()}
        return None


class NationalGridCostSensor(CoordinatorEntity[NationalGridCoordinator], SensorEntity):
    """The cost National Grid billed for one meter in the current month.

    Energy dashboard cost sources need a monetary sensor whose statistics
    accumulate, so this uses the total state class and resets at local midnight
    on the first of the month.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "cost_this_month"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = CURRENCY_USD
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: NationalGridCoordinator, meter_key: str) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator)
        self._meter_key = meter_key
        meter = coordinator.data.meters[meter_key]
        self._attr_unique_id = (
            f"{DOMAIN}_{meter_key}_{meter.fuel_type.lower()}_cost_this_month"
        )

    @property
    def _meter(self) -> MeterUsage | None:
        """Return the meter this sensor belongs to."""
        return self.coordinator.data.meters.get(self._meter_key)

    @property
    def available(self) -> bool:
        """Return True when the meter is still present in the latest data."""
        return super().available and self._meter is not None

    @property
    def device_info(self) -> DeviceInfo | None:
        """Group this sensor with the rest of the meter's entities."""
        meter = self._meter
        if meter is None:
            return None
        return DeviceInfo(
            identifiers={(DOMAIN, meter.key)},
            manufacturer=MANUFACTURER,
            name=f"{meter.fuel_type.title()} meter {meter.service_point}",
            model="AMI smart meter" if meter.is_smart_meter else "Meter",
            serial_number=meter.meter_number or None,
        )

    def _month_start(self) -> date:
        """Return the first day of the current month in the utility timezone."""
        return dt_util.now(_utility_tz()).date().replace(day=1)

    @property
    def native_value(self) -> float | None:
        """Return the cost billed for this meter so far this month."""
        meter = self._meter
        if meter is None:
            return None
        return round(meter.monthly_cost.get(self._month_start(), 0.0), 2)

    @property
    def last_reset(self) -> datetime | None:
        """Return the start of the month this sensor resets on."""
        return local_midnight(self._month_start(), _utility_tz())

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Say what the figure covers, because the two kinds are not comparable."""
        meter = self._meter
        if meter is None:
            return None
        month = self._month_start()
        source = meter.cost_source.get(month)
        if source == COST_SOURCE_BILLED:
            detail = (
                "Billed total for the statement: usage plus delivery, supply "
                "and fixed charges"
            )
        elif source == COST_SOURCE_ENERGY:
            detail = (
                "Energy cost only for the billing period so far, before the "
                "statement is issued; it excludes delivery and fixed charges"
            )
        else:
            detail = "National Grid has not reported cost for this month yet"
        return {
            "cost_month": month.isoformat(),
            "cost_source": source or "none",
            "cost_source_detail": detail,
        }
