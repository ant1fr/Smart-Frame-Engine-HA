"""Family Mode switch."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CAPABILITY_FAMILY_MODE
from .coordinator import SmartFrameConfigEntry, SmartFrameCoordinator
from .entity import SmartFrameEntity

PARALLEL_UPDATES = 1

FAMILY_MODE = SwitchEntityDescription(key="family_mode", translation_key="family_mode")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmartFrameConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Family Mode switch when the engine supports it."""
    coordinator = entry.runtime_data
    if CAPABILITY_FAMILY_MODE in coordinator.capabilities:
        async_add_entities([SmartFrameFamilyModeSwitch(coordinator)])


class SmartFrameFamilyModeSwitch(SmartFrameEntity, SwitchEntity):
    """The persistent Family Mode setting (``config.family.enabled``).

    A temporary override made through the ambient context is reported in the
    ``active_now`` attribute (``AmbientView.family_active``: "applies right now").
    """

    def __init__(self, coordinator: SmartFrameCoordinator) -> None:
        """Initialise the switch."""
        super().__init__(coordinator, FAMILY_MODE)

    @property
    def is_on(self) -> bool | None:
        """Return the stored Family Mode setting."""
        config = self.coordinator.data.config
        return config.family_enabled if config else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Report whether Family Mode applies right now (a temporary override may differ)."""
        ambient = self.coordinator.data.ambient
        return {"active_now": ambient.family_active} if ambient else None

    async def _async_set(self, enabled: bool) -> None:
        coordinator = self.coordinator
        config = await coordinator.async_action(coordinator.client.async_set_family_mode(enabled))
        coordinator.async_apply_config(config)
        await coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable Family Mode."""
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable Family Mode."""
        await self._async_set(False)
