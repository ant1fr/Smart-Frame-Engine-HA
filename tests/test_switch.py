"""Tests for the Family Mode switch."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.smart_frame_engine.const import (
    CAPABILITY_FAMILY_MODE,
    CAPABILITY_TITLE_CARD,
    DOMAIN,
)

from .common import (
    API_KEY,
    BASE,
    config_payload,
    context_payload,
    entity_id,
    has_entity,
    mock_engine,
    setup_engine,
)


async def _call(hass: HomeAssistant, service: str) -> None:
    await hass.services.async_call(
        "switch", service, {"entity_id": entity_id(hass, "switch", "family_mode")}, blocking=True
    )


def _puts(aioclient_mock: AiohttpClientMocker) -> list[tuple]:  # type: ignore[type-arg]
    return [call for call in aioclient_mock.mock_calls if call[0] == "PUT"]


async def test_state_follows_config_family_enabled(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The switch reads config.family.enabled, not the temporary ambient override."""
    await setup_engine(hass, aioclient_mock, config=config_payload(family_enabled=True))
    state = hass.states.get(entity_id(hass, "switch", "family_mode"))
    assert state is not None
    assert state.state == STATE_ON
    # AmbientView.family_active ("applies right now") is exposed separately.
    assert state.attributes["active_now"] is True


async def test_off_state_and_override_attribute(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The stored setting can be off while a temporary override applies."""
    await setup_engine(hass, aioclient_mock, config=config_payload(family_enabled=False))
    state = hass.states.get(entity_id(hass, "switch", "family_mode"))
    assert state is not None
    assert state.state == STATE_OFF
    assert state.attributes["active_now"] is True  # context_payload() says it applies now


async def test_turn_on_and_off(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Toggling sends PUT /api/config {"family": {"enabled": ...}} then refreshes."""
    await setup_engine(hass, aioclient_mock, api_key=API_KEY, auth="key")
    switch = entity_id(hass, "switch", "family_mode")
    assert hass.states.get(switch).state == STATE_OFF  # type: ignore[union-attr]

    # The engine now reports Family Mode on (the first matching mock wins, so start afresh).
    aioclient_mock.clear_requests()
    mock_engine(aioclient_mock, auth="key", config=config_payload(family_enabled=True))
    aioclient_mock.put(f"{BASE}/api/config", json=config_payload(family_enabled=True))
    await _call(hass, "turn_on")

    method, url, body, headers = _puts(aioclient_mock)[-1]
    assert (method, url.path, body) == ("PUT", "/api/config", {"family": {"enabled": True}})
    assert headers["X-API-Key"] == API_KEY
    assert hass.states.get(switch).state == STATE_ON  # type: ignore[union-attr]

    aioclient_mock.clear_requests()
    mock_engine(aioclient_mock, auth="key")
    aioclient_mock.put(f"{BASE}/api/config", json=config_payload(family_enabled=False))
    await _call(hass, "turn_off")
    assert _puts(aioclient_mock)[-1][2] == {"family": {"enabled": False}}
    assert hass.states.get(switch).state == STATE_OFF  # type: ignore[union-attr]


async def test_state_is_updated_before_the_next_poll(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The PUT reply is applied at once; the follow-up refresh then confirms it."""
    await setup_engine(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    mock_engine(aioclient_mock, config=config_payload(family_enabled=True))
    aioclient_mock.put(f"{BASE}/api/config", json=config_payload(family_enabled=True))
    status_calls = sum(c[1].path == "/api/status" for c in aioclient_mock.mock_calls)

    await _call(hass, "turn_on")

    assert sum(c[1].path == "/api/status" for c in aioclient_mock.mock_calls) == status_calls + 1


async def test_needs_the_family_mode_capability(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Engines without family_mode get no switch, and /api/config is not polled."""
    await setup_engine(hass, aioclient_mock, capabilities={CAPABILITY_TITLE_CARD})
    assert not has_entity(hass, "switch", "family_mode")
    assert all(c[1].path != "/api/config" for c in aioclient_mock.mock_calls)


async def test_config_is_polled_with_the_capability(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """With family_mode the coordinator fetches /api/config."""
    entry = await setup_engine(
        hass, aioclient_mock, capabilities={CAPABILITY_FAMILY_MODE}, context=context_payload()
    )
    assert any(c[1].path == "/api/config" for c in aioclient_mock.mock_calls)
    assert entry.runtime_data.data.config.family_enabled is False
    # Without ambient_context the attribute is simply absent.
    state = hass.states.get(entity_id(hass, "switch", "family_mode"))
    assert state is not None
    assert "active_now" not in state.attributes


@pytest.mark.parametrize("status", [409, 422, 500])
async def test_engine_errors_surface(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, status: int
) -> None:
    """Engine errors become Home Assistant errors that carry the engine's message."""
    await setup_engine(hass, aioclient_mock)
    aioclient_mock.put(f"{BASE}/api/config", status=status, json={"detail": "bad family"})
    with pytest.raises(HomeAssistantError, match="bad family"):
        await _call(hass, "turn_on")


async def test_unreachable_engine_raises(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A network error while toggling is reported to the user."""
    await setup_engine(hass, aioclient_mock)
    aioclient_mock.put(f"{BASE}/api/config", exc=TimeoutError)
    with pytest.raises(HomeAssistantError):
        await _call(hass, "turn_on")


async def test_rejected_key_starts_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 401 while acting asks the user to re-enter the key."""
    await setup_engine(hass, aioclient_mock, api_key=API_KEY, auth="key")
    aioclient_mock.put(f"{BASE}/api/config", status=401, json={"detail": "no"})
    with pytest.raises(HomeAssistantError):
        await _call(hass, "turn_on")
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_unavailable_when_the_engine_is_down(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The switch becomes unavailable while polling fails."""
    entry = await setup_engine(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}/api/health", exc=TimeoutError)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    state = hass.states.get(entity_id(hass, "switch", "family_mode"))
    assert state is not None
    assert state.state == STATE_UNAVAILABLE
