"""Base entity for Smart Frame Engine."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import SmartFrameCoordinator


class SmartFrameEntity(CoordinatorEntity[SmartFrameCoordinator]):
    """Entity attached to the single device that represents one engine."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SmartFrameCoordinator, description: EntityDescription) -> None:
        """Bind the entity to the engine's shared device."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.instance_id}_{description.key}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return the shared device (``sw_version`` from ``/api/health``, config URL)."""
        return self.coordinator.device_info
