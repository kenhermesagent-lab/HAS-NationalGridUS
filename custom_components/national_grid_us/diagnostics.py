"""Diagnostics support for the National Grid US integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import get_last_statistics
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import CONF_SELECTED_ACCOUNTS, DOMAIN, SETUP_ERRORS_KEY
from .coordinator import NationalGridCoordinator
from .models import MeterUsage
from .redact import (
    REDACTION_VERSION,
    mask_identifier,
    mask_numbers,
    redact_text,
)
from .statistics import statistics_id


def _meter_diagnostics(meter: MeterUsage) -> dict[str, Any]:
    """Summarise one meter without exposing the account or meter numbers."""
    daily = sorted(meter.daily.items())
    hourly = sorted(meter.hourly.items())
    return {
        "account": mask_identifier(meter.account_id),
        "service_point": mask_identifier(meter.service_point),
        "meter_number": mask_identifier(meter.meter_number),
        "premise_number": mask_identifier(meter.premise_number),
        "fuel_type": meter.fuel_type,
        "is_smart_meter": meter.is_smart_meter,
        "unit": meter.unit,
        "daily_points": len(daily),
        "ami_rows": meter.ami_rows,
        "monthly_rows": meter.monthly_rows,
        "daily_first": daily[0][0].isoformat() if daily else None,
        "daily_last": daily[-1][0].isoformat() if daily else None,
        "daily_total": round(sum(value for _, value in daily), 3),
        "recent_daily": {day.isoformat(): value for day, value in daily[-7:]},
        "hourly_points": len(hourly),
        "hourly_first": hourly[0][0].isoformat() if hourly else None,
        "hourly_last": hourly[-1][0].isoformat() if hourly else None,
        "recent_hourly": {start.isoformat(): value for start, value in hourly[-6:]},
        "monthly_points": len(meter.monthly),
        "cost_rows": meter.cost_rows,
        "monthly_cost": {
            month.isoformat(): round(value, 2)
            for month, value in sorted(meter.monthly_cost.items())[-6:]
        },
        "cost_source": {
            month.isoformat(): source
            for month, source in sorted(meter.cost_source.items())[-6:]
        },
        "recent_monthly": {
            month.isoformat(): value
            for month, value in sorted(meter.monthly.items())[-6:]
        },
        "statistic_ids": [
            mask_numbers(statistics_id(meter)),
            mask_numbers(statistics_id(meter, returning=True)),
            mask_numbers(statistics_id(meter, cost=True)),
        ],
    }


async def _async_statistics_state(
    hass: HomeAssistant, statistic_ids: list[str]
) -> dict[str, Any]:
    """Return the last stored row for each statistic, if the recorder is up."""
    try:
        recorder = get_instance(hass)
        latest: dict[str, Any] = {}
        for statistic_id_value in statistic_ids:
            rows = await recorder.async_add_executor_job(
                get_last_statistics,
                hass,
                1,
                statistic_id_value,
                False,
                {"sum", "state"},
            )
            statistic_rows = rows.get(statistic_id_value)
            latest[mask_numbers(statistic_id_value)] = (
                {
                    "start": statistic_rows[0].get("start"),
                    "state": statistic_rows[0].get("state"),
                    "sum": statistic_rows[0].get("sum"),
                }
                if statistic_rows
                else None
            )
        return latest
    except (HomeAssistantError, KeyError):
        return {"error": "recorder not available"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return a redacted dump that explains what the integration is seeing."""
    selected = entry.options.get(
        CONF_SELECTED_ACCOUNTS, entry.data.get(CONF_SELECTED_ACCOUNTS, [])
    )
    diagnostics: dict[str, Any] = {
        "redaction_version": REDACTION_VERSION,
        "entry": {
            "title": redact_text(entry.title),
            "state": entry.state.name,
            "version": entry.version,
            "selected_accounts": [mask_identifier(account) for account in selected],
        },
        "setup_error": hass.data.get(SETUP_ERRORS_KEY, {}).get(entry.entry_id),
    }

    coordinator: NationalGridCoordinator | None = hass.data.get(DOMAIN, {}).get(
        entry.entry_id
    )
    if coordinator is None:
        diagnostics["coordinator"] = None
        diagnostics["meters"] = []
        diagnostics["statistics"] = {}
        return diagnostics

    data = coordinator.data
    diagnostics["coordinator"] = {
        "last_update_success": coordinator.last_update_success,
        "update_interval_seconds": (
            coordinator.update_interval.total_seconds()
            if coordinator.update_interval
            else None
        ),
        "meter_count": len(data.meters),
    }
    diagnostics["meters"] = [
        _meter_diagnostics(meter) for meter in data.meters.values()
    ]
    diagnostics["statistics"] = await _async_statistics_state(
        hass,
        [
            statistic
            for meter in data.meters.values()
            for statistic in (
                statistics_id(meter),
                statistics_id(meter, returning=True),
                statistics_id(meter, cost=True),
            )
        ],
    )
    return diagnostics
