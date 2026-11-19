"""Tests for diagnostics (secrets must never leak)."""

from __future__ import annotations

import json

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.smart_frame_engine.const import ALL_CAPABILITIES

from .common import API_KEY, INSTANCE_ID, make_entry, mock_engine


async def test_diagnostics_redacts_the_api_key(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Coordinator data, health and capabilities are exported; the API key is redacted."""
    mock_engine(aioclient_mock, auth="key")
    entry = make_entry(api_key=API_KEY)
    entry.add_to_hass(hass)
    assert await async_setup_component(hass, "diagnostics", {})
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    assert diagnostics["entry"]["data"]["api_key"] == "**REDACTED**"
    assert API_KEY not in json.dumps(diagnostics)
    assert diagnostics["entry"]["unique_id"] == INSTANCE_ID
    assert diagnostics["capabilities"] == sorted(ALL_CAPABILITIES)
    assert diagnostics["health"]["auth"] == "key"
    assert diagnostics["health"]["api_version"] == 1
    assert diagnostics["health"]["instance_id"] == INSTANCE_ID
    assert diagnostics["status"]["version"] == "1.0.0rc2"
    assert diagnostics["ambient"]["family_active"] is True
    assert diagnostics["coordinator"]["last_update_success"] is True
