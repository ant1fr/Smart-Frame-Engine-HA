"""Home Assistant services: ambient context and title cards.

Each service checks the engine's capability flag first and raises a translated
``ServiceValidationError`` when the connected engine does not support it (contract:
"A missing flag means this engine is older: hide or disable that feature").
"""

from __future__ import annotations

from typing import Any, Final

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from .api import AmbientField
from .const import CAPABILITY_AMBIENT_CONTEXT, CAPABILITY_TITLE_CARD, DOMAIN
from .coordinator import SmartFrameConfigEntry

SERVICE_SET_AMBIENT_CONTEXT: Final = "set_ambient_context"
SERVICE_CLEAR_AMBIENT_CONTEXT: Final = "clear_ambient_context"
SERVICE_SEND_TITLE_CARD: Final = "send_title_card"

ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"
ATTR_FIELD: Final = "field"

AMBIENT_FIELDS: Final = (
    "family_mode",
    "presence",
    "weather",
    "season",
    "boost_tags",
    "boost_people",
)
SEASONS: Final = ["spring", "summer", "autumn", "winter"]
ACCENTS: Final = ["red", "yellow", "blue", "green", "white"]

# Limits from openapi.json (AmbientRequest, TitleCardRequest).
MAX_LIST_ITEMS: Final = 20
MAX_TEXT: Final = 80
MAX_CARD_TEXT: Final = 200
MIN_MINUTES: Final = 1
MAX_MINUTES: Final = 10080

_minutes = vol.All(vol.Coerce(int), vol.Range(min=MIN_MINUTES, max=MAX_MINUTES))
_names = vol.All(cv.ensure_list, [cv.string], vol.Length(max=MAX_LIST_ITEMS))


def _schema(fields: dict[Any, Any]) -> Any:
    """Build a service schema (typed ``Any``: HA 2026.10 types schemas as ``probatio``)."""
    return vol.Schema({vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string, **fields})


SET_AMBIENT_CONTEXT_SCHEMA = _schema(
    {
        vol.Optional("family_mode"): cv.boolean,
        vol.Optional("presence"): _names,
        vol.Optional("weather"): vol.All(cv.string, vol.Length(max=MAX_TEXT)),
        vol.Optional("season"): vol.In(SEASONS),
        vol.Optional("boost_tags"): _names,
        vol.Optional("boost_people"): _names,
        vol.Optional("ttl_minutes"): _minutes,
    }
)
CLEAR_AMBIENT_CONTEXT_SCHEMA = _schema({vol.Optional(ATTR_FIELD): vol.In(AMBIENT_FIELDS)})
SEND_TITLE_CARD_SCHEMA = _schema(
    {
        vol.Required("title"): vol.All(cv.string, vol.Length(min=1, max=MAX_CARD_TEXT)),
        vol.Optional("subtitle"): vol.All(cv.string, vol.Length(max=MAX_CARD_TEXT)),
        vol.Optional("caption"): vol.All(cv.string, vol.Length(max=MAX_CARD_TEXT)),
        vol.Optional("accent"): vol.In(ACCENTS),
        vol.Optional("ephemeral"): cv.boolean,
        vol.Optional("ttl_minutes"): _minutes,
        vol.Optional("hold_minutes"): _minutes,
        vol.Optional("push_to_fraimic"): cv.boolean,
    }
)


@callback
def _async_resolve_entries(
    hass: HomeAssistant, call: ServiceCall, capability: str
) -> list[SmartFrameConfigEntry]:
    """Return the loaded engine this call targets, after checking its capability.

    ``config_entry_id`` may be omitted when exactly one engine is set up.
    """
    entries = hass.config_entries.async_entries(DOMAIN)
    if (entry_id := call.data.get(ATTR_CONFIG_ENTRY_ID)) is not None:
        entries = [entry for entry in entries if entry.entry_id == entry_id]
        if not entries:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="entry_not_found",
                translation_placeholders={"entry_id": str(entry_id)},
            )
    elif len(entries) > 1:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="config_entry_required"
        )

    loaded = [entry for entry in entries if entry.state is ConfigEntryState.LOADED]
    if not loaded:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_engine_loaded")

    for entry in loaded:
        if capability not in entry.runtime_data.capabilities:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="capability_not_supported",
                translation_placeholders={"capability": capability, "name": entry.title},
            )
    return loaded


async def _async_set_ambient_context(call: ServiceCall) -> None:
    """``POST /api/context``."""
    fields = {name: call.data[name] for name in AMBIENT_FIELDS if name in call.data}
    entries = _async_resolve_entries(call.hass, call, CAPABILITY_AMBIENT_CONTEXT)
    if not fields:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_context_fields")
    for entry in entries:
        coordinator = entry.runtime_data
        await coordinator.async_action(
            coordinator.client.async_set_context(ttl_minutes=call.data.get("ttl_minutes"), **fields)
        )
        await coordinator.async_request_refresh()


async def _async_clear_ambient_context(call: ServiceCall) -> None:
    """``DELETE /api/context`` (optionally ``?field=``)."""
    field: AmbientField | None = call.data.get(ATTR_FIELD)
    for entry in _async_resolve_entries(call.hass, call, CAPABILITY_AMBIENT_CONTEXT):
        coordinator = entry.runtime_data
        await coordinator.async_action(coordinator.client.async_clear_context(field))
        await coordinator.async_request_refresh()


async def _async_send_title_card(call: ServiceCall) -> None:
    """``POST /api/display/title-card``."""
    options = {
        name: call.data[name]
        for name in (
            "subtitle",
            "caption",
            "accent",
            "ephemeral",
            "ttl_minutes",
            "hold_minutes",
            "push_to_fraimic",
        )
        if name in call.data
    }
    for entry in _async_resolve_entries(call.hass, call, CAPABILITY_TITLE_CARD):
        coordinator = entry.runtime_data
        await coordinator.async_action(
            coordinator.client.async_title_card(call.data["title"], **options)
        )
        await coordinator.async_request_refresh()


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's services (once, from ``async_setup``)."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_AMBIENT_CONTEXT,
        _async_set_ambient_context,
        schema=SET_AMBIENT_CONTEXT_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEAR_AMBIENT_CONTEXT,
        _async_clear_ambient_context,
        schema=CLEAR_AMBIENT_CONTEXT_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_TITLE_CARD,
        _async_send_title_card,
        schema=SEND_TITLE_CARD_SCHEMA,
    )
