"""Sensors: engine version, what is displayed, ambient context, delivery and frame state."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import AmbientState
from .const import CAPABILITY_AMBIENT_CONTEXT
from .coordinator import SmartFrameConfigEntry, SmartFrameData
from .entity import SmartFrameEntity

PARALLEL_UPDATES = 0

# ``scheduler.active_rule.source`` values enumerated by the contract.
REFRESH_RULE_SOURCES = ["pinned", "smart_mix", "last"]
MAX_STATE_LENGTH = 255

_AMBIENT_FIELDS = (
    "family_mode",
    "presence",
    "weather",
    "season",
    "boost_tags",
    "boost_people",
)


@dataclass(frozen=True, kw_only=True)
class SmartFrameSensorDescription(SensorEntityDescription):
    """Describe a sensor backed by the coordinator snapshot."""

    value_fn: Callable[[SmartFrameData], str | int | float | datetime | None]
    attrs_fn: Callable[[SmartFrameData], dict[str, Any]] | None = None
    # Only created when the engine advertises this capability flag.
    capability: str | None = None


def _ambient_fields(ambient: AmbientState) -> dict[str, Any]:
    """Flatten the active ambient fields: ``<field>`` and ``<field>_expires_at``."""
    attrs: dict[str, Any] = {}
    for name in _AMBIENT_FIELDS:
        timed = getattr(ambient, name)
        if timed is None:
            continue
        attrs[name] = timed.value if hasattr(timed, "value") else list(timed.values)
        attrs[f"{name}_expires_at"] = timed.expires_at
    return attrs


def _ambient_count(data: SmartFrameData) -> int | None:
    if data.ambient is None:
        return None
    return sum(getattr(data.ambient.context, name) is not None for name in _AMBIENT_FIELDS)


def _ambient_attrs(data: SmartFrameData) -> dict[str, Any]:
    if data.ambient is None:
        return {}
    return {
        **_ambient_fields(data.ambient.context),
        "family_active": data.ambient.family_active,
        "smart_mix_enabled": data.ambient.smart_mix_enabled,
        "extra_candidates": list(data.ambient.extra_candidates),
    }


def _image_attrs(data: SmartFrameData) -> dict[str, Any]:
    image = data.status.current_image
    if image is None:
        return {}
    return {
        "source": image.source,
        "template": image.template,
        "ephemeral": image.ephemeral,
        "pushed": image.pushed,
        "processed_at": image.processed_at,
        "expires_at": image.expires_at,
        # Provider metadata (date, location, people...): free-form, shown but not recorded.
        "details": dict(image.details),
    }


def _source(data: SmartFrameData) -> str | None:
    image = data.status.current_image
    return image.source if image else None


def _title(data: SmartFrameData) -> str | None:
    image = data.status.current_image
    if image is None or not image.title:
        return None
    return image.title[:MAX_STATE_LENGTH]


def _refresh_rule(data: SmartFrameData) -> str | None:
    source = data.status.scheduler.active_rule_source
    # The contract only ever adds enum values; an unknown one must not break the state.
    return source if source in REFRESH_RULE_SOURCES else None


def _last_delivery_at(data: SmartFrameData) -> datetime | None:
    last = data.status.delivery.last_delivery
    return last.delivered_at if last else None


def _last_delivery_attrs(data: SmartFrameData) -> dict[str, Any]:
    last = data.status.delivery.last_delivery
    if last is None:
        return {}
    return {
        "reason": last.reason,
        "duration_s": last.duration_s,
        "bytes": last.size_bytes,
    }


SENSORS: tuple[SmartFrameSensorDescription, ...] = (
    SmartFrameSensorDescription(
        key="engine_version",
        translation_key="engine_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.health.version,
    ),
    SmartFrameSensorDescription(
        key="active_source",
        translation_key="active_source",
        value_fn=_source,
        attrs_fn=lambda d: (
            {"template": d.status.current_image.template} if d.status.current_image else {}
        ),
    ),
    SmartFrameSensorDescription(
        key="refresh_rule",
        translation_key="refresh_rule",
        device_class=SensorDeviceClass.ENUM,
        options=REFRESH_RULE_SOURCES,
        value_fn=_refresh_rule,
        attrs_fn=lambda d: {"kind": d.status.scheduler.active_rule_kind},
    ),
    SmartFrameSensorDescription(
        key="media_title",
        translation_key="media_title",
        value_fn=_title,
        attrs_fn=_image_attrs,
    ),
    SmartFrameSensorDescription(
        key="ambient_context",
        translation_key="ambient_context",
        capability=CAPABILITY_AMBIENT_CONTEXT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_ambient_count,
        attrs_fn=_ambient_attrs,
    ),
    SmartFrameSensorDescription(
        key="last_delivery",
        translation_key="last_delivery",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_last_delivery_at,
        attrs_fn=_last_delivery_attrs,
    ),
    SmartFrameSensorDescription(
        key="frame_last_seen",
        translation_key="frame_last_seen",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.status.frame.telemetry.last_seen,
    ),
    SmartFrameSensorDescription(
        key="frame_battery",
        translation_key="frame_battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.status.frame.telemetry.battery_percent,
    ),
    SmartFrameSensorDescription(
        key="frame_wifi_signal",
        translation_key="frame_wifi_signal",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.status.frame.telemetry.rssi,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmartFrameConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors of one engine, skipping those the engine cannot back."""
    coordinator = entry.runtime_data
    async_add_entities(
        SmartFrameSensor(coordinator, description)
        for description in SENSORS
        if description.capability is None or description.capability in coordinator.capabilities
    )


class SmartFrameSensor(SmartFrameEntity, SensorEntity):
    """A value read from the coordinator snapshot."""

    entity_description: SmartFrameSensorDescription
    # The provider details can hold people and places: visible in the UI, never recorded.
    _unrecorded_attributes = frozenset({"details"})

    @property
    def native_value(self) -> str | int | float | datetime | None:
        """Return the value."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return grounded extra attributes, if the description defines any."""
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)
