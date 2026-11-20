"""Binary sensors: engine/frame connectivity and delivery state."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# Imported from the package: before HA 2026.2 the class has no ``.const`` module, and later
# versions only re-export it (hence the type-checker ignore).
from homeassistant.components.binary_sensor import (  # type: ignore[attr-defined]
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import SmartFrameConfigEntry, SmartFrameCoordinator
from .entity import SmartFrameEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class SmartFrameBinarySensorDescription(BinarySensorEntityDescription):
    """Describe a binary sensor backed by the coordinator."""

    value_fn: Callable[[SmartFrameCoordinator], bool | None]
    attrs_fn: Callable[[SmartFrameCoordinator], dict[str, Any]] | None = None
    # The engine connectivity sensor must keep reporting while the engine is unreachable.
    always_available: bool = False


def _frame_configured(coordinator: SmartFrameCoordinator) -> bool:
    return coordinator.data.status.frame.configured


BINARY_SENSORS: tuple[SmartFrameBinarySensorDescription, ...] = (
    SmartFrameBinarySensorDescription(
        key="engine_connected",
        translation_key="engine_connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        always_available=True,
        value_fn=lambda c: c.last_update_success,
    ),
    SmartFrameBinarySensorDescription(
        key="frame_online",
        translation_key="frame_online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        # ``telemetry.online`` is null until the frame was contacted once.
        value_fn=lambda c: c.data.status.frame.telemetry.online if _frame_configured(c) else None,
        attrs_fn=lambda c: {"last_error": c.data.status.frame.telemetry.last_error},
    ),
    SmartFrameBinarySensorDescription(
        key="frame_charging",
        translation_key="frame_charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.data.status.frame.telemetry.charging,
    ),
    SmartFrameBinarySensorDescription(
        key="delivery_pending",
        translation_key="delivery_pending",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.data.status.delivery.pending,
    ),
    SmartFrameBinarySensorDescription(
        key="ephemeral_active",
        translation_key="ephemeral_active",
        value_fn=lambda c: c.data.status.ephemeral.active,
        attrs_fn=lambda c: {
            "expires_at": c.data.status.ephemeral.expires_at,
            "title": c.data.status.ephemeral.title,
            "source": c.data.status.ephemeral.source,
        },
    ),
    SmartFrameBinarySensorDescription(
        key="quiet_hours",
        translation_key="quiet_hours",
        value_fn=lambda c: c.data.status.scheduler.in_quiet_hours,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmartFrameConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensors of one engine."""
    coordinator = entry.runtime_data
    async_add_entities(SmartFrameBinarySensor(coordinator, d) for d in BINARY_SENSORS)


class SmartFrameBinarySensor(SmartFrameEntity, BinarySensorEntity):
    """A binary state read from the coordinator snapshot."""

    entity_description: SmartFrameBinarySensorDescription

    def __init__(
        self,
        coordinator: SmartFrameCoordinator,
        description: SmartFrameBinarySensorDescription,
    ) -> None:
        """Initialise the binary sensor."""
        super().__init__(coordinator, description)

    @property
    def available(self) -> bool:
        """Engine connectivity stays available (it reports the outage itself)."""
        return self.entity_description.always_available or super().available

    @property
    def is_on(self) -> bool | None:
        """Return the state."""
        return self.entity_description.value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return grounded extra attributes, if the description defines any."""
        if self.entity_description.attrs_fn is None or not self.coordinator.last_update_success:
            return None
        return self.entity_description.attrs_fn(self.coordinator)
