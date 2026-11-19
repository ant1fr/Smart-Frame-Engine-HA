"""Tests for setup, the coordinator, the compatibility guardrail and Repair issues."""

from __future__ import annotations

from datetime import timedelta

import aiohttp
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.smart_frame_engine.const import (
    ALL_CAPABILITIES,
    CAPABILITY_FORCE_REFRESH,
    CAPABILITY_TITLE_CARD,
    CONF_SCAN_INTERVAL,
    DOMAIN,
)
from custom_components.smart_frame_engine.coordinator import SmartFrameCoordinator

from .common import (
    API_KEY,
    BASE,
    INSTANCE_ID,
    OTHER_INSTANCE_ID,
    health_payload,
    make_entry,
    mock_engine,
    status_payload,
)


def _issue(hass: HomeAssistant, entry_id: str) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, f"incompatible_api_{entry_id}")


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


# -- Happy path ------------------------------------------------------------------------


async def test_setup_and_unload(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A compatible engine loads; capabilities and data are cached; unload works."""
    mock_engine(aioclient_mock)
    entry = make_entry()
    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    coordinator: SmartFrameCoordinator = entry.runtime_data
    assert coordinator.capabilities == ALL_CAPABILITIES
    assert coordinator.data.health.instance_id == INSTANCE_ID
    assert coordinator.data.status.version == "1.0.0rc2"
    assert coordinator.data.ambient is not None
    assert coordinator.data.ambient.family_active is True
    assert _issue(hass, entry.entry_id) is None

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_capabilities_adapt_to_the_engine(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Only advertised capabilities are cached; ambient context is skipped without its flag."""
    mock_engine(aioclient_mock, capabilities={CAPABILITY_TITLE_CARD})
    entry = make_entry()
    await _setup(hass, entry)

    coordinator: SmartFrameCoordinator = entry.runtime_data
    assert coordinator.capabilities == {CAPABILITY_TITLE_CARD}
    assert coordinator.data.ambient is None
    assert all(call[1].path != "/api/context" for call in aioclient_mock.mock_calls)


async def test_key_is_sent_when_configured(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Polling carries the configured API key."""
    mock_engine(aioclient_mock, auth="key")
    await _setup(hass, make_entry(api_key=API_KEY))
    keyed = [c for c in aioclient_mock.mock_calls if c[1].path == "/api/context"]
    assert keyed
    assert all(c[3]["X-API-Key"] == API_KEY for c in keyed)


@pytest.mark.parametrize(("options", "seconds"), [({}, 30), ({CONF_SCAN_INTERVAL: 120}, 120)])
async def test_scan_interval_option(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    options: dict[str, int],
    seconds: int,
) -> None:
    """The polling interval defaults to 30 s and follows the option."""
    mock_engine(aioclient_mock)
    entry = make_entry()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options=options)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.update_interval == timedelta(seconds=seconds)


# -- Failures --------------------------------------------------------------------------


async def test_unreachable_engine_retries_setup(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A connection error during setup means 'not ready yet', not a broken entry."""
    aioclient_mock.get(f"{BASE}/api/health", exc=aiohttp.ClientError("down"))
    entry = make_entry()
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_timeout_retries_setup(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A timeout is handled like any other connection error."""
    aioclient_mock.get(f"{BASE}/api/health", exc=TimeoutError)
    entry = make_entry()
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_401_starts_reauth(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """In key mode /api/status is public; the key-gated context call detects a bad key."""
    aioclient_mock.get(f"{BASE}/api/health", json=health_payload(auth="key"))
    aioclient_mock.get(f"{BASE}/api/status", json=status_payload())
    aioclient_mock.get(f"{BASE}/api/context", status=401, json={"detail": "Missing"})
    entry = make_entry(api_key="revoked")
    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_status_401_starts_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """In password mode /api/status itself needs the key."""
    aioclient_mock.get(f"{BASE}/api/health", json=health_payload(auth="password"))
    aioclient_mock.get(f"{BASE}/api/status", status=401, json={"detail": "Missing"})
    entry = make_entry(api_key="revoked")
    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def test_later_failure_marks_update_failed_and_recovers(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """After setup, a failed poll fails the update (entities unavailable) and then recovers."""
    mock_engine(aioclient_mock)
    entry = make_entry()
    await _setup(hass, entry)
    coordinator: SmartFrameCoordinator = entry.runtime_data

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}/api/health", exc=TimeoutError)
    await coordinator.async_refresh()
    assert coordinator.last_update_success is False
    assert isinstance(coordinator.last_exception, UpdateFailed)
    assert entry.state is ConfigEntryState.LOADED

    aioclient_mock.clear_requests()
    mock_engine(aioclient_mock)
    await coordinator.async_refresh()
    assert coordinator.last_update_success is True


async def test_different_engine_at_same_address_is_refused(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """If another engine takes over the address, do not poll it as ours."""
    mock_engine(aioclient_mock, instance_id=OTHER_INSTANCE_ID)
    entry = make_entry()
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert _issue(hass, entry.entry_id) is None


# -- Compatibility guardrail + Repair issues -------------------------------------------


@pytest.mark.parametrize(
    ("api_version", "min_api_version", "translation_key"),
    [(0, 0, "server_outdated"), (2, 2, "integration_outdated")],
)
async def test_incompatible_engine_creates_repair_issue(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    api_version: int,
    min_api_version: int,
    translation_key: str,
) -> None:
    """Incompatibility blocks setup and raises a persistent, non-fixable Repair issue."""
    mock_engine(aioclient_mock, api_version=api_version, min_api_version=min_api_version)
    entry = make_entry()
    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_RETRY
    issue = _issue(hass, entry.entry_id)
    assert issue is not None
    assert issue.translation_key == translation_key
    assert issue.severity is ir.IssueSeverity.ERROR
    assert issue.is_persistent is True
    assert issue.is_fixable is False
    assert issue.translation_placeholders == {
        "name": entry.title,
        "server_version": "1.0.0rc2",
        "api_version": str(api_version),
        "min_api_version": str(min_api_version),
        "supported_version": "1",
    }


async def test_repair_issue_is_resolved_automatically(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The issue disappears on its own once the engine is compatible again."""
    mock_engine(aioclient_mock)
    entry = make_entry()
    await _setup(hass, entry)
    coordinator: SmartFrameCoordinator = entry.runtime_data
    assert _issue(hass, entry.entry_id) is None

    # The engine is upgraded to a contract that dropped version 1.
    aioclient_mock.clear_requests()
    mock_engine(aioclient_mock, api_version=2, min_api_version=2)
    await coordinator.async_refresh()
    assert coordinator.last_update_success is False
    issue = _issue(hass, entry.entry_id)
    assert issue is not None
    assert issue.translation_key == "integration_outdated"

    # The integration is updated (here: the engine serves contract 1 again).
    aioclient_mock.clear_requests()
    mock_engine(aioclient_mock, api_version=2, min_api_version=1)
    await coordinator.async_refresh()
    assert coordinator.last_update_success is True
    assert _issue(hass, entry.entry_id) is None


async def test_issue_cleared_when_setup_retry_succeeds(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An entry stuck in SETUP_RETRY by an old engine loads once the engine is updated."""
    mock_engine(aioclient_mock, api_version=0, min_api_version=0)
    entry = make_entry()
    await _setup(hass, entry)
    assert _issue(hass, entry.entry_id) is not None

    aioclient_mock.clear_requests()
    mock_engine(aioclient_mock)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=5))
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert _issue(hass, entry.entry_id) is None


async def test_removing_entry_deletes_issue(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Deleting the entry removes its Repair issue."""
    mock_engine(aioclient_mock, api_version=0, min_api_version=0)
    entry = make_entry()
    await _setup(hass, entry)
    assert _issue(hass, entry.entry_id) is not None

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert _issue(hass, entry.entry_id) is None


# -- Capability changes ----------------------------------------------------------------


async def test_capability_change_reloads_the_entry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An engine that gains or loses a capability gets its entities rebuilt."""
    mock_engine(aioclient_mock)
    entry = make_entry()
    await _setup(hass, entry)
    first: SmartFrameCoordinator = entry.runtime_data
    assert CAPABILITY_FORCE_REFRESH in first.capabilities

    aioclient_mock.clear_requests()
    mock_engine(aioclient_mock, capabilities=ALL_CAPABILITIES - {CAPABILITY_FORCE_REFRESH})
    await first.async_refresh()
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data is not first
    assert CAPABILITY_FORCE_REFRESH not in entry.runtime_data.capabilities


async def test_unchanged_capabilities_do_not_reload(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Normal polling never reloads the entry."""
    mock_engine(aioclient_mock)
    entry = make_entry()
    await _setup(hass, entry)
    first: SmartFrameCoordinator = entry.runtime_data

    await first.async_refresh()
    await hass.async_block_till_done()

    assert entry.runtime_data is first
