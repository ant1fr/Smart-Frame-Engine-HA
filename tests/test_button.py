"""Tests for the button platform."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.smart_frame_engine.const import (
    CAPABILITY_FORCE_REFRESH,
    CAPABILITY_FRAME_WAKEUP,
    DOMAIN,
)

from .common import (
    API_KEY,
    BASE,
    display_payload,
    entity_id,
    has_entity,
    setup_engine,
)

REFRESH_REPLY = {
    "display": display_payload(),
    "reason": "manual",
    "rule_kind": "immich",
    "rule_source": "smart_mix",
}


async def _press(hass: HomeAssistant, key: str) -> None:
    await hass.services.async_call(
        "button", "press", {"entity_id": entity_id(hass, "button", key)}, blocking=True
    )


def _count(aioclient_mock: AiohttpClientMocker, path: str) -> int:
    return sum(call[1].path == path for call in aioclient_mock.mock_calls)


@pytest.mark.parametrize(
    ("capabilities", "refresh", "wake"),
    [
        ({CAPABILITY_FORCE_REFRESH, CAPABILITY_FRAME_WAKEUP}, True, True),
        ({CAPABILITY_FORCE_REFRESH}, True, False),
        ({CAPABILITY_FRAME_WAKEUP}, False, True),
        (set(), False, False),
    ],
)
async def test_capability_gating(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    capabilities: set[str],
    refresh: bool,
    wake: bool,
) -> None:
    """Each button exists only when the engine advertises its capability."""
    await setup_engine(hass, aioclient_mock, capabilities=capabilities)
    assert has_entity(hass, "button", "force_refresh") is refresh
    assert has_entity(hass, "button", "wake_frame") is wake


async def test_force_refresh(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Pressing calls POST /api/refresh and then refreshes the coordinator."""
    await setup_engine(hass, aioclient_mock, api_key=API_KEY, auth="key")
    aioclient_mock.post(f"{BASE}/api/refresh", json=REFRESH_REPLY)
    before = _count(aioclient_mock, "/api/status")

    await _press(hass, "force_refresh")

    method, url, _, headers = [c for c in aioclient_mock.mock_calls if c[0] == "POST"][-1]
    assert (method, url.path) == ("POST", "/api/refresh")
    assert headers["X-API-Key"] == API_KEY
    assert _count(aioclient_mock, "/api/status") == before + 1


async def test_wake_frame_is_non_blocking(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Wake up: POST /api/fraimic/wakeup without ?wait (the engine answers 202)."""
    await setup_engine(hass, aioclient_mock)
    aioclient_mock.post(
        f"{BASE}/api/fraimic/wakeup", status=202, json={"accepted": True, "pending": True}
    )
    before = _count(aioclient_mock, "/api/status")

    await _press(hass, "wake_frame")

    _, url, _, _ = [c for c in aioclient_mock.mock_calls if c[0] == "POST"][-1]
    assert url.path == "/api/fraimic/wakeup"
    assert dict(url.query) == {}
    assert _count(aioclient_mock, "/api/status") == before + 1


@pytest.mark.parametrize(
    ("key", "path"),
    [("force_refresh", "/api/refresh"), ("wake_frame", "/api/fraimic/wakeup")],
)
@pytest.mark.parametrize("status", [404, 409, 502, 504])
async def test_engine_errors_surface(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    key: str,
    path: str,
    status: int,
) -> None:
    """404/409/502/504 (no source, upstream down...) reach the user with the engine's message."""
    await setup_engine(hass, aioclient_mock)
    aioclient_mock.post(f"{BASE}{path}", status=status, json={"detail": "Immich is not set up"})
    with pytest.raises(HomeAssistantError, match="Immich is not set up"):
        await _press(hass, key)


async def test_no_refresh_after_a_failed_press(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A failed action does not trigger a coordinator refresh."""
    await setup_engine(hass, aioclient_mock)
    aioclient_mock.post(f"{BASE}/api/refresh", status=409, json={"detail": "nope"})
    before = _count(aioclient_mock, "/api/status")
    with pytest.raises(HomeAssistantError):
        await _press(hass, "force_refresh")
    assert _count(aioclient_mock, "/api/status") == before


async def test_rejected_key_starts_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 401 on a press starts reauthentication."""
    await setup_engine(hass, aioclient_mock, api_key=API_KEY, auth="key")
    aioclient_mock.post(f"{BASE}/api/refresh", status=401, json={"detail": "no"})
    with pytest.raises(HomeAssistantError):
        await _press(hass, "force_refresh")
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]
