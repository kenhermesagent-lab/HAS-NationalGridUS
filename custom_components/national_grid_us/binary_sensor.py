"""Binary sensors for the National Grid US integration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import NationalGridCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one smart meter sensor per meter."""
    coordinator: NationalGridCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        NationalGridSmartMeterSensor(coordinator, meter_key)
        for meter_key in coordinator.data.meters
    )


class NationalGridSmartMeterSensor(
    CoordinatorEntity[NationalGridCoordinator], BinarySensorEntity
):
    """Whether a meter is reporting usage readings to the integration."""

    _attr_has_entity_name = True
    _attr_translation_key = "smart_meter"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: NationalGridCoordinator, meter_key: str) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator)
        self._meter_key = meter_key
        meter = coordinator.data.meters[meter_key]
        self._attr_unique_id = (
            f"{DOMAIN}_{meter_key}_{meter.fuel_type.lower()}_smart_meter"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, meter.key)},
            manufacturer=MANUFACTURER,
            name=f"{meter.fuel_type.title()} meter {meter.service_point}",
            model="AMI smart meter" if meter.is_smart_meter else "Meter",
            serial_number=meter.meter_number or None,
        )

    @property
    def is_on(self) -> bool:
        """Return True when the meter has readings to report.

        The account API's own smart-meter flag is unreliable (it is unset on gas
        meters that do report daily usage), so this reports what the integration
        actually received.
        """
        meter = self.coordinator.data.meters.get(self._meter_key)
        return bool(meter and meter.has_readings)
