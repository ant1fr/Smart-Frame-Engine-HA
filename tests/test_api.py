"""Tests for the API client (api.py)."""

from __future__ import annotations

from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.smart_frame_engine.api import (
    HealthInfo,
    SmartFrameApiError,
    SmartFrameAuthError,
    SmartFrameClient,
    SmartFrameConnectionError,
    SmartFrameIncompatibleError,
    SmartFrameValidationError,
    WithTtl,
    compatibility_target,
)
from custom_components.smart_frame_engine.const import ALL_CAPABILITIES, SUPPORTED_API_VERSION

from .common import (
    API_KEY,
    BASE,
    HOST,
    PORT,
    config_payload,
    context_payload,
    delivery_payload,
    display_payload,
    health_payload,
    status_payload,
)


def _client(hass: HomeAssistant, api_key: str | None = None, path: str = "/") -> SmartFrameClient:
    return SmartFrameClient(async_get_clientsession(hass), HOST, PORT, path, api_key)


def _last_call(mock: AiohttpClientMocker) -> tuple[str, Any, Any, Any]:
    method, url, data, headers = mock.mock_calls[-1]
    return method, url, data, headers


# -- Constants -------------------------------------------------------------------------


def test_supported_version_and_capability_flags() -> None:
    """The client speaks contract 1 and knows all 19 flags of API_CONTRACT.md."""
    assert SUPPORTED_API_VERSION == 1
    assert len(ALL_CAPABILITIES) == 19
    assert {
        "activity_log",
        "ambient_context",
        "display_immich",
        "display_smart_search",
        "display_tmdb",
        "ephemeral_images",
        "family_mode",
        "force_refresh",
        "frame_push",
        "frame_wakeup",
        "now_playing",
        "now_playing_dedup",
        "playground",
        "random_discovery",
        "smart_mix",
        "smart_mix_context",
        "smart_mix_kinds",
        "title_card",
        "upload_image",
    } == ALL_CAPABILITIES


# -- Compatibility handshake -----------------------------------------------------------


@pytest.mark.parametrize(
    ("api_version", "min_api_version", "expected"),
    [
        (1, 1, None),  # exactly our version
        (2, 1, None),  # newer engine still serving contract 1
        (5, 1, None),
        (0, 0, "server"),  # engine older than the integration
        (2, 2, "integration"),  # engine dropped contract 1
        (3, 2, "integration"),
    ],
)
def test_compatibility_rule(api_version: int, min_api_version: int, expected: str | None) -> None:
    """Compatible iff min_api_version <= 1 <= api_version; the target says who updates."""
    assert compatibility_target(api_version, min_api_version) == expected
    health = HealthInfo.from_dict(
        health_payload(api_version=api_version, min_api_version=min_api_version)
    )
    assert health.is_compatible is (expected is None)
    if expected is None:
        health.ensure_compatible()
    else:
        with pytest.raises(SmartFrameIncompatibleError) as err:
            health.ensure_compatible()
        assert err.value.update_target == expected


# -- Health ----------------------------------------------------------------------------


async def test_health_parsing(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Health is parsed; unknown fields and unknown capability flags are tolerated."""
    payload = health_payload(auth="key", capabilities={"title_card", "flag_from_the_future"})
    payload["brand_new_field"] = {"anything": True}
    aioclient_mock.get(f"{BASE}/api/health", json=payload)

    health = await _client(hass).async_get_health()

    assert health.version == "1.0.0rc3"
    assert health.api_version == 1
    assert health.min_api_version == 1
    assert health.auth == "key"
    assert health.requires_key is True
    assert health.capabilities == {"title_card", "flag_from_the_future"}
    assert health.instance_id == "3f2b8c1e-5d0a-4c7e-9b61-0a1d2e3f4a5b"
    assert health.instance_name == "Smart Frame Engine 3f2b8c"
    assert health.as_dict()["capabilities"] == ["flag_from_the_future", "title_card"]


async def test_health_without_instance_id(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An engine older than 1.0.0rc3 simply has no instance id."""
    aioclient_mock.get(
        f"{BASE}/api/health", json=health_payload(instance_id=None, instance_name=None)
    )
    health = await _client(hass).async_get_health()
    assert health.instance_id is None
    assert health.instance_name is None


@pytest.mark.parametrize("body", [["not", "an", "object"], {"status": "ok"}, {"version": 1}])
async def test_health_rejects_non_engines(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, body: Any
) -> None:
    """Anything that does not look like an engine is a connection error."""
    aioclient_mock.get(f"{BASE}/api/health", json=body)
    with pytest.raises(SmartFrameConnectionError):
        await _client(hass).async_get_health()


async def test_health_invalid_json(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A web server answering HTML is not an engine."""
    aioclient_mock.get(f"{BASE}/api/health", text="<html>hello</html>")
    with pytest.raises(SmartFrameConnectionError):
        await _client(hass).async_get_health()


# -- Auth modes ------------------------------------------------------------------------


@pytest.mark.parametrize("auth", ["open", "key", "password"])
async def test_health_needs_no_credentials_in_any_mode(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, auth: str
) -> None:
    """GET /api/health works without a key whatever the auth mode."""
    aioclient_mock.get(f"{BASE}/api/health", json=health_payload(auth=auth))
    health = await _client(hass).async_get_health()
    assert health.auth == auth
    assert health.requires_key is (auth != "open")
    assert "X-API-Key" not in _last_call(aioclient_mock)[3]


async def test_key_is_sent_as_x_api_key_header(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The key goes in the X-API-Key header (never in the URL)."""
    aioclient_mock.get(f"{BASE}/api/status", json=status_payload())
    await _client(hass, API_KEY).async_get_status()
    _, url, _, headers = _last_call(aioclient_mock)
    assert headers["X-API-Key"] == API_KEY
    assert API_KEY not in str(url)


async def test_no_header_in_open_mode(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Without a configured key, no credential header is sent."""
    aioclient_mock.get(f"{BASE}/api/status", json=status_payload())
    await _client(hass).async_get_status()
    assert "X-API-Key" not in _last_call(aioclient_mock)[3]


async def test_validate_key_uses_key_gated_endpoint(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """/api/status is public in key mode, so key validation hits /api/config."""
    aioclient_mock.get(f"{BASE}/api/config", json=config_payload())
    await _client(hass, API_KEY).async_validate_key()
    method, url, _, headers = _last_call(aioclient_mock)
    assert (method, url.path) == ("GET", "/api/config")
    assert headers["X-API-Key"] == API_KEY


@pytest.mark.parametrize("method_name", ["async_get_status", "async_validate_key", "async_refresh"])
async def test_401_is_an_auth_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, method_name: str
) -> None:
    """HTTP 401 becomes SmartFrameAuthError."""
    body = {"detail": "Missing or invalid API key or session"}
    for path in ("/api/status", "/api/config"):
        aioclient_mock.get(f"{BASE}{path}", status=401, json=body)
    aioclient_mock.post(f"{BASE}/api/refresh", status=401, json=body)
    with pytest.raises(SmartFrameAuthError):
        await getattr(_client(hass, "wrong"), method_name)()


# -- Errors ----------------------------------------------------------------------------


async def test_422_string_detail(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """422 with a plain-text detail."""
    aioclient_mock.post(f"{BASE}/api/display/title-card", status=422, json={"detail": "text"})
    with pytest.raises(SmartFrameValidationError) as err:
        await _client(hass).async_title_card("x")
    assert err.value.status == 422
    assert err.value.detail == "text"


async def test_422_list_detail(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """422 with FastAPI-style validation errors is flattened into readable text."""
    aioclient_mock.post(
        f"{BASE}/api/context",
        status=422,
        json={
            "detail": [
                {"loc": ["body", "ttl_minutes"], "msg": "less than or equal to 10080", "type": "x"}
            ]
        },
    )
    with pytest.raises(SmartFrameValidationError) as err:
        await _client(hass).async_set_context(weather="rainy", ttl_minutes=99999)
    assert err.value.detail == "ttl_minutes: less than or equal to 10080"


@pytest.mark.parametrize("status", [404, 409, 500, 502, 504])
async def test_other_error_statuses(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, status: int
) -> None:
    """Other error statuses carry the engine's detail."""
    aioclient_mock.post(f"{BASE}/api/refresh", status=status, json={"detail": "nope"})
    with pytest.raises(SmartFrameApiError) as err:
        await _client(hass).async_refresh()
    assert not isinstance(err.value, SmartFrameValidationError)
    assert (err.value.status, err.value.detail) == (status, "nope")


@pytest.mark.parametrize("exc", [TimeoutError, aiohttp.ClientConnectionError("boom")])
async def test_network_failures(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, exc: Any
) -> None:
    """Timeouts and transport errors become SmartFrameConnectionError."""
    aioclient_mock.get(f"{BASE}/api/health", exc=exc)
    with pytest.raises(SmartFrameConnectionError):
        await _client(hass).async_get_health()


# -- URL building ----------------------------------------------------------------------


async def test_ipv6_host_and_root_path(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """IPv6 hosts are bracketed and ROOT_PATH prefixes every endpoint."""
    client = SmartFrameClient(async_get_clientsession(hass), "2001:db8::1", 8080, "/frame/")
    aioclient_mock.get("http://[2001:db8::1]:8080/frame/api/health", json=health_payload())
    await client.async_get_health()
    assert client.base_url == "http://[2001:db8::1]:8080/frame"


async def test_base_url_at_root(hass: HomeAssistant) -> None:
    """The default path is the server root."""
    assert _client(hass).base_url == f"{BASE}/"


# -- Status / config / family mode -----------------------------------------------------


async def test_status_parsing(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """GET /api/status is parsed into typed models."""
    aioclient_mock.get(f"{BASE}/api/status", json=status_payload())
    status = await _client(hass).async_get_status()

    assert status.version == "1.0.0rc3"
    assert status.release == "1.0.0-rc.3"
    assert status.uptime_s == 1234.5
    assert status.current_image is not None
    assert status.current_image.source == "immich"
    assert status.current_image.title == "Summer 2024"
    assert status.current_image.processed_at is not None
    assert status.ephemeral.active is False
    assert status.frame.configured is True
    assert status.frame.power_mode == "deepsleep"
    assert status.frame.telemetry.online is True
    assert status.frame.telemetry.battery_percent == 80
    assert status.frame.telemetry.charging is False
    assert status.frame.telemetry.rssi == -61
    assert status.frame.telemetry.firmware_version == "1.2.3"
    assert status.frame.telemetry.last_seen is not None
    assert status.scheduler.active_rule_kind == "immich"
    assert status.scheduler.active_rule_source == "smart_mix"
    assert status.delivery.pending is False
    assert status.delivery.last_delivery is not None
    assert status.delivery.last_delivery.delivered_at is not None
    assert status.delivery.last_delivery.reason == "submit"
    assert status.delivery.last_delivery.size_bytes == 1
    assert status.scheduler.enabled is True
    assert status.scheduler.last_run_status == "ok"
    assert status.scheduler.ambient.weather is not None
    assert status.scheduler.ambient.weather.value == "rainy"
    assert status.immich_configured is True
    assert status.tmdb_configured is False


async def test_status_before_first_image(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """current_image is null before the first image."""
    payload = status_payload()
    payload["current_image"] = None
    aioclient_mock.get(f"{BASE}/api/status", json=payload)
    assert (await _client(hass).async_get_status()).current_image is None


async def test_config_and_family_mode(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Family Mode is read from config.family.enabled and set with PUT /api/config."""
    aioclient_mock.get(f"{BASE}/api/config", json=config_payload(family_enabled=False))
    client = _client(hass, API_KEY)
    assert (await client.async_get_config()).family_enabled is False

    aioclient_mock.put(f"{BASE}/api/config", json=config_payload(family_enabled=True))
    result = await client.async_set_family_mode(True)

    method, url, data, _ = _last_call(aioclient_mock)
    assert (method, url.path) == ("PUT", "/api/config")
    assert data == {"family": {"enabled": True}}
    assert result.family_enabled is True


# -- Ambient context -------------------------------------------------------------------


async def test_get_context(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """GET /api/context is parsed."""
    aioclient_mock.get(f"{BASE}/api/context", json=context_payload())
    view = await _client(hass, API_KEY).async_get_context()
    assert view.family_active is True
    assert view.smart_mix_enabled is True
    assert view.extra_candidates == ("rainy",)
    assert view.context.family_mode is not None
    assert view.context.family_mode.value is True
    assert view.context.presence is not None
    assert view.context.presence.values == ("Alice", "Bob")
    assert view.context.weather is None


async def test_set_context_with_ttls(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Bare values, per-field TTL objects and the request-wide ttl_minutes."""
    aioclient_mock.post(f"{BASE}/api/context", json=context_payload())
    await _client(hass, API_KEY).async_set_context(
        presence=["Alice"],
        family_mode=WithTtl(True, 30),
        weather="rainy",
        boost_tags=WithTtl(["beach", "birthday"], 120),
        ttl_minutes=60,
    )
    _, url, data, _ = _last_call(aioclient_mock)
    assert url.path == "/api/context"
    assert data == {
        "presence": ["Alice"],
        "family_mode": {"value": True, "ttl_minutes": 30},
        "weather": "rainy",
        "boost_tags": {"values": ["beach", "birthday"], "ttl_minutes": 120},
        "ttl_minutes": 60,
    }
    assert "is_locked" not in data


async def test_set_context_requires_a_field(hass: HomeAssistant) -> None:
    """An empty update is a programming error, not a request."""
    with pytest.raises(ValueError, match="At least one"):
        await _client(hass, API_KEY).async_set_context()


async def test_clear_context(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """DELETE /api/context clears everything, or one field with ?field=."""
    aioclient_mock.delete(f"{BASE}/api/context", json=context_payload())
    client = _client(hass, API_KEY)

    await client.async_clear_context()
    method, url, _, _ = _last_call(aioclient_mock)
    assert (method, url.path, dict(url.query)) == ("DELETE", "/api/context", {})

    await client.async_clear_context("weather")
    assert dict(_last_call(aioclient_mock)[1].query) == {"field": "weather"}


# -- Display triggers ------------------------------------------------------------------


async def test_refresh(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """POST /api/refresh returns the replayed rule and the display result."""
    aioclient_mock.post(
        f"{BASE}/api/refresh",
        json={
            "display": display_payload(),
            "reason": "manual",
            "rule_kind": "immich",
            "rule_source": "smart_mix",
            "entry_label": "Beach",
        },
    )
    result = await _client(hass, API_KEY).async_refresh()
    assert result.rule_source == "smart_mix"
    assert result.entry_label == "Beach"
    assert result.display.delivery is not None
    assert result.display.delivery.status == "delivered"
    assert result.display.image.title == "Summer 2024"


async def test_title_card_minimal_and_full(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Only provided options are sent; ttl_minutes=None means 'until cancelled' (null)."""
    aioclient_mock.post(f"{BASE}/api/display/title-card", json=display_payload())
    client = _client(hass, API_KEY)

    await client.async_title_card("Hello")
    assert _last_call(aioclient_mock)[2] == {"title": "Hello"}

    result = await client.async_title_card(
        "Hello",
        subtitle="sub",
        caption="cap",
        accent="blue",
        ephemeral=False,
        ttl_minutes=None,
        hold_minutes=15,
        push_to_fraimic=False,
    )
    assert _last_call(aioclient_mock)[2] == {
        "title": "Hello",
        "subtitle": "sub",
        "caption": "cap",
        "accent": "blue",
        "ephemeral": False,
        "ttl_minutes": None,
        "hold_minutes": 15,
        "push_to_fraimic": False,
    }
    assert result.deduplicated is False

    await client.async_title_card("Hello", ttl_minutes=5)
    assert _last_call(aioclient_mock)[2] == {"title": "Hello", "ttl_minutes": 5}


async def test_now_playing_body(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """POST /api/display/now-playing sends only the documented fields that were given."""
    aioclient_mock.post(f"{BASE}/api/display/now-playing", json=display_payload())
    client = _client(hass, API_KEY)

    await client.async_now_playing("Daft Punk")
    call = _last_call(aioclient_mock)
    assert (call[0], call[1].path, call[2]) == (
        "POST",
        "/api/display/now-playing",
        {"artist": "Daft Punk"},
    )
    assert call[3]["X-API-Key"] == API_KEY

    result = await client.async_now_playing(
        "Daft Punk",
        album="Discovery",
        title="One More Time",
        artwork_url="http://ha/cover.jpg",
        duration_seconds=320.0,
        position_seconds=0.0,
        enrich_artwork=False,
        ephemeral=False,
        hold_minutes=30,
        push_to_fraimic=False,
        show_title=False,
        style="vinyl",
        ttl_seconds=120,
    )
    assert _last_call(aioclient_mock)[2] == {
        "artist": "Daft Punk",
        "album": "Discovery",
        "title": "One More Time",
        "artwork_url": "http://ha/cover.jpg",
        "duration_seconds": 320.0,
        "position_seconds": 0.0,
        "enrich_artwork": False,
        "ephemeral": False,
        "hold_minutes": 30,
        "push_to_fraimic": False,
        "show_title": False,
        "style": "vinyl",
        "ttl_seconds": 120,
    }
    assert result.image.source == "immich"


async def test_now_playing_deduplicated_result(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Sending the album that is already shown answers deduplicated: true."""
    aioclient_mock.post(
        f"{BASE}/api/display/now-playing", json=display_payload() | {"deduplicated": True}
    )
    assert (await _client(hass).async_now_playing("Air")).deduplicated is True


async def test_display_tmdb_body(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """POST /api/display/tmdb sends only the documented fields that were given."""
    aioclient_mock.post(f"{BASE}/api/display/tmdb", json=display_payload())
    client = _client(hass, API_KEY)

    await client.async_display_tmdb("Blade Runner")
    call = _last_call(aioclient_mock)
    assert (call[0], call[1].path, call[2]) == (
        "POST",
        "/api/display/tmdb",
        {"query": "Blade Runner"},
    )
    assert call[3]["X-API-Key"] == API_KEY

    await client.async_display_tmdb(
        "Severance",
        media_type="tv",
        season=0,
        year=2022,
        language="fr-CA",
        fallback_url="http://ha/poster.jpg",
        ephemeral=False,
        ttl_minutes=90,
        hold_minutes=15,
        push_to_fraimic=False,
    )
    assert _last_call(aioclient_mock)[2] == {
        "query": "Severance",
        "media_type": "tv",
        "season": 0,
        "year": 2022,
        "language": "fr-CA",
        "fallback_url": "http://ha/poster.jpg",
        "ephemeral": False,
        "ttl_minutes": 90,
        "hold_minutes": 15,
        "push_to_fraimic": False,
    }


@pytest.mark.parametrize("status", [404, 409, 502, 504])
async def test_display_tmdb_errors(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, status: int
) -> None:
    """The documented TMDB error statuses carry the engine's message."""
    aioclient_mock.post(f"{BASE}/api/display/tmdb", status=status, json={"detail": "nope"})
    with pytest.raises(SmartFrameApiError) as err:
        await _client(hass).async_display_tmdb("x")
    assert (err.value.status, err.value.detail) == (status, "nope")


async def test_now_playing_422(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A rejected cover request is a validation error."""
    aioclient_mock.post(f"{BASE}/api/display/now-playing", status=422, json={"detail": "bad"})
    with pytest.raises(SmartFrameValidationError):
        await _client(hass).async_now_playing("Air")


async def test_wakeup_non_blocking_returns_202(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """POST /api/fraimic/wakeup answers 202 WakeupAccepted without wait."""
    aioclient_mock.post(
        f"{BASE}/api/fraimic/wakeup", status=202, json={"accepted": True, "pending": True}
    )
    result = await _client(hass, API_KEY).async_wakeup()
    assert (result.accepted, result.pending, result.outcome) == (True, True, None)
    assert dict(_last_call(aioclient_mock)[1].query) == {}


async def test_wakeup_wait_returns_outcome(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """With wait=true the delivery outcome comes back (200)."""
    aioclient_mock.post(f"{BASE}/api/fraimic/wakeup", json=delivery_payload("delivered", "wakeup"))
    result = await _client(hass, API_KEY).async_wakeup(wait=True)
    assert dict(_last_call(aioclient_mock)[1].query) == {"wait": "true"}
    assert result.outcome is not None
    assert result.outcome.status == "delivered"
    assert result.outcome.reason == "wakeup"


@pytest.mark.parametrize(("http_status", "outcome"), [(200, "delivered"), (202, "queued")])
async def test_push(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, http_status: int, outcome: str
) -> None:
    """200 = delivered, 202 = frame unreachable and still queued; both are normal."""
    aioclient_mock.post(
        f"{BASE}/api/fraimic/push", status=http_status, json=delivery_payload(outcome)
    )
    result = await _client(hass, API_KEY).async_push(from_preview=True)
    assert result.status == outcome
    assert dict(_last_call(aioclient_mock)[1].query) == {"from_preview": "true"}


# -- Images ----------------------------------------------------------------------------


async def test_images(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """current.jpg and preview.jpg return raw bytes; 404 means no image yet."""
    aioclient_mock.get(f"{BASE}/api/current.jpg", content=b"\xff\xd8current")
    aioclient_mock.get(f"{BASE}/api/preview.jpg", status=404, json={"detail": "none"})
    client = _client(hass)
    assert await client.async_get_current_image() == b"\xff\xd8current"
    assert await client.async_get_preview_image() is None


async def test_image_server_error_is_raised(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Only 404 is swallowed for images."""
    aioclient_mock.get(f"{BASE}/api/current.jpg", status=500, text="boom")
    with pytest.raises(SmartFrameApiError):
        await _client(hass).async_get_current_image()
