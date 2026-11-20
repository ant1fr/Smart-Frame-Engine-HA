"""Tests for the base entity and the shared device."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityDescription
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.smart_frame_engine.const import DOMAIN
from custom_components.smart_frame_engine.coordinator import SmartFrameCoordinator
from custom_components.smart_frame_engine.entity import SmartFrameEntity

from .common import INSTANCE_ID, INSTANCE_NAME, make_entry, mock_engine


async def test_entity_shares_the_engine_device(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Entities attach to one device keyed on instance_id, with sw_version and a config URL."""
    mock_engine(aioclient_mock)
    entry = make_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator: SmartFrameCoordinator = entry.runtime_data

    entity = SmartFrameEntity(coordinator, EntityDescription(key="status"))

    assert entity.unique_id == f"{INSTANCE_ID}_status"
    assert entity.has_entity_name is True
    info = entity.device_info
    assert info["identifiers"] == {(DOMAIN, INSTANCE_ID)}
    assert info["name"] == INSTANCE_NAME
    assert info["manufacturer"] == "Smart Frame Engine"
    assert info["sw_version"] == "1.0.0-rc.3"
    assert info["configuration_url"] == "http://192.168.1.50:8080/"
    assert entity.available is True
