"""Tests for the sensor platform."""

from __future__ import annotations

from typing import Any

from homeassistant.const import STATE_UNKNOWN, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.smart_frame_engine.const import (
    CAPABILITY_AMBIENT_CONTEXT,
    CAPABILITY_TITLE_CARD,
)
from custom_components.smart_frame_engine.sensor import SmartFrameSensor

from .common import entity_id, has_entity, setup_engine, status_payload


def _state(hass: HomeAssistant, key: str) -> Any:
    state = hass.states.get(entity_id(hass, "sensor", key))
    assert state is not None
    return state


async def test_values(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Each sensor reports the documented field."""
    await setup_engine(hass, aioclient_mock)

    assert _state(hass, "engine_version").state == "1.0.0-rc.3"
    assert _state(hass, "active_source").state == "immich"
    assert _state(hass, "media_title").state == "Summer 2024"
    assert _state(hass, "refresh_rule").state == "smart_mix"
    assert _state(hass, "last_delivery").state == "2026-11-19T10:00:05+00:00"
    assert _state(hass, "frame_last_seen").state == "2026-11-19T09:59:00+00:00"
    assert _state(hass, "frame_battery").state == "80.0"
    assert _state(hass, "frame_battery").attributes["device_class"] == "battery"
    assert _state(hass, "frame_battery").attributes["unit_of_measurement"] == "%"


async def test_engine_version_falls_back_before_rc3(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Engines older than 1.0.0rc3 send no ``release``: show the PEP 440 version instead."""
    status = status_payload()
    del status["release"]
    entry = await setup_engine(hass, aioclient_mock, status=status)

    assert entry.runtime_data.data.status.release is None
    assert _state(hass, "engine_version").state == "1.0.0rc3"


async def test_refresh_rule_is_an_enum(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The rule source enum of the contract is exposed with options and the rule kind."""
    await setup_engine(hass, aioclient_mock)
    state = _state(hass, "refresh_rule")
    assert state.attributes["device_class"] == "enum"
    assert state.attributes["options"] == ["pinned", "smart_mix", "last"]
    assert state.attributes["kind"] == "immich"


async def test_unknown_rule_source_does_not_break_the_state(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Contract: enum values may be added; an unknown one becomes unknown, not an error."""
    status = status_payload()
    status["scheduler"]["active_rule"] = {"kind": "immich", "source": "from_the_future"}
    await setup_engine(hass, aioclient_mock, status=status)
    assert _state(hass, "refresh_rule").state == STATE_UNKNOWN


async def test_media_title_attributes(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The title sensor carries the image metadata that /api/status documents."""
    status = status_payload()
    status["current_image"].update(
        {
            "title": "Dune",
            "source": "tmdb",
            "template": "poster",
            "ephemeral": True,
            "expires_at": "2026-11-19T12:00:00Z",
            "details": {"year": 2021, "people": ["Alice"]},
        }
    )
    await setup_engine(hass, aioclient_mock, status=status)

    state = _state(hass, "media_title")
    assert state.state == "Dune"
    assert state.attributes["source"] == "tmdb"
    assert state.attributes["template"] == "poster"
    assert state.attributes["ephemeral"] is True
    assert state.attributes["pushed"] is True
    assert state.attributes["processed_at"].isoformat() == "2026-11-19T10:00:00+00:00"
    assert state.attributes["expires_at"].isoformat() == "2026-11-19T12:00:00+00:00"
    assert state.attributes["details"] == {"year": 2021, "people": ["Alice"]}
    assert _state(hass, "active_source").attributes["template"] == "poster"


def test_provider_details_are_not_recorded() -> None:
    """People and places in the provider metadata stay out of the recorder database."""
    assert "details" in SmartFrameSensor._unrecorded_attributes


async def test_long_titles_fit_the_state_limit(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """HA states are limited to 255 characters."""
    status = status_payload()
    status["current_image"]["title"] = "x" * 400
    await setup_engine(hass, aioclient_mock, status=status)
    assert _state(hass, "media_title").state == "x" * 255


async def test_before_the_first_image(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """current_image is null before the first image: unknown, no attributes."""
    status = status_payload()
    status["current_image"] = None
    await setup_engine(hass, aioclient_mock, status=status)
    for key in ("media_title", "active_source"):
        assert _state(hass, key).state == STATE_UNKNOWN
    assert "processed_at" not in _state(hass, "media_title").attributes


async def test_ambient_context_summary(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The ambient sensor counts the active fields and lists them with their expiry."""
    await setup_engine(hass, aioclient_mock)

    state = _state(hass, "ambient_context")
    assert state.state == "2"
    assert state.attributes["family_mode"] is True
    assert state.attributes["family_mode_expires_at"].isoformat() == "2026-11-19T12:00:00+00:00"
    assert state.attributes["presence"] == ["Alice", "Bob"]
    assert state.attributes["presence_expires_at"].isoformat() == "2026-11-19T12:00:00+00:00"
    assert state.attributes["family_active"] is True
    assert state.attributes["smart_mix_enabled"] is True
    assert state.attributes["extra_candidates"] == ["rainy"]
    assert "weather" not in state.attributes


async def test_ambient_context_empty(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """No active field means a count of zero."""
    await setup_engine(
        hass,
        aioclient_mock,
        context={
            "context": {},
            "extra_candidates": [],
            "family_active": False,
            "smart_mix_enabled": False,
        },
    )
    assert _state(hass, "ambient_context").state == "0"


async def test_ambient_sensor_needs_the_capability(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Capability gating: no ambient_context flag, no ambient sensor (and no polling of it)."""
    await setup_engine(hass, aioclient_mock, capabilities={CAPABILITY_TITLE_CARD})
    assert not has_entity(hass, "sensor", "ambient_context")
    assert has_entity(hass, "sensor", "engine_version")

    await hass.config_entries.async_reload(hass.config_entries.async_entries()[0].entry_id)
    await hass.async_block_till_done()
    assert CAPABILITY_AMBIENT_CONTEXT not in {
        c[1].path.removeprefix("/api/") for c in aioclient_mock.mock_calls
    }


async def test_diagnostic_entities_and_defaults(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Diagnostic sensors are categorised; the noisy Wi-Fi signal is disabled by default."""
    await setup_engine(hass, aioclient_mock)
    registry = er.async_get(hass)

    for key in ("engine_version", "last_delivery", "frame_last_seen", "frame_battery"):
        entry = registry.async_get(entity_id(hass, "sensor", key))
        assert entry is not None
        assert entry.entity_category is EntityCategory.DIAGNOSTIC

    wifi = registry.async_get(entity_id(hass, "sensor", "frame_wifi_signal"))
    assert wifi is not None
    assert wifi.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get(wifi.entity_id) is None


async def test_no_delivery_yet(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A fresh engine has no last delivery."""
    status = status_payload()
    status["delivery"] = {"pending": False, "last_delivery": None}
    await setup_engine(hass, aioclient_mock, status=status)
    assert _state(hass, "last_delivery").state == STATE_UNKNOWN
