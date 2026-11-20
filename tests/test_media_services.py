"""Tests for the album cover and movie/series poster services."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.network import NoURLAvailableError
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
import voluptuous as vol

from custom_components.smart_frame_engine.const import (
    CAPABILITY_DISPLAY_TMDB,
    CAPABILITY_NOW_PLAYING,
    CAPABILITY_TITLE_CARD,
    DOMAIN,
)

from .common import API_KEY, BASE, OTHER_INSTANCE_ID, display_payload, make_entry, setup_engine

OTHER_BASE = "http://192.168.1.51:8080"
PLAYER = "media_player.living_room"
INTERNAL_URL = "http://homeassistant.local:8123"
NOW_PLAYING = f"{BASE}/api/display/now-playing"
TMDB = f"{BASE}/api/display/tmdb"


def _posts(aioclient_mock: AiohttpClientMocker, path: str) -> list[dict[str, Any]]:
    return [c[2] for c in aioclient_mock.mock_calls if c[0] == "POST" and c[1].path == path]


async def _call(hass: HomeAssistant, service: str, data: dict[str, Any] | None = None) -> None:
    await hass.services.async_call(DOMAIN, service, data or {}, blocking=True)


async def _internal_url(hass: HomeAssistant) -> None:
    await hass.config.async_update(internal_url=INTERNAL_URL)


def _music(**attributes: Any) -> dict[str, Any]:
    return {
        "media_artist": "Daft Punk",
        "media_album_name": "Discovery",
        "media_title": "One More Time",
        "media_duration": 320,
        "media_position": 20,
        "entity_picture": "/api/media_player_proxy/media_player.living_room?token=abc",
    } | attributes


def _video(**attributes: Any) -> dict[str, Any]:
    return {
        "media_content_type": "movie",
        "media_title": "Blade Runner",
        "entity_picture": "/api/media_player_proxy/media_player.tv?token=abc",
    } | attributes


@pytest.fixture
async def music_engine(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """An engine that shows covers, and an internal URL for the pictures."""
    await setup_engine(hass, aioclient_mock, api_key=API_KEY, auth="key")
    aioclient_mock.post(NOW_PLAYING, json=display_payload())
    aioclient_mock.post(TMDB, json=display_payload())
    await _internal_url(hass)


async def test_media_services_are_registered(hass: HomeAssistant) -> None:
    """Both services exist as soon as the integration is loaded."""
    assert await async_setup_component(hass, DOMAIN, {})
    assert hass.services.has_service(DOMAIN, "show_now_playing")
    assert hass.services.has_service(DOMAIN, "show_media_poster")


# -- show_now_playing ------------------------------------------------------------------


@pytest.mark.usefixtures("music_engine")
async def test_now_playing_from_a_media_player(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Artist, album, title, duration, position and the picture come from the player."""
    hass.states.async_set(PLAYER, "paused", _music())
    await _call(hass, "show_now_playing", {"media_player": PLAYER})

    (body,) = _posts(aioclient_mock, "/api/display/now-playing")
    assert body == {
        "artist": "Daft Punk",
        "album": "Discovery",
        "title": "One More Time",
        "duration_seconds": 320.0,
        "position_seconds": 20.0,
        # The relative picture is made absolute with the internal URL.
        "artwork_url": f"{INTERNAL_URL}/api/media_player_proxy/media_player.living_room?token=abc",
    }
    call = [c for c in aioclient_mock.mock_calls if c[1].path == "/api/display/now-playing"][-1]
    assert call[3]["X-API-Key"] == API_KEY


@pytest.mark.usefixtures("music_engine")
async def test_now_playing_position_advances_while_playing(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A playing track's reported position is topped up with the time since it was reported."""
    freezer.move_to(datetime(2026, 11, 20, 12, 0, 30, tzinfo=UTC))
    updated = datetime(2026, 11, 20, 12, 0, 0, tzinfo=UTC)
    hass.states.async_set(PLAYER, "playing", _music(media_position_updated_at=updated))

    await _call(hass, "show_now_playing", {"media_player": PLAYER})

    assert _posts(aioclient_mock, "/api/display/now-playing")[-1]["position_seconds"] == 50.0


@pytest.mark.usefixtures("music_engine")
async def test_now_playing_position_is_not_advanced_when_paused_or_past_the_end(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Paused players keep their position; a stale one never exceeds the track length."""
    freezer.move_to(datetime(2026, 11, 20, 12, 0, 30, tzinfo=UTC))
    updated = datetime(2026, 11, 20, 12, 0, 0, tzinfo=UTC)
    hass.states.async_set(PLAYER, "paused", _music(media_position_updated_at=updated))
    await _call(hass, "show_now_playing", {"media_player": PLAYER})
    assert _posts(aioclient_mock, "/api/display/now-playing")[-1]["position_seconds"] == 20.0

    hass.states.async_set(
        PLAYER, "playing", _music(media_position=319, media_position_updated_at=updated)
    )
    await _call(hass, "show_now_playing", {"media_player": PLAYER})
    assert _posts(aioclient_mock, "/api/display/now-playing")[-1]["position_seconds"] == 320.0


@pytest.mark.usefixtures("music_engine")
async def test_now_playing_explicit_values_win(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """What the call sets is sent as is; the player only fills the gaps."""
    hass.states.async_set(PLAYER, "playing", _music())
    await _call(
        hass,
        "show_now_playing",
        {
            "media_player": PLAYER,
            "artist": "Justice",
            "album": "Cross",
            "title": "D.A.N.C.E.",
            "artwork_url": "http://cdn.example/cover.jpg",
            "duration_seconds": 200,
            "position_seconds": 5,
        },
    )
    body = _posts(aioclient_mock, "/api/display/now-playing")[-1]
    assert body == {
        "artist": "Justice",
        "album": "Cross",
        "title": "D.A.N.C.E.",
        "artwork_url": "http://cdn.example/cover.jpg",
        "duration_seconds": 200.0,
        "position_seconds": 5.0,
    }


@pytest.mark.usefixtures("music_engine")
async def test_now_playing_without_a_media_player_sends_only_what_is_given(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The contract's own fields work on their own, with every option forwarded."""
    await _call(
        hass,
        "show_now_playing",
        {
            "artist": "Air",
            "enrich_artwork": False,
            "ephemeral": False,
            "hold_minutes": 30,
            "push_to_fraimic": False,
            "show_title": True,
            "style": "vinyl",
            "ttl_seconds": 600,
        },
    )
    assert _posts(aioclient_mock, "/api/display/now-playing")[-1] == {
        "artist": "Air",
        "enrich_artwork": False,
        "ephemeral": False,
        "hold_minutes": 30,
        "push_to_fraimic": False,
        "show_title": True,
        "style": "vinyl",
        "ttl_seconds": 600,
    }


@pytest.mark.usefixtures("music_engine")
async def test_now_playing_artist_only(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Only the artist is required."""
    await _call(hass, "show_now_playing", {"artist": "Air"})
    assert _posts(aioclient_mock, "/api/display/now-playing")[-1] == {"artist": "Air"}


@pytest.mark.usefixtures("music_engine")
async def test_now_playing_can_leave_the_cover_to_the_engine(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """use_media_player_picture: false sends no cover, so the engine's lookup applies."""
    hass.states.async_set(PLAYER, "playing", _music())
    await _call(
        hass, "show_now_playing", {"media_player": PLAYER, "use_media_player_picture": False}
    )
    body = _posts(aioclient_mock, "/api/display/now-playing")[-1]
    assert "artwork_url" not in body
    assert body["artist"] == "Daft Punk"


@pytest.mark.parametrize(
    ("picture", "expected"),
    [
        ("https://cdn.example/a.jpg", "https://cdn.example/a.jpg"),
        ("/local/cover.jpg", f"{INTERNAL_URL}/local/cover.jpg"),
        ("data:image/png;base64,AAAA", None),
        ("cover.jpg", None),
        ("/" + "x" * 4096, None),
        ("", None),
        (None, None),
    ],
    ids=["absolute", "relative", "data uri", "no scheme", "too long", "empty", "missing"],
)
@pytest.mark.usefixtures("music_engine")
async def test_now_playing_picture_handling(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    picture: str | None,
    expected: str | None,
) -> None:
    """Only a picture the engine can fetch is sent; the rest is left out, never an error."""
    hass.states.async_set(PLAYER, "playing", _music(entity_picture=picture))
    await _call(hass, "show_now_playing", {"media_player": PLAYER})
    body = _posts(aioclient_mock, "/api/display/now-playing")[-1]
    assert body.get("artwork_url") == expected


async def test_now_playing_without_an_internal_url_sends_no_cover(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A relative picture cannot be made absolute without an internal URL: the engine looks."""
    await setup_engine(hass, aioclient_mock)
    aioclient_mock.post(NOW_PLAYING, json=display_payload())
    hass.states.async_set(PLAYER, "playing", _music())
    with patch(
        "custom_components.smart_frame_engine.services.get_url", side_effect=NoURLAvailableError
    ):
        await _call(hass, "show_now_playing", {"media_player": PLAYER})
    body = _posts(aioclient_mock, "/api/display/now-playing")[-1]
    assert "artwork_url" not in body
    assert body["artist"] == "Daft Punk"


@pytest.mark.usefixtures("music_engine")
async def test_now_playing_ignores_unusable_attributes(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Blank text and out-of-range or non-numeric numbers are left out, long text is cut."""
    hass.states.async_set(
        PLAYER,
        "playing",
        _music(
            media_artist="  " + "a" * 400,
            media_album_name="   ",
            media_title=None,
            media_duration=True,
            media_position=-5,
            entity_picture=None,
        ),
    )
    await _call(hass, "show_now_playing", {"media_player": PLAYER})
    assert _posts(aioclient_mock, "/api/display/now-playing")[-1] == {"artist": "a" * 300}


@pytest.mark.parametrize(
    "attributes", [{}, {"media_artist": ""}, {"media_artist": None}, {"media_artist": 12}]
)
@pytest.mark.usefixtures("music_engine")
async def test_now_playing_needs_an_artist(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, attributes: dict[str, Any]
) -> None:
    """No artist (from the call or the player) means nothing is sent."""
    hass.states.async_set(PLAYER, "playing", attributes)
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "show_now_playing", {"media_player": PLAYER})
    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "missing_artist"
    assert not _posts(aioclient_mock, "/api/display/now-playing")


@pytest.mark.usefixtures("music_engine")
async def test_now_playing_without_artist_or_player(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Neither an artist nor a player is the same error."""
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "show_now_playing", {"album": "Discovery"})
    assert err.value.translation_key == "missing_artist"


@pytest.mark.parametrize("service", ["show_now_playing", "show_media_poster"])
@pytest.mark.usefixtures("music_engine")
async def test_unknown_media_player(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, service: str
) -> None:
    """A media player that does not exist is reported with its id."""
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, service, {"media_player": "media_player.ghost"})
    assert err.value.translation_key == "media_player_not_found"
    assert err.value.translation_placeholders == {"entity_id": "media_player.ghost"}
    assert not [c for c in aioclient_mock.mock_calls if c[0] == "POST"]


@pytest.mark.parametrize(
    ("service", "data"),
    [
        ("show_now_playing", {"media_player": "light.kitchen"}),
        ("show_media_poster", {"media_player": "light.kitchen"}),
    ],
)
@pytest.mark.usefixtures("music_engine")
async def test_media_player_must_be_a_media_player(
    hass: HomeAssistant, service: str, data: dict[str, Any]
) -> None:
    """Other domains are refused by the schema."""
    with pytest.raises(vol.Invalid):
        await _call(hass, service, data)


@pytest.mark.parametrize(
    "data",
    [
        {"artist": ""},
        {"artist": "x" * 301},
        {"artist": "a", "album": "x" * 301},
        {"artist": "a", "title": "x" * 301},
        {"artist": "a", "artwork_url": "x" * 4097},
        {"artist": "a", "duration_seconds": -1},
        {"artist": "a", "duration_seconds": 86401},
        {"artist": "a", "position_seconds": 86401},
        {"artist": "a", "hold_minutes": 0},
        {"artist": "a", "style": "cassette"},
        {"artist": "a", "ttl_seconds": 9},
        {"artist": "a", "ttl_seconds": 86401},
        {"artist": "a", "is_locked": True},
    ],
    ids=[
        "empty artist",
        "artist 301",
        "album 301",
        "title 301",
        "url 4097",
        "duration -1",
        "duration big",
        "position big",
        "hold 0",
        "style",
        "ttl 9",
        "ttl big",
        "unknown field",
    ],
)
@pytest.mark.usefixtures("music_engine")
async def test_now_playing_schema_limits(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, data: dict[str, Any]
) -> None:
    """The limits of NowPlayingRequest are enforced before any request; no invented fields."""
    with pytest.raises(vol.Invalid):
        await _call(hass, "show_now_playing", data)
    assert not _posts(aioclient_mock, "/api/display/now-playing")


@pytest.mark.usefixtures("music_engine")
async def test_now_playing_accepts_the_bounds(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The inclusive limits are valid."""
    await _call(
        hass,
        "show_now_playing",
        {
            "artist": "a" * 300,
            "album": "b" * 300,
            "title": "c" * 300,
            "artwork_url": "u" * 4096,
            "duration_seconds": 86400,
            "position_seconds": 0,
            "ttl_seconds": 10,
            "hold_minutes": 10080,
        },
    )
    assert len(_posts(aioclient_mock, "/api/display/now-playing")) == 1


# -- show_media_poster -----------------------------------------------------------------


@pytest.mark.usefixtures("music_engine")
async def test_poster_for_a_movie_player(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A movie: its title, type movie, and the picture as the TMDB fallback."""
    hass.states.async_set("media_player.tv", "playing", _video())
    await _call(hass, "show_media_poster", {"media_player": "media_player.tv"})
    assert _posts(aioclient_mock, "/api/display/tmdb")[-1] == {
        "query": "Blade Runner",
        "media_type": "movie",
        "fallback_url": f"{INTERNAL_URL}/api/media_player_proxy/media_player.tv?token=abc",
    }


@pytest.mark.usefixtures("music_engine")
async def test_poster_for_an_episode_uses_the_series(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An episode: the series title (not the episode's), type tv, and its season."""
    hass.states.async_set(
        "media_player.tv",
        "playing",
        _video(
            media_content_type="episode",
            media_title="Pilot",
            media_series_title="Severance",
            media_season=2,
        ),
    )
    await _call(hass, "show_media_poster", {"media_player": "media_player.tv"})
    body = _posts(aioclient_mock, "/api/display/tmdb")[-1]
    assert (body["query"], body["media_type"], body["season"]) == ("Severance", "tv", 2)


@pytest.mark.parametrize(
    ("content_type", "media_type"),
    [("movie", "movie"), ("tvshow", "tv"), ("episode", "tv"), ("video", None), (None, None)],
)
@pytest.mark.usefixtures("music_engine")
async def test_poster_media_type_mapping(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    content_type: str | None,
    media_type: str | None,
) -> None:
    """Only the documented content types name a TMDB type; otherwise the engine searches both."""
    hass.states.async_set(
        "media_player.tv", "playing", _video(media_content_type=content_type, media_season=3)
    )
    await _call(hass, "show_media_poster", {"media_player": "media_player.tv"})
    body = _posts(aioclient_mock, "/api/display/tmdb")[-1]
    assert body.get("media_type") == media_type
    # A season only makes sense for a series.
    assert ("season" in body) is (media_type == "tv")


@pytest.mark.usefixtures("music_engine")
async def test_poster_explicit_values_win(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The call's own values replace the ones read from the player."""
    hass.states.async_set(
        "media_player.tv",
        "playing",
        _video(media_content_type="episode", media_series_title="Severance", media_season=2),
    )
    await _call(
        hass,
        "show_media_poster",
        {
            "media_player": "media_player.tv",
            "query": "Dark",
            "media_type": "multi",
            "season": 1,
            "fallback_url": "http://cdn.example/p.jpg",
        },
    )
    body = _posts(aioclient_mock, "/api/display/tmdb")[-1]
    assert body == {
        "query": "Dark",
        "media_type": "multi",
        "season": 1,
        "fallback_url": "http://cdn.example/p.jpg",
    }


@pytest.mark.usefixtures("music_engine")
async def test_poster_without_a_media_player_forwards_every_option(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The contract's own fields work on their own."""
    await _call(
        hass,
        "show_media_poster",
        {
            "query": "Dune",
            "media_type": "movie",
            "season": 0,
            "year": 2021,
            "language": "fr-CA",
            "fallback_url": "http://cdn.example/p.jpg",
            "ephemeral": False,
            "ttl_minutes": 1440,
            "hold_minutes": 10080,
            "push_to_fraimic": False,
        },
    )
    assert _posts(aioclient_mock, "/api/display/tmdb")[-1] == {
        "query": "Dune",
        "media_type": "movie",
        "season": 0,
        "year": 2021,
        "language": "fr-CA",
        "fallback_url": "http://cdn.example/p.jpg",
        "ephemeral": False,
        "ttl_minutes": 1440,
        "hold_minutes": 10080,
        "push_to_fraimic": False,
    }


@pytest.mark.usefixtures("music_engine")
async def test_poster_picture_fallback_can_be_turned_off(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """use_media_player_picture: false sends no fallback picture."""
    hass.states.async_set("media_player.tv", "playing", _video())
    await _call(
        hass,
        "show_media_poster",
        {"media_player": "media_player.tv", "use_media_player_picture": False},
    )
    assert "fallback_url" not in _posts(aioclient_mock, "/api/display/tmdb")[-1]


@pytest.mark.parametrize(
    "attributes",
    [{}, {"media_title": ""}, {"media_title": None}, {"media_series_title": "  "}],
)
@pytest.mark.usefixtures("music_engine")
async def test_poster_needs_a_title(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, attributes: dict[str, Any]
) -> None:
    """No title (from the call or the player) means nothing is sent."""
    hass.states.async_set("media_player.tv", "playing", attributes)
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "show_media_poster", {"media_player": "media_player.tv"})
    assert err.value.translation_key == "missing_query"
    assert not _posts(aioclient_mock, "/api/display/tmdb")


@pytest.mark.parametrize(
    "data",
    [
        {"query": ""},
        {"query": "x" * 201},
        {"query": "a", "media_type": "anime"},
        {"query": "a", "season": 101},
        {"query": "a", "season": -1},
        {"query": "a", "year": 1869},
        {"query": "a", "year": 2201},
        {"query": "a", "ttl_minutes": 0},
        {"query": "a", "ttl_minutes": 1441},
        {"query": "a", "hold_minutes": 10081},
        {"query": "a", "fallback_url": "x" * 4097},
        {"query": "a", "accent": "red"},
    ],
    ids=[
        "empty query",
        "query 201",
        "media type",
        "season 101",
        "season -1",
        "year 1869",
        "year 2201",
        "ttl 0",
        "ttl 1441 (max is 1440, not 10080)",
        "hold 10081",
        "url 4097",
        "unknown field",
    ],
)
@pytest.mark.usefixtures("music_engine")
async def test_poster_schema_limits(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, data: dict[str, Any]
) -> None:
    """The limits of TmdbDisplayRequest are enforced before any request."""
    with pytest.raises(vol.Invalid):
        await _call(hass, "show_media_poster", data)
    assert not _posts(aioclient_mock, "/api/display/tmdb")


@pytest.mark.usefixtures("music_engine")
async def test_poster_accepts_the_bounds(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The inclusive limits are valid."""
    await _call(
        hass,
        "show_media_poster",
        {"query": "q" * 200, "season": 100, "year": 1870, "ttl_minutes": 1, "hold_minutes": 1},
    )
    await _call(hass, "show_media_poster", {"query": "q", "season": 0, "year": 2200})
    assert len(_posts(aioclient_mock, "/api/display/tmdb")) == 2


# -- Capability gating, targeting and errors -------------------------------------------


@pytest.mark.parametrize(
    ("service", "data", "missing"),
    [
        ("show_now_playing", {"artist": "Air"}, CAPABILITY_NOW_PLAYING),
        ("show_media_poster", {"query": "Dune"}, CAPABILITY_DISPLAY_TMDB),
    ],
)
async def test_unsupported_capability_raises_before_any_request(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    service: str,
    data: dict[str, Any],
    missing: str,
) -> None:
    """An engine without the capability gets a translated ServiceValidationError."""
    entry = await setup_engine(hass, aioclient_mock, capabilities={CAPABILITY_TITLE_CARD})
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, service, data)
    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "capability_not_supported"
    assert err.value.translation_placeholders == {"capability": missing, "name": entry.title}
    assert not [c for c in aioclient_mock.mock_calls if c[0] == "POST"]


async def test_each_service_needs_its_own_capability(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """now_playing does not unlock the poster service, and display_tmdb not the cover one."""
    await setup_engine(hass, aioclient_mock, capabilities={CAPABILITY_NOW_PLAYING})
    aioclient_mock.post(NOW_PLAYING, json=display_payload())
    await _call(hass, "show_now_playing", {"artist": "Air"})
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "show_media_poster", {"query": "Dune"})
    assert err.value.translation_placeholders is not None
    assert err.value.translation_placeholders["capability"] == CAPABILITY_DISPLAY_TMDB


async def test_several_engines_need_an_explicit_target(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """With two engines the call says which one; only that one is used."""
    await setup_engine(hass, aioclient_mock)
    second = await setup_engine(
        hass,
        aioclient_mock,
        base=OTHER_BASE,
        instance_id=OTHER_INSTANCE_ID,
        entry=make_entry(unique_id=OTHER_INSTANCE_ID, host="192.168.1.51"),
    )
    aioclient_mock.post(NOW_PLAYING, json=display_payload())
    aioclient_mock.post(f"{OTHER_BASE}/api/display/now-playing", json=display_payload())

    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "show_now_playing", {"artist": "Air"})
    assert err.value.translation_key == "config_entry_required"

    await _call(hass, "show_now_playing", {"artist": "Air", "config_entry_id": second.entry_id})
    posts = [c for c in aioclient_mock.mock_calls if c[0] == "POST"]
    assert [str(c[1].host) for c in posts] == ["192.168.1.51"]


@pytest.mark.parametrize(
    ("service", "data", "path"),
    [
        ("show_now_playing", {"artist": "Air"}, "/api/display/now-playing"),
        ("show_media_poster", {"query": "Dune"}, "/api/display/tmdb"),
    ],
)
async def test_engine_422_is_a_validation_error(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    service: str,
    data: dict[str, Any],
    path: str,
) -> None:
    """A request the engine rejects (e.g. an unknown person) is invalid input, with its message."""
    await setup_engine(hass, aioclient_mock)
    aioclient_mock.post(f"{BASE}{path}", status=422, json={"detail": "bad request"})
    with pytest.raises(ServiceValidationError, match="bad request") as err:
        await _call(hass, service, data)
    assert err.value.translation_key == "request_rejected"


@pytest.mark.parametrize(
    ("service", "data", "path", "status"),
    [
        ("show_now_playing", {"artist": "Air"}, "/api/display/now-playing", 502),
        ("show_media_poster", {"query": "Dune"}, "/api/display/tmdb", 404),
        ("show_media_poster", {"query": "Dune"}, "/api/display/tmdb", 409),
        ("show_media_poster", {"query": "Dune"}, "/api/display/tmdb", 502),
        ("show_media_poster", {"query": "Dune"}, "/api/display/tmdb", 504),
    ],
)
async def test_engine_failures_are_home_assistant_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    service: str,
    data: dict[str, Any],
    path: str,
    status: int,
) -> None:
    """404 (no poster), 409 (TMDB not configured), 502 and 504 carry the engine's message."""
    await setup_engine(hass, aioclient_mock)
    aioclient_mock.post(f"{BASE}{path}", status=status, json={"detail": "engine said no"})
    with pytest.raises(HomeAssistantError, match="engine said no") as err:
        await _call(hass, service, data)
    assert not isinstance(err.value, ServiceValidationError)


async def test_unreachable_engine(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Network failures are reported."""
    await setup_engine(hass, aioclient_mock)
    aioclient_mock.post(NOW_PLAYING, exc=TimeoutError)
    with pytest.raises(HomeAssistantError):
        await _call(hass, "show_now_playing", {"artist": "Air"})


async def test_rejected_key_starts_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 401 starts reauthentication."""
    await setup_engine(hass, aioclient_mock, api_key=API_KEY, auth="key")
    aioclient_mock.post(TMDB, status=401, json={"detail": "no"})
    with pytest.raises(HomeAssistantError):
        await _call(hass, "show_media_poster", {"query": "Dune"})
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_services_refresh_the_engine_state(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """After showing something the coordinator is refreshed (the image entity follows)."""
    await setup_engine(hass, aioclient_mock)
    aioclient_mock.post(NOW_PLAYING, json=display_payload())
    before = sum(c[1].path == "/api/status" for c in aioclient_mock.mock_calls)
    await _call(hass, "show_now_playing", {"artist": "Air"})
    assert sum(c[1].path == "/api/status" for c in aioclient_mock.mock_calls) == before + 1
