"""Tests for the automation blueprints.

The blueprints are loaded with Home Assistant's own blueprint models, then run as real
automations whose ``smart_frame_engine`` services are replaced by recorders that validate the
call with the integration's real service schemas.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import shutil
from typing import Any
from urllib.parse import quote

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components import automation
from homeassistant.components.blueprint import models
from homeassistant.components.blueprint.schemas import BLUEPRINT_SCHEMA
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.setup import async_setup_component
from homeassistant.util.yaml import load_yaml
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed
import yaml

from custom_components.smart_frame_engine import services
from custom_components.smart_frame_engine.const import DOMAIN

ROOT = Path(__file__).resolve().parents[1]
BLUEPRINTS = ROOT / "blueprints" / "automation" / "smart_frame_engine"
BLUEPRINT_FILES = sorted(BLUEPRINTS.glob("*.yaml"))
SOURCE_URL = "https://github.com/ant1fr/Smart-Frame-Engine-HA/blob/main/blueprints/automation/smart_frame_engine/"

SCHEMAS = {
    services.SERVICE_SET_AMBIENT_CONTEXT: services.SET_AMBIENT_CONTEXT_SCHEMA,
    services.SERVICE_CLEAR_AMBIENT_CONTEXT: services.CLEAR_AMBIENT_CONTEXT_SCHEMA,
    services.SERVICE_SEND_TITLE_CARD: services.SEND_TITLE_CARD_SCHEMA,
    services.SERVICE_SHOW_NOW_PLAYING: services.SHOW_NOW_PLAYING_SCHEMA,
    services.SERVICE_SHOW_MEDIA_POSTER: services.SHOW_MEDIA_POSTER_SCHEMA,
}


# -- Static checks ---------------------------------------------------------------------


def test_there_are_five_blueprints() -> None:
    """Weather, presence, title card, music and TV."""
    assert [path.name for path in BLUEPRINT_FILES] == [
        "ambient_presence.yaml",
        "ambient_weather.yaml",
        "now_playing_cover.yaml",
        "title_card.yaml",
        "tv_poster.yaml",
    ]


@pytest.mark.parametrize("path", BLUEPRINT_FILES, ids=lambda p: p.stem)
def test_blueprint_is_valid_and_documented(path: Path) -> None:
    """Home Assistant accepts it, and it carries what an importable blueprint needs."""
    blueprint = models.Blueprint(
        load_yaml(str(path)), expected_domain="automation", schema=BLUEPRINT_SCHEMA
    )
    meta = blueprint.metadata

    assert meta["name"].startswith("Smart Frame Engine - ")
    assert meta["description"]
    assert meta["domain"] == "automation"
    assert meta["source_url"] == f"{SOURCE_URL}{path.name}"
    hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
    assert meta["homeassistant"]["min_version"] == hacs["homeassistant"]
    assert blueprint.validate() is None
    for name, definition in blueprint.inputs.items():
        assert definition["name"], name
        assert definition["selector"], name


@pytest.mark.parametrize("path", BLUEPRINT_FILES, ids=lambda p: p.stem)
def test_blueprint_only_uses_existing_services_and_fields(path: Path) -> None:
    """Every service and every field sent exists in services.yaml and in the real schema."""
    definitions = yaml.safe_load(
        (ROOT / "custom_components" / "smart_frame_engine" / "services.yaml").read_text(
            encoding="utf-8"
        )
    )
    data = load_yaml(str(path))
    calls = [
        step for step in data["actions"] if str(step.get("action", "")).startswith(f"{DOMAIN}.")
    ]
    assert calls, "the blueprint calls no smart_frame_engine service"
    for step in calls:
        service = step["action"].split(".", 1)[1]
        assert service in definitions
        sent = set(re.findall(r"'(\w+)':", step["data"]))
        assert sent
        assert sent <= {str(key) for key in SCHEMAS[service].schema}, (service, sent)
        assert sent <= set(definitions[service]["fields"]), (service, sent)


def test_readme_lists_every_blueprint() -> None:
    """Each blueprint has its import link in the README."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for path in BLUEPRINT_FILES:
        link = "blueprint_url=" + quote(f"{SOURCE_URL}{path.name}", safe="")
        assert link in readme, path.name


# -- Running the blueprints ------------------------------------------------------------


@pytest.fixture
async def calls(
    hass: HomeAssistant, tmp_path: Path
) -> AsyncGenerator[list[tuple[str, dict[str, Any]]]]:
    """Install the blueprints, and record every ``smart_frame_engine`` service call."""
    shutil.copytree(BLUEPRINTS, tmp_path / "blueprints" / "automation" / "smart_frame_engine")
    hass.config.config_dir = str(tmp_path)

    recorded: list[tuple[str, dict[str, Any]]] = []

    def _recorder(name: str) -> Any:
        async def _record(call: ServiceCall) -> None:
            recorded.append((name, dict(call.data)))

        return _record

    for name, schema in SCHEMAS.items():
        hass.services.async_register(DOMAIN, name, _recorder(name), schema=schema)
    yield recorded

    # Stop the automations: their time triggers would otherwise outlive the test.
    for entity_id in hass.states.async_entity_ids(automation.DOMAIN):
        await hass.services.async_call(
            automation.DOMAIN, "turn_off", {"entity_id": entity_id}, blocking=True
        )


async def _setup(hass: HomeAssistant, blueprint: str, inputs: dict[str, Any]) -> None:
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "alias": f"test {blueprint}",
                "use_blueprint": {
                    "path": f"smart_frame_engine/{blueprint}.yaml",
                    "input": inputs,
                },
            }
        },
    )
    await hass.async_block_till_done()
    assert hass.states.async_entity_ids(automation.DOMAIN), "the automation was not created"


async def _set(
    hass: HomeAssistant, entity_id: str, state: str, attributes: dict[str, Any] | None = None
) -> None:
    hass.states.async_set(entity_id, state, attributes or {})
    await hass.async_block_till_done()


# -- Weather ---------------------------------------------------------------------------

WEATHER = "weather.home"


async def test_weather_follows_the_entity(
    hass: HomeAssistant, calls: list[tuple[str, dict[str, Any]]]
) -> None:
    """A new condition is sent with the default lifetime and no engine."""
    await _set(hass, WEATHER, "sunny", {"temperature": 20})
    await _setup(hass, "ambient_weather", {"weather_entity": WEATHER})

    await _set(hass, WEATHER, "rainy", {"temperature": 20})

    assert calls == [("set_ambient_context", {"weather": "rainy", "ttl_minutes": 180})]


async def test_weather_ignores_attribute_changes(
    hass: HomeAssistant, calls: list[tuple[str, dict[str, Any]]]
) -> None:
    """Temperature and the like change all the time: only the condition counts."""
    await _set(hass, WEATHER, "sunny", {"temperature": 20})
    await _setup(hass, "ambient_weather", {"weather_entity": WEATHER})

    await _set(hass, WEATHER, "sunny", {"temperature": 21})

    assert calls == []


@pytest.mark.parametrize("state", ["unknown", "unavailable"])
async def test_weather_ignores_unusable_states(
    hass: HomeAssistant, calls: list[tuple[str, dict[str, Any]]], state: str
) -> None:
    """The engine is never told that the weather is 'unavailable'."""
    await _set(hass, WEATHER, "sunny")
    await _setup(hass, "ambient_weather", {"weather_entity": WEATHER})

    await _set(hass, WEATHER, state)

    assert calls == []


async def test_weather_inputs(hass: HomeAssistant, calls: list[tuple[str, dict[str, Any]]]) -> None:
    """The engine and the lifetime are forwarded."""
    await _set(hass, WEATHER, "sunny")
    await _setup(
        hass,
        "ambient_weather",
        {"weather_entity": WEATHER, "ttl_minutes": 90, "engine": "abc123"},
    )

    await _set(hass, WEATHER, "snowy")

    assert calls == [
        (
            "set_ambient_context",
            {"config_entry_id": "abc123", "weather": "snowy", "ttl_minutes": 90},
        )
    ]


async def test_weather_is_sent_at_startup(
    hass: HomeAssistant, calls: list[tuple[str, dict[str, Any]]]
) -> None:
    """When Home Assistant starts, the current weather is sent."""
    await _set(hass, WEATHER, "cloudy")
    await _setup(hass, "ambient_weather", {"weather_entity": WEATHER})

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()

    assert calls == [("set_ambient_context", {"weather": "cloudy", "ttl_minutes": 180})]


async def test_weather_is_renewed_every_half_hour(
    hass: HomeAssistant,
    calls: list[tuple[str, dict[str, Any]]],
    freezer: FrozenDateTimeFactory,
) -> None:
    """The value is re-sent so it does not expire while the weather stays the same."""
    freezer.move_to(datetime(2026, 11, 20, 12, 5, 0, tzinfo=UTC))
    await _set(hass, WEATHER, "cloudy")
    await _setup(hass, "ambient_weather", {"weather_entity": WEATHER})

    async_fire_time_changed(hass, datetime(2026, 11, 20, 12, 15, 0, tzinfo=UTC))
    await hass.async_block_till_done()
    assert calls == []

    async_fire_time_changed(hass, datetime(2026, 11, 20, 12, 30, 1, tzinfo=UTC))
    await hass.async_block_till_done()
    assert calls == [("set_ambient_context", {"weather": "cloudy", "ttl_minutes": 180})]


# -- Presence --------------------------------------------------------------------------


def _persons(hass: HomeAssistant, **states: str) -> list[str]:
    """Create person entities named after their key; returns their entity ids."""
    entity_ids = []
    for name, state in states.items():
        entity_id = f"person.{name}"
        hass.states.async_set(entity_id, state, {"friendly_name": name.capitalize()})
        entity_ids.append(entity_id)
    return entity_ids


async def test_presence_lists_the_people_at_home(
    hass: HomeAssistant, calls: list[tuple[str, dict[str, Any]]]
) -> None:
    """Names of the people at home, in the order they were chosen."""
    people = _persons(hass, alice="home", bob="not_home", carol="home")
    await _setup(hass, "ambient_presence", {"persons": people})

    await _set(hass, "person.bob", "home", {"friendly_name": "Bob"})

    assert calls == [
        ("set_ambient_context", {"presence": ["Alice", "Bob", "Carol"], "ttl_minutes": 180})
    ]


async def test_presence_is_emptied_when_everyone_leaves(
    hass: HomeAssistant, calls: list[tuple[str, dict[str, Any]]]
) -> None:
    """An empty list is sent (the engine clears the field)."""
    people = _persons(hass, alice="home")
    await _setup(hass, "ambient_presence", {"persons": people})

    await _set(hass, "person.alice", "not_home", {"friendly_name": "Alice"})

    assert calls == [("set_ambient_context", {"presence": [], "ttl_minutes": 180})]


async def test_presence_follows_a_single_person(
    hass: HomeAssistant, calls: list[tuple[str, dict[str, Any]]]
) -> None:
    """One person arriving gives a one-element list; other zones count as away."""
    people = _persons(hass, alice="work")
    await _setup(hass, "ambient_presence", {"persons": people})

    await _set(hass, "person.alice", "home", {"friendly_name": "Alice"})

    assert calls == [("set_ambient_context", {"presence": ["Alice"], "ttl_minutes": 180})]


async def test_presence_respects_the_engine_limits(
    hass: HomeAssistant, calls: list[tuple[str, dict[str, Any]]]
) -> None:
    """At most 20 names, each cut to 80 characters (the schema would refuse more)."""
    people = []
    for index in range(25):
        entity_id = f"person.p{index:02}"
        name = ("x" * 90) if index == 0 else f"Person {index}"
        hass.states.async_set(entity_id, "home", {"friendly_name": name})
        people.append(entity_id)
    await _setup(hass, "ambient_presence", {"persons": people})

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()

    ((_, data),) = calls
    assert len(data["presence"]) == 20
    assert data["presence"][0] == "x" * 80
    assert data["presence"][1] == "Person 1"


async def test_presence_forwards_the_engine_and_lifetime(
    hass: HomeAssistant, calls: list[tuple[str, dict[str, Any]]]
) -> None:
    """The engine and the lifetime are forwarded."""
    people = _persons(hass, alice="home")
    await _setup(
        hass,
        "ambient_presence",
        {"persons": people, "engine": "abc123", "ttl_minutes": 600},
    )

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()

    assert calls == [
        (
            "set_ambient_context",
            {"config_entry_id": "abc123", "presence": ["Alice"], "ttl_minutes": 600},
        )
    ]


async def test_presence_ignores_attribute_changes(
    hass: HomeAssistant, calls: list[tuple[str, dict[str, Any]]]
) -> None:
    """Person entities update their coordinates constantly: that is not an arrival."""
    people = _persons(hass, alice="home")
    await _setup(hass, "ambient_presence", {"persons": people})

    await _set(hass, "person.alice", "home", {"friendly_name": "Alice", "latitude": 1.5})

    assert calls == []


async def test_presence_is_renewed_every_half_hour(
    hass: HomeAssistant,
    calls: list[tuple[str, dict[str, Any]]],
    freezer: FrozenDateTimeFactory,
) -> None:
    """The list is re-sent so it does not expire while nothing changes."""
    freezer.move_to(datetime(2026, 11, 20, 12, 5, 0, tzinfo=UTC))
    people = _persons(hass, alice="home")
    await _setup(hass, "ambient_presence", {"persons": people})

    async_fire_time_changed(hass, datetime(2026, 11, 20, 12, 15, 0, tzinfo=UTC))
    await hass.async_block_till_done()
    assert calls == []

    async_fire_time_changed(hass, datetime(2026, 11, 20, 12, 30, 1, tzinfo=UTC))
    await hass.async_block_till_done()
    assert calls == [("set_ambient_context", {"presence": ["Alice"], "ttl_minutes": 180})]


# -- Title card ------------------------------------------------------------------------

DOOR = "input_boolean.front_door"
DOOR_TRIGGER = [{"trigger": "state", "entity_id": DOOR, "to": "on"}]


async def _open_door(hass: HomeAssistant) -> None:
    await _set(hass, DOOR, "off", {"friendly_name": "Front door"})
    await _set(hass, DOOR, "on", {"friendly_name": "Front door"})


async def test_title_card_with_the_defaults(
    hass: HomeAssistant, calls: list[tuple[str, dict[str, Any]]]
) -> None:
    """Only the title is required; empty texts are left out."""
    await _set(hass, DOOR, "off")
    await _setup(hass, "title_card", {"trigger": DOOR_TRIGGER, "title": "Welcome"})

    await _set(hass, DOOR, "on")

    assert calls == [
        (
            "send_title_card",
            {"title": "Welcome", "accent": "red", "ephemeral": True, "ttl_minutes": 60},
        )
    ]


async def test_title_card_templates_use_the_trigger(
    hass: HomeAssistant, calls: list[tuple[str, dict[str, Any]]]
) -> None:
    """Title, subtitle and caption are rendered with the trigger's data."""
    await _set(hass, DOOR, "off", {"friendly_name": "Front door"})
    await _setup(
        hass,
        "title_card",
        {
            "trigger": DOOR_TRIGGER,
            "title": "{{ trigger.to_state.name }} is open",
            "subtitle": "since {{ trigger.to_state.state }}",
            "caption": "check it",
        },
    )

    await _set(hass, DOOR, "on", {"friendly_name": "Front door"})

    ((name, data),) = calls
    assert name == "send_title_card"
    assert data["title"] == "Front door is open"
    assert data["subtitle"] == "since on"
    assert data["caption"] == "check it"


async def test_title_card_every_input(
    hass: HomeAssistant, calls: list[tuple[str, dict[str, Any]]]
) -> None:
    """All inputs reach the service."""
    await _set(hass, DOOR, "off")
    await _setup(
        hass,
        "title_card",
        {
            "trigger": DOOR_TRIGGER,
            "title": "Dinner",
            "subtitle": "is ready",
            "caption": "7 pm",
            "accent": "green",
            "ephemeral": True,
            "ttl_minutes": 15,
            "hold_minutes": 30,
            "engine": "abc123",
        },
    )

    await _set(hass, DOOR, "on")

    assert calls == [
        (
            "send_title_card",
            {
                "config_entry_id": "abc123",
                "title": "Dinner",
                "subtitle": "is ready",
                "caption": "7 pm",
                "accent": "green",
                "ephemeral": True,
                "ttl_minutes": 15,
                "hold_minutes": 30,
            },
        )
    ]


async def test_title_card_that_is_not_temporary_sends_no_duration(
    hass: HomeAssistant, calls: list[tuple[str, dict[str, Any]]]
) -> None:
    """The duration only applies to temporary cards."""
    await _set(hass, DOOR, "off")
    await _setup(
        hass,
        "title_card",
        {"trigger": DOOR_TRIGGER, "title": "Sticky", "ephemeral": False, "ttl_minutes": 15},
    )

    await _set(hass, DOOR, "on")

    assert calls == [("send_title_card", {"title": "Sticky", "accent": "red", "ephemeral": False})]


async def test_title_card_texts_are_cut_to_the_engine_limit(
    hass: HomeAssistant, calls: list[tuple[str, dict[str, Any]]]
) -> None:
    """A long templated text is cut to 200 characters instead of failing."""
    await _set(hass, DOOR, "off")
    await _setup(
        hass,
        "title_card",
        {
            "trigger": DOOR_TRIGGER,
            "title": "t" * 250,
            "subtitle": "s" * 250,
            "caption": "c" * 250,
        },
    )

    await _set(hass, DOOR, "on")

    ((_, data),) = calls
    assert (len(data["title"]), len(data["subtitle"]), len(data["caption"])) == (200, 200, 200)


async def test_title_card_accepts_any_trigger(
    hass: HomeAssistant, calls: list[tuple[str, dict[str, Any]]]
) -> None:
    """The trigger input is whatever the user picks, here an event."""
    await _setup(
        hass,
        "title_card",
        {"trigger": [{"trigger": "event", "event_type": "doorbell"}], "title": "Ding dong"},
    )

    hass.bus.async_fire("doorbell")
    await hass.async_block_till_done()

    assert [name for name, _ in calls] == ["send_title_card"]


# -- Music -----------------------------------------------------------------------------

SPEAKER = "media_player.kitchen"
SONG = {
    "media_artist": "Daft Punk",
    "media_album_name": "Discovery",
    "media_title": "One More Time",
    "media_duration": 320,
    "media_position": 0,
}
SHOW_COVER = ("show_now_playing", {"media_player": SPEAKER, "use_media_player_picture": True})


async def test_music_shows_the_cover_when_playback_starts(
    hass: HomeAssistant, calls: list[tuple[str, dict[str, Any]]]
) -> None:
    """The player is passed on: the service reads the track from it."""
    await _set(hass, SPEAKER, "idle")
    await _setup(hass, "now_playing_cover", {"media_player": SPEAKER})

    await _set(hass, SPEAKER, "playing", SONG)

    assert calls[-1] == SHOW_COVER


async def test_music_follows_track_changes(
    hass: HomeAssistant, calls: list[tuple[str, dict[str, Any]]]
) -> None:
    """A new title while playing shows the new track."""
    await _set(hass, SPEAKER, "playing", SONG)
    await _setup(hass, "now_playing_cover", {"media_player": SPEAKER})

    await _set(hass, SPEAKER, "playing", SONG | {"media_title": "Aerodynamic"})

    assert calls == [SHOW_COVER]


async def test_music_ignores_position_updates(
    hass: HomeAssistant, calls: list[tuple[str, dict[str, Any]]]
) -> None:
    """The position changes every few seconds: that must not call the engine."""
    await _set(hass, SPEAKER, "playing", SONG)
    await _setup(hass, "now_playing_cover", {"media_player": SPEAKER})

    for position in (5, 10, 15):
        await _set(hass, SPEAKER, "playing", SONG | {"media_position": position})

    assert calls == []


@pytest.mark.parametrize("state", ["paused", "idle", "off", "unavailable"])
async def test_music_only_shows_while_playing(
    hass: HomeAssistant, calls: list[tuple[str, dict[str, Any]]], state: str
) -> None:
    """A title change on a player that is not playing shows nothing."""
    await _set(hass, SPEAKER, state, SONG)
    await _setup(hass, "now_playing_cover", {"media_player": SPEAKER})

    await _set(hass, SPEAKER, state, SONG | {"media_title": "Aerodynamic"})

    assert calls == []


@pytest.mark.parametrize("artist", [None, "", "   "])
async def test_music_needs_an_artist(
    hass: HomeAssistant, calls: list[tuple[str, dict[str, Any]]], artist: str | None
) -> None:
    """A stream that reports no artist is skipped (the service would refuse it)."""
    await _set(hass, SPEAKER, "idle")
    await _setup(hass, "now_playing_cover", {"media_player": SPEAKER})

    await _set(hass, SPEAKER, "playing", SONG | {"media_artist": artist})

    assert calls == []


async def test_music_can_leave_the_cover_to_the_engine(
    hass: HomeAssistant, calls: list[tuple[str, dict[str, Any]]]
) -> None:
    """With 'send the player's cover' off, the engine looks the cover up itself."""
    await _set(hass, SPEAKER, "idle")
    await _setup(hass, "now_playing_cover", {"media_player": SPEAKER, "send_artwork": False})

    await _set(hass, SPEAKER, "playing", SONG)

    assert calls[-1] == (
        "show_now_playing",
        {"media_player": SPEAKER, "use_media_player_picture": False},
    )


@pytest.mark.parametrize(("show_title", "expected"), [("", None), ("yes", True), ("no", False)])
async def test_music_show_title_choice(
    hass: HomeAssistant,
    calls: list[tuple[str, dict[str, Any]]],
    show_title: str,
    expected: bool | None,
) -> None:
    """Engine default, yes and no; 'no' is sent as false, not dropped."""
    await _set(hass, SPEAKER, "idle")
    await _setup(hass, "now_playing_cover", {"media_player": SPEAKER, "show_title": show_title})

    await _set(hass, SPEAKER, "playing", SONG)

    data = calls[-1][1]
    if expected is None:
        assert "show_title" not in data
    else:
        assert data["show_title"] is expected


async def test_music_every_input(
    hass: HomeAssistant, calls: list[tuple[str, dict[str, Any]]]
) -> None:
    """Style, protection, duration and engine are forwarded."""
    await _set(hass, SPEAKER, "idle")
    await _setup(
        hass,
        "now_playing_cover",
        {
            "media_player": SPEAKER,
            "style": "vinyl",
            "show_title": "yes",
            "hold_minutes": 20,
            "ttl_seconds": 240,
            "engine": "abc123",
        },
    )

    await _set(hass, SPEAKER, "playing", SONG)

    assert calls[-1] == (
        "show_now_playing",
        {
            "config_entry_id": "abc123",
            "media_player": SPEAKER,
            "use_media_player_picture": True,
            "style": "vinyl",
            "show_title": True,
            "hold_minutes": 20,
            "ttl_seconds": 240,
        },
    )


# -- TV --------------------------------------------------------------------------------

TV = "media_player.living_room_tv"
MOVIE = {"media_content_type": "movie", "media_title": "Blade Runner"}
SHOW_POSTER = ("show_media_poster", {"media_player": TV, "use_media_player_picture": True})


async def test_tv_shows_the_poster_of_a_movie(
    hass: HomeAssistant, calls: list[tuple[str, dict[str, Any]]]
) -> None:
    """The player is passed on: the service reads the title from it."""
    await _set(hass, TV, "idle")
    await _setup(hass, "tv_poster", {"media_player": TV})

    await _set(hass, TV, "playing", MOVIE)

    assert calls[-1] == SHOW_POSTER


@pytest.mark.parametrize("content_type", ["movie", "tvshow", "episode"])
async def test_tv_accepts_movies_series_and_episodes(
    hass: HomeAssistant, calls: list[tuple[str, dict[str, Any]]], content_type: str
) -> None:
    """These three content types are shown."""
    await _set(hass, TV, "idle")
    await _setup(hass, "tv_poster", {"media_player": TV})

    await _set(hass, TV, "playing", {"media_content_type": content_type, "media_title": "X"})

    assert calls[-1] == SHOW_POSTER


@pytest.mark.parametrize("content_type", ["music", "video", "channel", "app", None])
async def test_tv_ignores_other_content(
    hass: HomeAssistant, calls: list[tuple[str, dict[str, Any]]], content_type: str | None
) -> None:
    """Music, channels, apps and untyped video are not looked up on TMDB."""
    await _set(hass, TV, "idle")
    await _setup(hass, "tv_poster", {"media_player": TV})

    await _set(hass, TV, "playing", {"media_content_type": content_type, "media_title": "X"})

    assert calls == []


async def test_tv_follows_title_changes(
    hass: HomeAssistant, calls: list[tuple[str, dict[str, Any]]]
) -> None:
    """The next episode or film shows its poster."""
    await _set(hass, TV, "playing", MOVIE)
    await _setup(hass, "tv_poster", {"media_player": TV})

    await _set(hass, TV, "playing", MOVIE | {"media_title": "Dune"})

    assert calls == [SHOW_POSTER]


async def test_tv_ignores_position_updates(
    hass: HomeAssistant, calls: list[tuple[str, dict[str, Any]]]
) -> None:
    """Playback progress does not call the engine."""
    await _set(hass, TV, "playing", MOVIE)
    await _setup(hass, "tv_poster", {"media_player": TV})

    await _set(hass, TV, "playing", MOVIE | {"media_position": 30})

    assert calls == []


@pytest.mark.parametrize("state", ["paused", "idle", "off"])
async def test_tv_only_shows_while_playing(
    hass: HomeAssistant, calls: list[tuple[str, dict[str, Any]]], state: str
) -> None:
    """A title change while not playing shows nothing."""
    await _set(hass, TV, state, MOVIE)
    await _setup(hass, "tv_poster", {"media_player": TV})

    await _set(hass, TV, state, MOVIE | {"media_title": "Dune"})

    assert calls == []


async def test_tv_needs_a_title(
    hass: HomeAssistant, calls: list[tuple[str, dict[str, Any]]]
) -> None:
    """A player that reports no title has nothing to look up."""
    await _set(hass, TV, "idle")
    await _setup(hass, "tv_poster", {"media_player": TV})

    await _set(hass, TV, "playing", {"media_content_type": "movie", "media_title": " "})

    assert calls == []


async def test_tv_series_title_is_enough(
    hass: HomeAssistant, calls: list[tuple[str, dict[str, Any]]]
) -> None:
    """An episode without its own title still has its series."""
    await _set(hass, TV, "idle")
    await _setup(hass, "tv_poster", {"media_player": TV})

    await _set(
        hass,
        TV,
        "playing",
        {"media_content_type": "episode", "media_series_title": "Severance"},
    )

    assert calls[-1] == SHOW_POSTER


async def test_tv_can_skip_the_fallback_picture(
    hass: HomeAssistant, calls: list[tuple[str, dict[str, Any]]]
) -> None:
    """The player's picture is not sent when the fallback is turned off."""
    await _set(hass, TV, "idle")
    await _setup(hass, "tv_poster", {"media_player": TV, "use_media_player_picture": False})

    await _set(hass, TV, "playing", MOVIE)

    assert calls[-1] == (
        "show_media_poster",
        {"media_player": TV, "use_media_player_picture": False},
    )


async def test_tv_every_input(hass: HomeAssistant, calls: list[tuple[str, dict[str, Any]]]) -> None:
    """Language (trimmed), duration, protection and engine are forwarded."""
    await _set(hass, TV, "idle")
    await _setup(
        hass,
        "tv_poster",
        {
            "media_player": TV,
            "language": " fr-CA ",
            "ttl_minutes": 120,
            "hold_minutes": 10,
            "engine": "abc123",
        },
    )

    await _set(hass, TV, "playing", MOVIE)

    assert calls[-1] == (
        "show_media_poster",
        {
            "config_entry_id": "abc123",
            "media_player": TV,
            "use_media_player_picture": True,
            "language": "fr-CA",
            "ttl_minutes": 120,
            "hold_minutes": 10,
        },
    )


async def test_music_a_new_track_that_also_starts_playback_is_not_sent_twice(
    hass: HomeAssistant, calls: list[tuple[str, dict[str, Any]]]
) -> None:
    """Starting playback with a new title fires both triggers; the second run replaces the first."""
    await _set(hass, SPEAKER, "idle", SONG)
    await _setup(hass, "now_playing_cover", {"media_player": SPEAKER})

    await _set(hass, SPEAKER, "playing", SONG | {"media_title": "Aerodynamic"})

    assert calls == [SHOW_COVER]


async def test_tv_a_new_title_that_also_starts_playback_is_not_sent_twice(
    hass: HomeAssistant, calls: list[tuple[str, dict[str, Any]]]
) -> None:
    """Starting playback with a new title fires both triggers; it is counted once."""
    await _set(hass, TV, "idle", MOVIE)
    await _setup(hass, "tv_poster", {"media_player": TV})

    await _set(hass, TV, "playing", MOVIE | {"media_title": "Dune"})

    assert calls == [SHOW_POSTER]
