"""Diagnostics for Smart Frame Engine (secrets redacted)."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant

from .coordinator import SmartFrameConfigEntry

# The engine never returns secrets, but anything key-like is redacted defensively.
TO_REDACT = {
    CONF_API_KEY,
    "api_key",
    "password",
    "token",
    "X-API-Key",
    # Personal data: provider metadata (people, places) and the ambient people lists.
    "details",
    "presence",
    "boost_people",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SmartFrameConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = coordinator.data

    return async_redact_data(
        {
            "entry": {
                "unique_id": entry.unique_id,
                "title": entry.title,
                "data": dict(entry.data),
                "options": dict(entry.options),
            },
            "coordinator": {
                "last_update_success": coordinator.last_update_success,
                "update_interval_seconds": (
                    coordinator.update_interval.total_seconds()
                    if coordinator.update_interval
                    else None
                ),
            },
            "capabilities": sorted(coordinator.capabilities),
            "family_enabled": data.config.family_enabled if data and data.config else None,
            "health": data.health.as_dict() if data else None,
            "status": asdict(data.status) if data else None,
            "ambient": asdict(data.ambient) if data and data.ambient else None,
        },
        TO_REDACT,
    )
