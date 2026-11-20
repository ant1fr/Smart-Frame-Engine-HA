"""Home Assistant services: ambient context, title cards, album covers and posters.

Each service checks the engine's capability flag first and raises a translated
``ServiceValidationError`` when the connected engine does not support it (contract:
"A missing flag means this engine is older: hide or disable that feature").
"""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any, Final

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, State, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .api import AmbientField
from .const import (
    CAPABILITY_AMBIENT_CONTEXT,
    CAPABILITY_DISPLAY_TMDB,
    CAPABILITY_NOW_PLAYING,
    CAPABILITY_TITLE_CARD,
    DOMAIN,
)
from .coordinator import SmartFrameConfigEntry

_LOGGER = logging.getLogger(__name__)

SERVICE_SET_AMBIENT_CONTEXT: Final = "set_ambient_context"
SERVICE_CLEAR_AMBIENT_CONTEXT: Final = "clear_ambient_context"
SERVICE_SEND_TITLE_CARD: Final = "send_title_card"
SERVICE_SHOW_NOW_PLAYING: Final = "show_now_playing"
SERVICE_SHOW_MEDIA_POSTER: Final = "show_media_poster"

ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"
ATTR_FIELD: Final = "field"
ATTR_MEDIA_PLAYER: Final = "media_player"
ATTR_USE_PICTURE: Final = "use_media_player_picture"

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
MUSIC_STYLES: Final = ["sleeve", "vinyl"]
TMDB_MEDIA_TYPES: Final = ["movie", "tv", "multi"]

# Limits from openapi.json (AmbientRequest, TitleCardRequest).
MAX_LIST_ITEMS: Final = 20
MAX_TEXT: Final = 80
MAX_CARD_TEXT: Final = 200
MIN_MINUTES: Final = 1
MAX_MINUTES: Final = 10080
# Limits from openapi.json (NowPlayingRequest, TmdbDisplayRequest).
MAX_MUSIC_TEXT: Final = 300
MAX_URL: Final = 4096
MAX_TRACK_SECONDS: Final = 86400
MIN_TTL_SECONDS: Final = 10
MAX_TMDB_QUERY: Final = 200
MAX_TMDB_TTL_MINUTES: Final = 1440
MAX_SEASON: Final = 100
MIN_YEAR: Final = 1870
MAX_YEAR: Final = 2200

_minutes = vol.All(vol.Coerce(int), vol.Range(min=MIN_MINUTES, max=MAX_MINUTES))
_names = vol.All(cv.ensure_list, [cv.string], vol.Length(max=MAX_LIST_ITEMS))
_track_seconds = vol.All(vol.Coerce(float), vol.Range(min=0, max=MAX_TRACK_SECONDS))
_url = vol.All(cv.string, vol.Length(max=MAX_URL))


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
SHOW_NOW_PLAYING_SCHEMA = _schema(
    {
        vol.Optional(ATTR_MEDIA_PLAYER): cv.entity_domain("media_player"),
        vol.Optional(ATTR_USE_PICTURE): cv.boolean,
        vol.Optional("artist"): vol.All(cv.string, vol.Length(min=1, max=MAX_MUSIC_TEXT)),
        vol.Optional("album"): vol.All(cv.string, vol.Length(max=MAX_MUSIC_TEXT)),
        vol.Optional("title"): vol.All(cv.string, vol.Length(max=MAX_MUSIC_TEXT)),
        vol.Optional("artwork_url"): _url,
        vol.Optional("duration_seconds"): _track_seconds,
        vol.Optional("position_seconds"): _track_seconds,
        vol.Optional("enrich_artwork"): cv.boolean,
        vol.Optional("ephemeral"): cv.boolean,
        vol.Optional("hold_minutes"): _minutes,
        vol.Optional("push_to_fraimic"): cv.boolean,
        vol.Optional("show_title"): cv.boolean,
        vol.Optional("style"): vol.In(MUSIC_STYLES),
        vol.Optional("ttl_seconds"): vol.All(
            vol.Coerce(int), vol.Range(min=MIN_TTL_SECONDS, max=MAX_TRACK_SECONDS)
        ),
    }
)
SHOW_MEDIA_POSTER_SCHEMA = _schema(
    {
        vol.Optional(ATTR_MEDIA_PLAYER): cv.entity_domain("media_player"),
        vol.Optional(ATTR_USE_PICTURE): cv.boolean,
        vol.Optional("query"): vol.All(cv.string, vol.Length(min=1, max=MAX_TMDB_QUERY)),
        vol.Optional("media_type"): vol.In(TMDB_MEDIA_TYPES),
        vol.Optional("season"): vol.All(vol.Coerce(int), vol.Range(min=0, max=MAX_SEASON)),
        vol.Optional("year"): vol.All(vol.Coerce(int), vol.Range(min=MIN_YEAR, max=MAX_YEAR)),
        vol.Optional("language"): cv.string,
        vol.Optional("fallback_url"): _url,
        vol.Optional("ephemeral"): cv.boolean,
        vol.Optional("ttl_minutes"): vol.All(
            vol.Coerce(int), vol.Range(min=MIN_MINUTES, max=MAX_TMDB_TTL_MINUTES)
        ),
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


# -- media_player helpers ---------------------------------------------------------------


def _text(value: Any, limit: int) -> str | None:
    """A trimmed, length-capped string attribute, or ``None`` when empty or not text."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] or None


def _number(value: Any, maximum: float) -> float | None:
    """A non-negative number within ``maximum`` (booleans are not numbers), else ``None``."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if 0 <= value <= maximum else None


def _player(hass: HomeAssistant, entity_id: str) -> State:
    """The state of the ``media_player`` entity a call refers to."""
    if (state := hass.states.get(entity_id)) is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="media_player_not_found",
            translation_placeholders={"entity_id": entity_id},
        )
    return state


def _picture_url(hass: HomeAssistant, state: State) -> str | None:
    """The player's picture as an absolute URL the engine can fetch, when there is one.

    ``entity_picture`` is usually a path relative to Home Assistant
    (``/api/media_player_proxy/...``). It is made absolute with the internal URL; when none
    is configured the picture is left out (and the engine looks the artwork up itself).
    """
    picture = _text(state.attributes.get("entity_picture"), MAX_URL + 1)
    if picture is None:
        return None
    if picture.startswith(("http://", "https://")):
        url = picture
    elif picture.startswith("/"):
        try:
            url = f"{get_url(hass, allow_external=False, allow_cloud=False)}{picture}"
        except NoURLAvailableError:
            _LOGGER.debug("No internal URL to make the picture of %s absolute", state.entity_id)
            return None
    else:
        return None
    return url if len(url) <= MAX_URL else None


def _live_position(state: State) -> float | None:
    """Playback position now: the reported one plus the time elapsed while playing."""
    position = _number(state.attributes.get("media_position"), MAX_TRACK_SECONDS)
    if position is None:
        return None
    updated_at = state.attributes.get("media_position_updated_at")
    if state.state == "playing" and isinstance(updated_at, datetime):
        position += max((dt_util.utcnow() - updated_at).total_seconds(), 0.0)
    duration = _number(state.attributes.get("media_duration"), MAX_TRACK_SECONDS)
    return round(min(position, duration if duration is not None else MAX_TRACK_SECONDS), 1)


def _passthrough(call: ServiceCall, names: tuple[str, ...]) -> dict[str, Any]:
    """The optional fields of a call that go to the engine unchanged."""
    return {name: call.data[name] for name in names if name in call.data}


def _async_player_of(call: ServiceCall) -> State | None:
    """The ``media_player`` the call names, if any."""
    if ATTR_MEDIA_PLAYER not in call.data:
        return None
    return _player(call.hass, call.data[ATTR_MEDIA_PLAYER])


async def _async_show_now_playing(call: ServiceCall) -> None:
    """``POST /api/display/now-playing``."""
    entries = _async_resolve_entries(call.hass, call, CAPABILITY_NOW_PLAYING)
    state = _async_player_of(call)

    # Values given in the call win; the media_player only fills what is missing.
    values: dict[str, Any] = {}
    if state is not None:
        attrs = state.attributes
        values = {
            "artist": _text(attrs.get("media_artist"), MAX_MUSIC_TEXT),
            "album": _text(attrs.get("media_album_name"), MAX_MUSIC_TEXT),
            "title": _text(attrs.get("media_title"), MAX_MUSIC_TEXT),
            "duration_seconds": _number(attrs.get("media_duration"), MAX_TRACK_SECONDS),
            "position_seconds": _live_position(state),
        }
        if call.data.get(ATTR_USE_PICTURE, True):
            values["artwork_url"] = _picture_url(call.hass, state)
    values.update(
        _passthrough(
            call,
            ("artist", "album", "title", "artwork_url", "duration_seconds", "position_seconds"),
        )
    )

    if not values.get("artist"):
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="missing_artist")
    artist = values.pop("artist")
    options = values | _passthrough(
        call,
        (
            "enrich_artwork",
            "ephemeral",
            "hold_minutes",
            "push_to_fraimic",
            "show_title",
            "style",
            "ttl_seconds",
        ),
    )
    for entry in entries:
        coordinator = entry.runtime_data
        await coordinator.async_action(coordinator.client.async_now_playing(artist, **options))
        await coordinator.async_request_refresh()


# ``media_content_type`` values of Home Assistant's ``MediaType`` that name a TMDB media type.
_TMDB_TYPE_BY_CONTENT_TYPE: Final = {"movie": "movie", "tvshow": "tv", "episode": "tv"}


async def _async_show_media_poster(call: ServiceCall) -> None:
    """``POST /api/display/tmdb``."""
    entries = _async_resolve_entries(call.hass, call, CAPABILITY_DISPLAY_TMDB)
    state = _async_player_of(call)

    values: dict[str, Any] = {}
    if state is not None:
        attrs = state.attributes
        media_type = _TMDB_TYPE_BY_CONTENT_TYPE.get(str(attrs.get("media_content_type")))
        values = {
            "query": _text(attrs.get("media_series_title"), MAX_TMDB_QUERY)
            or _text(attrs.get("media_title"), MAX_TMDB_QUERY),
            "media_type": media_type,
        }
        season = _number(attrs.get("media_season"), MAX_SEASON)
        if media_type == "tv" and season is not None:
            values["season"] = int(season)
        if call.data.get(ATTR_USE_PICTURE, True):
            values["fallback_url"] = _picture_url(call.hass, state)
    values.update(_passthrough(call, ("query", "media_type", "season", "fallback_url")))

    if not values.get("query"):
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="missing_query")
    query = values.pop("query")
    options = values | _passthrough(
        call,
        ("year", "language", "ephemeral", "ttl_minutes", "hold_minutes", "push_to_fraimic"),
    )
    for entry in entries:
        coordinator = entry.runtime_data
        await coordinator.async_action(coordinator.client.async_display_tmdb(query, **options))
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
    hass.services.async_register(
        DOMAIN,
        SERVICE_SHOW_NOW_PLAYING,
        _async_show_now_playing,
        schema=SHOW_NOW_PLAYING_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SHOW_MEDIA_POSTER,
        _async_show_media_poster,
        schema=SHOW_MEDIA_POSTER_SCHEMA,
    )
