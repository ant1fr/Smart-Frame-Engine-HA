"""The Smart Frame Engine integration."""

from __future__ import annotations

from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import SmartFrameClient
from .const import CONF_PATH, DEFAULT_PATH, DOMAIN
from .coordinator import (
    SmartFrameConfigEntry,
    SmartFrameCoordinator,
    async_delete_compatibility_issue,
)
from .services import async_setup_services

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.IMAGE,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the services once; they resolve the target engine when called."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SmartFrameConfigEntry) -> bool:
    """Set up one engine from a config entry."""
    client = SmartFrameClient(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        entry.data.get(CONF_PATH, DEFAULT_PATH),
        entry.data.get(CONF_API_KEY),
    )
    coordinator = SmartFrameCoordinator(hass, entry, client)
    # Raises ConfigEntryNotReady (unreachable / incompatible) or ConfigEntryAuthFailed (401).
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SmartFrameConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: SmartFrameConfigEntry) -> None:
    """Drop the compatibility Repair issue together with the entry."""
    async_delete_compatibility_issue(hass, entry.entry_id)
