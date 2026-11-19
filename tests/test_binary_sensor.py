"""Tests for the binary sensor platform."""

from __future__ import annotations

from typing import Any

from homeassistant.const import (
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    EntityCategory,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.smart_frame_engine.coordinator import SmartFrameCoordinator

from .common import BASE, entity_id, mock_engine, setup_engine, status_payload


def _state(hass: HomeAssistant, key: str) -> Any:
    state = hass.states.get(entity_id(hass, "binary_sensor", key))
    assert state is not None
    return state


async def test_states_and_metadata(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Every binary sensor reports the value documented in /api/status."""
    await setup_engine(hass, aioclient_mock)

    assert _state(hass, "engine_connected").state == STATE_ON
    assert _state(hass, "engine_connected").attributes["device_class"] == "connectivity"
    assert _state(hass, "frame_online").state == STATE_ON
    assert _state(hass, "frame_online").attributes["device_class"] == "connectivity"
    assert _state(hass, "frame_online").attributes["last_error"] is None
    assert _state(hass, "frame_charging").state == STATE_OFF
    assert _state(hass, "frame_charging").attributes["device_class"] == "battery_charging"
    assert _state(hass, "delivery_pending").state == STATE_OFF
    assert _state(hass, "ephemeral_active").state == STATE_OFF
    assert _state(hass, "quiet_hours").state == STATE_OFF

    registry = er.async_get(hass)
    for key in ("engine_connected", "frame_online", "frame_charging", "delivery_pending"):
        entry = registry.async_get(entity_id(hass, "binary_sensor", key))
        assert entry is not None
        assert entry.entity_category is EntityCategory.DIAGNOSTIC


async def test_active_states(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Pending delivery, temporary image, quiet hours and an offline frame are reflected."""
    status = status_payload()
    status["delivery"]["pending"] = True
    status["ephemeral"] = {
        "active": True,
        "pending": False,
        "has_base": True,
        "expires_at": "2026-11-19T12:00:00Z",
        "source": "tmdb",
        "title": "Dune",
    }
    status["scheduler"]["in_quiet_hours"] = True
    status["frame"]["telemetry"]["online"] = False
    status["frame"]["telemetry"]["last_error"] = "timeout"
    status["frame"]["telemetry"]["info"]["charging"] = True
    await setup_engine(hass, aioclient_mock, status=status)

    assert _state(hass, "delivery_pending").state == STATE_ON
    assert _state(hass, "quiet_hours").state == STATE_ON
    assert _state(hass, "frame_online").state == STATE_OFF
    assert _state(hass, "frame_online").attributes["last_error"] == "timeout"
    assert _state(hass, "frame_charging").state == STATE_ON
    ephemeral = _state(hass, "ephemeral_active")
    assert ephemeral.state == STATE_ON
    assert ephemeral.attributes["title"] == "Dune"
    assert ephemeral.attributes["source"] == "tmdb"


async def test_frame_never_contacted_is_unknown(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """telemetry.online is null until the frame was contacted: unknown, not off."""
    status = status_payload()
    status["frame"]["telemetry"] = {"online": None, "info": None}
    await setup_engine(hass, aioclient_mock, status=status)

    assert _state(hass, "frame_online").state == STATE_UNKNOWN
    assert _state(hass, "frame_charging").state == STATE_UNKNOWN


async def test_frame_not_configured_is_unknown(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Without a configured frame there is nothing to be online."""
    status = status_payload()
    status["frame"]["configured"] = False
    await setup_engine(hass, aioclient_mock, status=status)
    assert _state(hass, "frame_online").state == STATE_UNKNOWN


@pytest.mark.parametrize("exc", [TimeoutError, ConnectionError])
async def test_engine_connectivity_reports_the_outage(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, exc: type[Exception]
) -> None:
    """When polling fails the connectivity sensor goes off; the others become unavailable."""
    entry = await setup_engine(hass, aioclient_mock)
    coordinator: SmartFrameCoordinator = entry.runtime_data

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}/api/health", exc=exc)
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert _state(hass, "engine_connected").state == STATE_OFF
    assert _state(hass, "frame_online").state == STATE_UNAVAILABLE
    assert _state(hass, "quiet_hours").state == STATE_UNAVAILABLE

    aioclient_mock.clear_requests()
    mock_engine(aioclient_mock)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert _state(hass, "engine_connected").state == STATE_ON
    assert _state(hass, "frame_online").state == STATE_ON
