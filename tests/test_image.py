"""Tests for the live preview image entity."""

from __future__ import annotations

from typing import Any

import aiohttp
from homeassistant.components.image import async_get_image
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.smart_frame_engine.coordinator import SmartFrameCoordinator

from .common import API_KEY, BASE, entity_id, mock_engine, setup_engine, status_payload

JPEG = b"\xff\xd8\xff\xe0current"
PREVIEW = b"\xff\xd8\xff\xe0preview"


def _count(aioclient_mock: AiohttpClientMocker, path: str) -> int:
    return sum(call[1].path == path for call in aioclient_mock.mock_calls)


def _status_with(**image: Any) -> dict[str, Any]:
    status = status_payload()
    status["current_image"].update(image)
    return status


async def _refresh(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    coordinator: SmartFrameCoordinator,
    status: dict[str, Any],
) -> None:
    aioclient_mock.clear_requests()
    mock_engine(aioclient_mock, status=status)
    aioclient_mock.get(f"{BASE}/api/current.jpg", content=JPEG)
    await coordinator.async_refresh()
    await hass.async_block_till_done()


async def test_state_is_the_render_time(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The image entity's state is image_last_updated, taken from processed_at."""
    await setup_engine(hass, aioclient_mock)
    state = hass.states.get(entity_id(hass, "image", "current_image"))
    assert state is not None
    assert state.state == "2026-11-19T10:00:00+00:00"
    assert "access_token" in state.attributes


async def test_fetches_current_jpg_and_caches_it(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """One download serves any number of reads while the image does not change."""
    await setup_engine(hass, aioclient_mock)
    aioclient_mock.get(f"{BASE}/api/current.jpg", content=JPEG)
    entity = entity_id(hass, "image", "current_image")

    first = await async_get_image(hass, entity)
    second = await async_get_image(hass, entity)

    assert first.content == second.content == JPEG
    assert first.content_type == "image/jpeg"
    assert _count(aioclient_mock, "/api/current.jpg") == 1
    assert _count(aioclient_mock, "/api/preview.jpg") == 0


async def test_polling_the_same_image_keeps_the_cache(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Coordinator updates that do not change the image do not invalidate the cache."""
    entry = await setup_engine(hass, aioclient_mock)
    aioclient_mock.get(f"{BASE}/api/current.jpg", content=JPEG)
    entity = entity_id(hass, "image", "current_image")
    await async_get_image(hass, entity)

    await _refresh(hass, aioclient_mock, entry.runtime_data, status_payload())
    await async_get_image(hass, entity)

    assert _count(aioclient_mock, "/api/current.jpg") == 0


@pytest.mark.parametrize(
    "change",
    [{"sha256": "f" * 64}, {"processed_at": "2026-11-19T11:30:00Z"}],
    ids=["new content hash", "new render time"],
)
async def test_new_image_invalidates_the_cache(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    change: dict[str, str],
) -> None:
    """A new image (hash or render time) bumps image_last_updated and is downloaded again."""
    entry = await setup_engine(hass, aioclient_mock)
    aioclient_mock.get(f"{BASE}/api/current.jpg", content=JPEG)
    entity = entity_id(hass, "image", "current_image")
    await async_get_image(hass, entity)
    token_before = hass.states.get(entity).attributes["access_token"]  # type: ignore[union-attr]

    await _refresh(hass, aioclient_mock, entry.runtime_data, _status_with(**change))

    state = hass.states.get(entity)
    assert state is not None
    expected = change.get("processed_at", "2026-11-19T10:00:00Z").replace("Z", "+00:00")
    assert state.state == expected
    assert state.attributes["access_token"] != token_before
    await async_get_image(hass, entity)
    assert _count(aioclient_mock, "/api/current.jpg") == 1


async def test_falls_back_to_the_preview_on_404(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """current.jpg answers 404 before an image is displayed: use preview.jpg."""
    await setup_engine(hass, aioclient_mock)
    aioclient_mock.get(f"{BASE}/api/current.jpg", status=404, json={"detail": "none"})
    aioclient_mock.get(f"{BASE}/api/preview.jpg", content=PREVIEW)
    entity = entity_id(hass, "image", "current_image")

    image = await async_get_image(hass, entity)
    again = await async_get_image(hass, entity)

    assert image.content == again.content == PREVIEW
    # The fallback result is cached too, so the 404 is not retried on every read.
    assert _count(aioclient_mock, "/api/current.jpg") == 1
    assert _count(aioclient_mock, "/api/preview.jpg") == 1


async def test_no_image_at_all(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Both endpoints 404: nothing to show, and nothing is cached."""
    status = status_payload()
    status["current_image"] = None
    await setup_engine(hass, aioclient_mock, status=status)
    aioclient_mock.get(f"{BASE}/api/current.jpg", status=404, json={"detail": "none"})
    aioclient_mock.get(f"{BASE}/api/preview.jpg", status=404, json={"detail": "none"})
    entity = entity_id(hass, "image", "current_image")

    assert hass.states.get(entity).state == STATE_UNKNOWN  # type: ignore[union-attr]
    with pytest.raises(HomeAssistantError):
        await async_get_image(hass, entity)
    with pytest.raises(HomeAssistantError):
        await async_get_image(hass, entity)
    assert _count(aioclient_mock, "/api/current.jpg") == 2


@pytest.mark.parametrize("failure", [{"exc": TimeoutError}, {"status": 500, "text": "boom"}])
async def test_download_errors_are_not_cached(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, failure: dict[str, Any]
) -> None:
    """A failing download yields no image and is retried on the next read."""
    await setup_engine(hass, aioclient_mock)
    aioclient_mock.get(f"{BASE}/api/current.jpg", **failure)
    entity = entity_id(hass, "image", "current_image")
    with pytest.raises(HomeAssistantError):
        await async_get_image(hass, entity)

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}/api/current.jpg", content=JPEG)
    assert (await async_get_image(hass, entity)).content == JPEG


async def test_sends_the_api_key(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """With PUBLIC_IMAGE_ENDPOINTS=false the image needs the key."""
    await setup_engine(hass, aioclient_mock, api_key=API_KEY, auth="key")
    aioclient_mock.get(f"{BASE}/api/current.jpg", content=JPEG)
    await async_get_image(hass, entity_id(hass, "image", "current_image"))
    call = [c for c in aioclient_mock.mock_calls if c[1].path == "/api/current.jpg"][-1]
    assert call[3]["X-API-Key"] == API_KEY


async def test_no_image_before_the_first_render_then_first_image(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Going from 'no image yet' to a first image sets image_last_updated."""
    status = status_payload()
    status["current_image"] = None
    entry = await setup_engine(hass, aioclient_mock, status=status)
    entity = entity_id(hass, "image", "current_image")
    assert hass.states.get(entity).state == STATE_UNKNOWN  # type: ignore[union-attr]

    await _refresh(hass, aioclient_mock, entry.runtime_data, status_payload())
    assert hass.states.get(entity).state == "2026-11-19T10:00:00+00:00"  # type: ignore[union-attr]


async def test_connection_error_class_is_handled(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Transport errors (not only timeouts) are swallowed into 'no image'."""
    await setup_engine(hass, aioclient_mock)
    aioclient_mock.get(f"{BASE}/api/current.jpg", exc=aiohttp.ClientConnectionError("x"))
    with pytest.raises(HomeAssistantError):
        await async_get_image(hass, entity_id(hass, "image", "current_image"))
