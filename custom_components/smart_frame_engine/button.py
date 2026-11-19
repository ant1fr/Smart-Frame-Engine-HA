"""Buttons: force a refresh, wake the frame."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import SmartFrameClient
from .const import CAPABILITY_FORCE_REFRESH, CAPABILITY_FRAME_WAKEUP
from .coordinator import SmartFrameConfigEntry
from .entity import SmartFrameEntity

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class SmartFrameButtonDescription(ButtonEntityDescription):
    """Describe a button that calls one engine endpoint."""

    capability: str
    press_fn: Callable[[SmartFrameClient], Awaitable[Any]]


BUTTONS: tuple[SmartFrameButtonDescription, ...] = (
    SmartFrameButtonDescription(
        key="force_refresh",
        translation_key="force_refresh",
        capability=CAPABILITY_FORCE_REFRESH,
        press_fn=lambda client: client.async_refresh(),
    ),
    SmartFrameButtonDescription(
        key="wake_frame",
        translation_key="wake_frame",
        capability=CAPABILITY_FRAME_WAKEUP,
        # Non-blocking: the engine answers 202 and uploads in the background.
        press_fn=lambda client: client.async_wakeup(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmartFrameConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the buttons the engine supports."""
    coordinator = entry.runtime_data
    async_add_entities(
        SmartFrameButton(coordinator, description)
        for description in BUTTONS
        if description.capability in coordinator.capabilities
    )


class SmartFrameButton(SmartFrameEntity, ButtonEntity):
    """A button that triggers an action on the engine."""

    entity_description: SmartFrameButtonDescription

    async def async_press(self) -> None:
        """Call the endpoint, then refresh so the new state shows up."""
        coordinator = self.coordinator
        await coordinator.async_action(self.entity_description.press_fn(coordinator.client))
        await coordinator.async_request_refresh()
