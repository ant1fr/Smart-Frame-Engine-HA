"""Tests for the Home Assistant services."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
import voluptuous as vol

from custom_components.smart_frame_engine.const import (
    CAPABILITY_AMBIENT_CONTEXT,
    CAPABILITY_TITLE_CARD,
    DOMAIN,
)

from .common import (
    API_KEY,
    BASE,
    OTHER_INSTANCE_ID,
    context_payload,
    display_payload,
    make_entry,
    setup_engine,
)

OTHER_BASE = "http://192.168.1.51:8080"


def _calls(aioclient_mock: AiohttpClientMocker, method: str) -> list[tuple[Any, ...]]:
    return [c for c in aioclient_mock.mock_calls if c[0] == method]


async def _call(hass: HomeAssistant, service: str, data: dict[str, Any] | None = None) -> None:
    await hass.services.async_call(DOMAIN, service, data or {}, blocking=True)


async def test_services_are_registered_without_any_entry(hass: HomeAssistant) -> None:
    """Services are registered when the integration loads (from async_setup)."""
    assert await async_setup_component(hass, DOMAIN, {})
    for name in ("set_ambient_context", "clear_ambient_context", "send_title_card"):
        assert hass.services.has_service(DOMAIN, name)


# -- set_ambient_context ---------------------------------------------------------------


async def test_set_ambient_context_with_every_field(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """All documented fields and ttl_minutes go to POST /api/context."""
    await setup_engine(hass, aioclient_mock, api_key=API_KEY, auth="key")
    aioclient_mock.post(f"{BASE}/api/context", json=context_payload())
    before = len([c for c in aioclient_mock.mock_calls if c[1].path == "/api/status"])

    await _call(
        hass,
        "set_ambient_context",
        {
            "family_mode": True,
            "presence": ["Alice", "Bob"],
            "weather": "rainy",
            "season": "winter",
            "boost_tags": ["beach"],
            "boost_people": ["Alice"],
            "ttl_minutes": 90,
        },
    )

    _, url, body, headers = _calls(aioclient_mock, "POST")[-1]
    assert url.path == "/api/context"
    assert body == {
        "family_mode": True,
        "presence": ["Alice", "Bob"],
        "weather": "rainy",
        "season": "winter",
        "boost_tags": ["beach"],
        "boost_people": ["Alice"],
        "ttl_minutes": 90,
    }
    assert headers["X-API-Key"] == API_KEY
    assert len([c for c in aioclient_mock.mock_calls if c[1].path == "/api/status"]) == before + 1


async def test_set_ambient_context_single_field_and_clearing_values(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Only given fields are sent; an empty list or text is passed on (the engine clears it)."""
    await setup_engine(hass, aioclient_mock)
    aioclient_mock.post(f"{BASE}/api/context", json=context_payload())

    await _call(hass, "set_ambient_context", {"weather": "sunny"})
    assert _calls(aioclient_mock, "POST")[-1][2] == {"weather": "sunny"}

    await _call(hass, "set_ambient_context", {"presence": [], "weather": ""})
    assert _calls(aioclient_mock, "POST")[-1][2] == {"presence": [], "weather": ""}


async def test_set_ambient_context_needs_a_field(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """ttl_minutes alone is not a request."""
    await setup_engine(hass, aioclient_mock)
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "set_ambient_context", {"ttl_minutes": 30})
    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "no_context_fields"
    assert not _calls(aioclient_mock, "POST")


@pytest.mark.parametrize(
    "data",
    [
        {"ttl_minutes": 0, "weather": "rainy"},
        {"ttl_minutes": 10081, "weather": "rainy"},
        {"season": "monsoon"},
        {"weather": "x" * 81},
        {"presence": [str(i) for i in range(21)]},
        {"boost_tags": [str(i) for i in range(21)]},
        {"is_locked": True, "weather": "rainy"},
    ],
    ids=["ttl 0", "ttl too big", "season", "weather length", "presence 21", "tags 21", "is_locked"],
)
async def test_set_ambient_context_schema_limits(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, data: dict[str, Any]
) -> None:
    """Limits from openapi.json are enforced before anything is sent; no invented fields."""
    await setup_engine(hass, aioclient_mock)
    with pytest.raises(vol.Invalid):
        await _call(hass, "set_ambient_context", data)
    assert not _calls(aioclient_mock, "POST")


async def test_ambient_services_boundaries_are_accepted(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The documented bounds themselves (1 and 10080 minutes, 20 items, 80 chars) are valid."""
    await setup_engine(hass, aioclient_mock)
    aioclient_mock.post(f"{BASE}/api/context", json=context_payload())
    for ttl in (1, 10080):
        await _call(hass, "set_ambient_context", {"weather": "w" * 80, "ttl_minutes": ttl})
    await _call(hass, "set_ambient_context", {"boost_tags": [str(i) for i in range(20)]})
    assert len(_calls(aioclient_mock, "POST")) == 3


# -- clear_ambient_context -------------------------------------------------------------


async def test_clear_ambient_context(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """DELETE /api/context, with or without ?field=."""
    await setup_engine(hass, aioclient_mock)
    aioclient_mock.delete(f"{BASE}/api/context", json=context_payload())

    await _call(hass, "clear_ambient_context")
    method, url, _, _ = _calls(aioclient_mock, "DELETE")[-1]
    assert (method, url.path, dict(url.query)) == ("DELETE", "/api/context", {})

    await _call(hass, "clear_ambient_context", {"field": "boost_people"})
    assert dict(_calls(aioclient_mock, "DELETE")[-1][1].query) == {"field": "boost_people"}


async def test_clear_ambient_context_rejects_unknown_field(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Only the six documented fields may be cleared."""
    await setup_engine(hass, aioclient_mock)
    with pytest.raises(vol.Invalid):
        await _call(hass, "clear_ambient_context", {"field": "is_locked"})


# -- send_title_card -------------------------------------------------------------------


async def test_send_title_card_minimal(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Only the title is required; the engine's defaults apply for the rest."""
    await setup_engine(hass, aioclient_mock)
    aioclient_mock.post(f"{BASE}/api/display/title-card", json=display_payload())
    await _call(hass, "send_title_card", {"title": "Hello"})
    assert _calls(aioclient_mock, "POST")[-1][2] == {"title": "Hello"}


async def test_send_title_card_with_every_parameter(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """All documented parameters are forwarded."""
    await setup_engine(hass, aioclient_mock, api_key=API_KEY, auth="key")
    aioclient_mock.post(f"{BASE}/api/display/title-card", json=display_payload())
    await _call(
        hass,
        "send_title_card",
        {
            "title": "Dinner",
            "subtitle": "is ready",
            "caption": "7 pm",
            "accent": "green",
            "ephemeral": False,
            "ttl_minutes": 15,
            "hold_minutes": 30,
            "push_to_fraimic": False,
        },
    )
    _, url, body, headers = _calls(aioclient_mock, "POST")[-1]
    assert url.path == "/api/display/title-card"
    assert body == {
        "title": "Dinner",
        "subtitle": "is ready",
        "caption": "7 pm",
        "accent": "green",
        "ephemeral": False,
        "ttl_minutes": 15,
        "hold_minutes": 30,
        "push_to_fraimic": False,
    }
    assert headers["X-API-Key"] == API_KEY


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"title": ""},
        {"title": "x" * 201},
        {"title": "ok", "subtitle": "x" * 201},
        {"title": "ok", "caption": "x" * 201},
        {"title": "ok", "accent": "purple"},
        {"title": "ok", "ttl_minutes": 0},
        {"title": "ok", "hold_minutes": 10081},
    ],
    ids=["no title", "empty", "title 201", "subtitle 201", "caption 201", "accent", "ttl", "hold"],
)
async def test_send_title_card_schema_limits(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, data: dict[str, Any]
) -> None:
    """Limits of TitleCardRequest are enforced before any request."""
    await setup_engine(hass, aioclient_mock)
    with pytest.raises(vol.Invalid):
        await _call(hass, "send_title_card", data)
    assert not _calls(aioclient_mock, "POST")


async def test_send_title_card_accepts_the_bounds(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """200-character texts and the 1..10080 minute range are valid."""
    await setup_engine(hass, aioclient_mock)
    aioclient_mock.post(f"{BASE}/api/display/title-card", json=display_payload())
    await _call(
        hass,
        "send_title_card",
        {"title": "t" * 200, "subtitle": "s" * 200, "ttl_minutes": 10080, "hold_minutes": 1},
    )
    assert len(_calls(aioclient_mock, "POST")) == 1


# -- Capability gating -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("service", "data", "missing", "available"),
    [
        (
            "set_ambient_context",
            {"weather": "rainy"},
            CAPABILITY_AMBIENT_CONTEXT,
            {CAPABILITY_TITLE_CARD},
        ),
        ("clear_ambient_context", {}, CAPABILITY_AMBIENT_CONTEXT, {CAPABILITY_TITLE_CARD}),
        ("send_title_card", {"title": "Hi"}, CAPABILITY_TITLE_CARD, {CAPABILITY_AMBIENT_CONTEXT}),
    ],
)
async def test_unsupported_capability_raises_before_any_request(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    service: str,
    data: dict[str, Any],
    missing: str,
    available: set[str],
) -> None:
    """An engine without the capability gets a translated ServiceValidationError."""
    entry = await setup_engine(hass, aioclient_mock, capabilities=available)
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, service, data)

    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "capability_not_supported"
    assert err.value.translation_placeholders == {"capability": missing, "name": entry.title}
    assert not [c for c in aioclient_mock.mock_calls if c[0] in ("POST", "DELETE")]


# -- Targeting -------------------------------------------------------------------------


async def test_unknown_config_entry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A config_entry_id that does not exist is reported."""
    await setup_engine(hass, aioclient_mock)
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "send_title_card", {"title": "Hi", "config_entry_id": "nope"})
    assert err.value.translation_key == "entry_not_found"
    assert err.value.translation_placeholders == {"entry_id": "nope"}


async def test_no_engine_loaded(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """With the engine unreachable (entry not loaded) the service says so."""
    aioclient_mock.get(f"{BASE}/api/health", exc=TimeoutError)
    entry = make_entry()
    entry.add_to_hass(hass)
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "send_title_card", {"title": "Hi"})
    assert err.value.translation_key == "no_engine_loaded"


async def test_several_engines_need_an_explicit_target(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """With two engines the call must say which; with config_entry_id only that one is used."""
    first = await setup_engine(hass, aioclient_mock)
    second = await setup_engine(
        hass,
        aioclient_mock,
        base=OTHER_BASE,
        instance_id=OTHER_INSTANCE_ID,
        entry=make_entry(unique_id=OTHER_INSTANCE_ID, host="192.168.1.51"),
    )
    aioclient_mock.post(f"{BASE}/api/display/title-card", json=display_payload())
    aioclient_mock.post(f"{OTHER_BASE}/api/display/title-card", json=display_payload())

    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "send_title_card", {"title": "Hi"})
    assert err.value.translation_key == "config_entry_required"
    assert not _calls(aioclient_mock, "POST")

    await _call(hass, "send_title_card", {"title": "Hi", "config_entry_id": second.entry_id})
    posts = _calls(aioclient_mock, "POST")
    assert [str(c[1].host) for c in posts] == ["192.168.1.51"]
    assert first.entry_id != second.entry_id


async def test_every_target_is_checked_before_any_is_called(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The capability check is part of resolving the target: nothing is sent on failure."""
    await setup_engine(hass, aioclient_mock, capabilities={CAPABILITY_AMBIENT_CONTEXT})
    aioclient_mock.post(f"{BASE}/api/display/title-card", json=display_payload())
    with pytest.raises(ServiceValidationError):
        await _call(hass, "send_title_card", {"title": "Hi"})
    assert not _calls(aioclient_mock, "POST")


# -- Engine errors ---------------------------------------------------------------------


async def test_engine_422_is_a_validation_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A request the engine rejects is reported as invalid input, with its message."""
    await setup_engine(hass, aioclient_mock)
    aioclient_mock.post(
        f"{BASE}/api/display/title-card", status=422, json={"detail": "title: too short"}
    )
    with pytest.raises(ServiceValidationError, match="title: too short") as err:
        await _call(hass, "send_title_card", {"title": "Hi"})
    assert err.value.translation_key == "request_rejected"


@pytest.mark.parametrize("status", [409, 500, 502])
async def test_engine_failures_are_home_assistant_errors(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, status: int
) -> None:
    """Other engine errors surface as HomeAssistantError (not as bad input)."""
    await setup_engine(hass, aioclient_mock)
    aioclient_mock.delete(f"{BASE}/api/context", status=status, json={"detail": "engine broke"})
    with pytest.raises(HomeAssistantError, match="engine broke") as err:
        await _call(hass, "clear_ambient_context")
    assert not isinstance(err.value, ServiceValidationError)


async def test_unreachable_engine(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Network failures are reported."""
    await setup_engine(hass, aioclient_mock)
    aioclient_mock.post(f"{BASE}/api/display/title-card", exc=TimeoutError)
    with pytest.raises(HomeAssistantError):
        await _call(hass, "send_title_card", {"title": "Hi"})


async def test_rejected_key_starts_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 401 from a service starts reauthentication."""
    await setup_engine(hass, aioclient_mock, api_key=API_KEY, auth="key")
    aioclient_mock.post(f"{BASE}/api/context", status=401, json={"detail": "no"})
    with pytest.raises(HomeAssistantError):
        await _call(hass, "set_ambient_context", {"weather": "rainy"})
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]
