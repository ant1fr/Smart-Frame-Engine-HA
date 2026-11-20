"""Tests for the config flow: manual setup, Zeroconf, reauth and options."""

from __future__ import annotations

import ssl
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
from homeassistant.config_entries import SOURCE_USER, SOURCE_ZEROCONF
from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_PORT, CONF_SSL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.smart_frame_engine.config_flow import _parse_address
from custom_components.smart_frame_engine.const import (
    CONF_PATH,
    CONF_SCAN_INTERVAL,
    DOMAIN,
)

from .common import (
    API_KEY,
    BASE,
    HOST,
    INSTANCE_ID,
    INSTANCE_NAME,
    OTHER_INSTANCE_ID,
    PORT,
    config_payload,
    health_payload,
    make_entry,
    zeroconf_info,
)

pytestmark = pytest.mark.usefixtures("mock_setup_entry")


async def _manual(hass: HomeAssistant) -> dict[str, Any]:
    """Open the manual flow and submit host and port."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST, CONF_PORT: PORT}
    )


async def _zeroconf(hass: HomeAssistant, **kwargs: Any) -> dict[str, Any]:
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=zeroconf_info(**kwargs)
    )


# -- Manual setup ----------------------------------------------------------------------


async def test_user_flow_open(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_setup_entry: AsyncMock
) -> None:
    """Open auth mode: host/port -> entry, no key asked, unique id is the instance id."""
    aioclient_mock.get(f"{BASE}/api/health", json=health_payload(auth="open"))

    result = await _manual(hass)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == INSTANCE_NAME
    assert result["data"] == {
        CONF_HOST: HOST,
        CONF_PORT: PORT,
        CONF_PATH: "/",
        CONF_SSL: False,
        CONF_VERIFY_SSL: True,
    }
    assert result["result"].unique_id == INSTANCE_ID
    assert len(mock_setup_entry.mock_calls) == 1


@pytest.mark.parametrize("auth", ["key", "password"])
async def test_user_flow_key(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, auth: str
) -> None:
    """key/password modes: ask for the key, reject a wrong one, accept the right one."""
    aioclient_mock.get(f"{BASE}/api/health", json=health_payload(auth=auth))
    aioclient_mock.get(f"{BASE}/api/config", status=401, json={"detail": "Missing"})

    result = await _manual(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "auth"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "wrong"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}/api/config", json=config_payload())
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: f" {API_KEY} "}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_API_KEY] == API_KEY
    assert aioclient_mock.mock_calls[-1][3]["X-API-Key"] == API_KEY


async def test_auth_step_cannot_connect(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A network failure while validating the key is reported as cannot_connect."""
    aioclient_mock.get(f"{BASE}/api/health", json=health_payload(auth="key"))
    aioclient_mock.get(f"{BASE}/api/config", exc=aiohttp.ClientError("down"))

    result = await _manual(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: API_KEY}
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_cannot_connect_then_recovers(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An unreachable host shows an error and the form can be resubmitted."""
    aioclient_mock.get(f"{BASE}/api/health", exc=TimeoutError)
    result = await _manual(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}/api/health", json=health_payload())
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST, CONF_PORT: PORT}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_unexpected_error(hass: HomeAssistant) -> None:
    """Anything unexpected is shown as 'unknown' instead of crashing the flow."""
    with patch(
        "custom_components.smart_frame_engine.config_flow.SmartFrameClient.async_get_health",
        side_effect=RuntimeError("bug"),
    ):
        result = await _manual(hass)
    assert result["errors"] == {"base": "unknown"}


@pytest.mark.parametrize(
    ("api_version", "min_api_version", "reason"),
    [(0, 0, "server_outdated"), (2, 2, "integration_outdated")],
)
async def test_user_flow_incompatible(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    api_version: int,
    min_api_version: int,
    reason: str,
) -> None:
    """An incompatible engine aborts and says which side to update."""
    aioclient_mock.get(
        f"{BASE}/api/health",
        json=health_payload(api_version=api_version, min_api_version=min_api_version),
    )
    result = await _manual(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == reason


async def test_user_flow_engine_without_instance_id(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An engine older than 1.0.0rc2 cannot be keyed: abort with a clear reason."""
    aioclient_mock.get(
        f"{BASE}/api/health", json=health_payload(instance_id=None, instance_name=None)
    )
    result = await _manual(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "instance_id_missing"


async def test_user_flow_duplicate_updates_address(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The same engine reached at a new address is not added twice; its host is updated."""
    entry = make_entry(host="192.168.1.99")
    entry.add_to_hass(hass)
    aioclient_mock.get(f"{BASE}/api/health", json=health_payload())

    result = await _manual(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == HOST
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


# -- HTTPS / pasted addresses ------------------------------------------------------------

HTTPS_HOST = "frame.example.org"
HTTPS_BASE = f"https://{HTTPS_HOST}"


async def _submit(hass: HomeAssistant, user_input: dict[str, Any]) -> dict[str, Any]:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    return await hass.config_entries.flow.async_configure(result["flow_id"], user_input)


@pytest.mark.parametrize(
    ("typed", "port", "ssl", "expected"),
    [
        ("192.168.1.50", 8080, False, ("192.168.1.50", 8080, False, "/")),
        ("  engine.local ", 8080, False, ("engine.local", 8080, False, "/")),
        ("engine.local", 8080, True, ("engine.local", 8080, True, "/")),
        ("engine.local:9000", 8080, False, ("engine.local", 9000, False, "/")),
        ("2001:db8::1", 8080, False, ("2001:db8::1", 8080, False, "/")),
        ("[2001:db8::1]:9000", 8080, False, ("2001:db8::1", 9000, False, "/")),
        # a scheme decides ssl, and the port field is only kept when the user changed it
        ("https://frame.example.org", 443, False, ("frame.example.org", 443, True, "/")),
        ("https://frame.example.org", 8080, False, ("frame.example.org", 443, True, "/")),
        ("https://frame.example.org", 8443, False, ("frame.example.org", 8443, True, "/")),
        ("https://frame.example.org:9443/", 8080, False, ("frame.example.org", 9443, True, "/")),
        ("http://frame.example.org", 8080, True, ("frame.example.org", 80, False, "/")),
        ("http://192.168.1.20:8080", 8080, True, ("192.168.1.20", 8080, False, "/")),
        ("HTTPS://Frame.Example.org", 443, False, ("frame.example.org", 443, True, "/")),
        # a path becomes ROOT_PATH, a query or fragment is ignored
        ("https://example.org/frame", 443, False, ("example.org", 443, True, "/frame")),
        ("https://example.org/frame/?a=1#b", 443, False, ("example.org", 443, True, "/frame/")),
        ("example.org/frame", 8080, False, ("example.org", 8080, False, "/frame")),
    ],
)
def test_parse_address(typed: str, port: int, ssl: bool, expected: tuple[Any, ...]) -> None:
    """Hosts, IPs and pasted URLs are all understood."""
    assert _parse_address(typed, port, ssl) == expected


@pytest.mark.parametrize(
    "typed", ["", "   ", "http://", "https://:443", "ftp://frame.example.org", "bad host", "a b"]
)
def test_parse_address_rejects_what_has_no_host(typed: str) -> None:
    """Nothing usable as a host is an error the flow can report."""
    with pytest.raises(ValueError, match="invalid host"):
        _parse_address(typed, 8080, False)


async def test_user_flow_https_address(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Pasting https://host with port 443 reaches the engine over HTTPS (the reported case)."""
    aioclient_mock.get(f"{HTTPS_BASE}/api/health", json=health_payload())

    result = await _submit(hass, {CONF_HOST: HTTPS_BASE, CONF_PORT: 443})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_HOST: HTTPS_HOST,
        CONF_PORT: 443,
        CONF_PATH: "/",
        CONF_SSL: True,
        CONF_VERIFY_SSL: True,
    }
    assert str(aioclient_mock.mock_calls[0][1]) == f"{HTTPS_BASE}/api/health"


async def test_user_flow_https_toggle_with_plain_host(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The 'Use HTTPS' switch works without typing a scheme."""
    aioclient_mock.get(f"{HTTPS_BASE}/api/health", json=health_payload())

    result = await _submit(hass, {CONF_HOST: HTTPS_HOST, CONF_PORT: 443, CONF_SSL: True})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SSL] is True
    assert result["data"][CONF_HOST] == HTTPS_HOST


async def test_user_flow_https_with_root_path(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An address with a path is stored as the engine's ROOT_PATH."""
    aioclient_mock.get(f"{HTTPS_BASE}/frame/api/health", json=health_payload())

    result = await _submit(hass, {CONF_HOST: f"{HTTPS_BASE}/frame", CONF_PORT: 443})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PATH] == "/frame"


@pytest.mark.parametrize("typed", ["", "http://", "ftp://frame.example.org", "bad host"])
async def test_user_flow_invalid_host(hass: HomeAssistant, typed: str) -> None:
    """A host that cannot be read is reported as such, not as 'unknown'."""
    result = await _submit(hass, {CONF_HOST: typed, CONF_PORT: PORT})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_host"}


async def test_user_flow_certificate_error_then_unverified(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A certificate that cannot be verified has its own message; turning the check off works."""
    aioclient_mock.get(
        f"{HTTPS_BASE}/api/health",
        exc=aiohttp.ClientConnectorCertificateError(
            MagicMock(), ssl.SSLCertVerificationError("self-signed certificate")
        ),
    )
    result = await _submit(hass, {CONF_HOST: HTTPS_BASE, CONF_PORT: 443})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "ssl_error"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{HTTPS_BASE}/api/health", json=health_payload())
    with patch(
        "custom_components.smart_frame_engine.config_flow.async_get_clientsession",
        wraps=async_get_clientsession,
    ) as get_session:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: HTTPS_BASE, CONF_PORT: 443, CONF_SSL: True, CONF_VERIFY_SSL: False},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_VERIFY_SSL] is False
    assert get_session.call_args.kwargs == {"verify_ssl": False}


async def test_user_flow_https_key_step_uses_https(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The API key is validated over the same HTTPS address."""
    aioclient_mock.get(f"{HTTPS_BASE}/api/health", json=health_payload(auth="key"))
    aioclient_mock.get(f"{HTTPS_BASE}/api/config", json=config_payload())

    result = await _submit(hass, {CONF_HOST: HTTPS_BASE, CONF_PORT: 443})
    assert result["step_id"] == "auth"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: API_KEY}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_API_KEY] == API_KEY
    assert result["data"][CONF_SSL] is True
    assert str(aioclient_mock.mock_calls[-1][1]) == f"{HTTPS_BASE}/api/config"


async def test_user_flow_duplicate_switches_known_engine_to_https(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Adding a known engine again through its HTTPS address updates the entry."""
    entry = make_entry()
    entry.add_to_hass(hass)
    aioclient_mock.get(f"{HTTPS_BASE}/api/health", json=health_payload())

    result = await _submit(hass, {CONF_HOST: HTTPS_BASE, CONF_PORT: 443})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == HTTPS_HOST
    assert entry.data[CONF_PORT] == 443
    assert entry.data[CONF_SSL] is True


async def test_reauth_uses_https_and_certificate_setting(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Reauthentication talks to an HTTPS engine the way it was set up."""
    entry = make_entry(api_key="old-key", host=HTTPS_HOST)
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_PORT: 443, CONF_SSL: True, CONF_VERIFY_SSL: False}
    )
    aioclient_mock.get(f"{HTTPS_BASE}/api/health", json=health_payload(auth="key"))
    aioclient_mock.get(f"{HTTPS_BASE}/api/config", json=config_payload())

    result = await entry.start_reauth_flow(hass)
    with patch(
        "custom_components.smart_frame_engine.config_flow.async_get_clientsession",
        wraps=async_get_clientsession,
    ) as get_session:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_KEY: API_KEY}
        )

    assert result["reason"] == "reauth_successful"
    assert str(aioclient_mock.mock_calls[-1][1]) == f"{HTTPS_BASE}/api/config"
    assert get_session.call_args.kwargs == {"verify_ssl": False}


# -- Zeroconf --------------------------------------------------------------------------


async def test_zeroconf_open(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_setup_entry: AsyncMock
) -> None:
    """auth=open: a plain confirmation, no key requested."""
    aioclient_mock.get(f"{BASE}/api/health", json=health_payload(auth="open"))

    result = await _zeroconf(hass, auth="open")
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "zeroconf_confirm"
    assert result["data_schema"] is None
    assert result["description_placeholders"] == {
        "name": INSTANCE_NAME,
        "host": HOST,
        "port": str(PORT),
    }

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == INSTANCE_NAME
    assert result["data"] == {
        CONF_HOST: HOST,
        CONF_PORT: PORT,
        CONF_PATH: "/",
        CONF_SSL: False,
        CONF_VERIFY_SSL: True,
    }
    assert result["result"].unique_id == INSTANCE_ID
    assert len(mock_setup_entry.mock_calls) == 1


async def test_zeroconf_key_asks_for_api_key(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """auth=key: the confirmation step asks for, and validates, the API key."""
    aioclient_mock.get(f"{BASE}/api/health", json=health_payload(auth="key"))
    aioclient_mock.get(f"{BASE}/api/config", status=401, json={"detail": "x"})

    result = await _zeroconf(hass, auth="key")
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "zeroconf_confirm"
    assert CONF_API_KEY in result["data_schema"].schema

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}/api/config", json=config_payload())
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: API_KEY}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_API_KEY] == API_KEY


async def test_zeroconf_uses_path_from_txt(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """ROOT_PATH advertised in TXT is used for requests and stored."""
    aioclient_mock.get(f"{BASE}/frame/api/health", json=health_payload())

    result = await _zeroconf(hass, path="/frame")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PATH] == "/frame"


async def test_zeroconf_known_engine_updates_address_without_network(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A DHCP address change of a known engine is applied from the TXT record alone."""
    entry = make_entry(host="192.168.1.99")
    entry.add_to_hass(hass)

    result = await _zeroconf(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == HOST
    assert aioclient_mock.call_count == 0


async def test_zeroconf_known_https_engine_gets_a_consistent_address(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A discovered LAN address replaces host, port and scheme together, never a mix."""
    entry = make_entry(host=HTTPS_HOST)
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_PORT: 443, CONF_SSL: True, CONF_VERIFY_SSL: False}
    )

    result = await _zeroconf(hass)

    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == HOST
    assert entry.data[CONF_PORT] == PORT
    assert entry.data[CONF_SSL] is False
    assert entry.data[CONF_VERIFY_SSL] is True


@pytest.mark.parametrize(
    ("api_version", "min_api_version", "reason"),
    [("0", "0", "server_outdated"), ("2", "2", "integration_outdated")],
)
async def test_zeroconf_incompatible_txt(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    api_version: str,
    min_api_version: str,
    reason: str,
) -> None:
    """The TXT record alone is enough to reject an incompatible engine."""
    result = await _zeroconf(hass, api_version=api_version, min_api_version=min_api_version)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == reason
    assert aioclient_mock.call_count == 0


async def test_zeroconf_incompatible_health(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """GET /api/health is authoritative when TXT looked compatible."""
    aioclient_mock.get(f"{BASE}/api/health", json=health_payload(api_version=0, min_api_version=0))
    result = await _zeroconf(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "server_outdated"


async def test_zeroconf_cannot_connect(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A discovered engine that does not answer aborts."""
    aioclient_mock.get(f"{BASE}/api/health", exc=aiohttp.ClientError("down"))
    result = await _zeroconf(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_zeroconf_instance_id_mismatch(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """If the engine at that address is not the one that advertised, do not trust it."""
    aioclient_mock.get(f"{BASE}/api/health", json=health_payload(instance_id=OTHER_INSTANCE_ID))
    result = await _zeroconf(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_instance"


async def test_zeroconf_without_txt_instance_id_uses_health(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A record without instance_id still works when /api/health provides one."""
    aioclient_mock.get(f"{BASE}/api/health", json=health_payload())
    result = await _zeroconf(hass, instance_id=None)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == INSTANCE_ID


async def test_zeroconf_engine_without_any_instance_id(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """rc.1 engines (no instance id anywhere) are rejected with a clear reason."""
    aioclient_mock.get(
        f"{BASE}/api/health", json=health_payload(instance_id=None, instance_name=None)
    )
    result = await _zeroconf(hass, instance_id=None)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "instance_id_missing"


# -- Reauth ----------------------------------------------------------------------------


async def test_reauth_success(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A new valid key replaces the old one."""
    entry = make_entry(api_key="old-key")
    entry.add_to_hass(hass)
    aioclient_mock.get(f"{BASE}/api/health", json=health_payload(auth="key"))
    aioclient_mock.get(f"{BASE}/api/config", status=401, json={"detail": "x"})

    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"] == {"name": INSTANCE_NAME}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "still-wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}/api/health", json=health_payload(auth="key"))
    aioclient_mock.get(f"{BASE}/api/config", json=config_payload())
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: API_KEY}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_KEY] == API_KEY


async def test_reauth_cannot_connect(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Network failures during reauth keep the form open."""
    entry = make_entry(api_key="old-key")
    entry.add_to_hass(hass)
    aioclient_mock.get(f"{BASE}/api/health", exc=TimeoutError)

    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: API_KEY}
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_reauth_wrong_instance(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Never store a key for a different engine that took over the address."""
    entry = make_entry(api_key="old-key")
    entry.add_to_hass(hass)
    aioclient_mock.get(
        f"{BASE}/api/health", json=health_payload(auth="key", instance_id=OTHER_INSTANCE_ID)
    )
    aioclient_mock.get(f"{BASE}/api/config", json=config_payload())

    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: API_KEY}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_instance"
    assert entry.data[CONF_API_KEY] == "old-key"


@pytest.mark.parametrize(
    ("api_version", "min_api_version", "reason"),
    [(0, 0, "server_outdated"), (2, 2, "integration_outdated")],
)
async def test_reauth_incompatible(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    api_version: int,
    min_api_version: int,
    reason: str,
) -> None:
    """An engine that became incompatible aborts reauth with the right reason."""
    entry = make_entry(api_key="old-key")
    entry.add_to_hass(hass)
    aioclient_mock.get(
        f"{BASE}/api/health",
        json=health_payload(auth="key", api_version=api_version, min_api_version=min_api_version),
    )
    aioclient_mock.get(f"{BASE}/api/config", json=config_payload())

    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: API_KEY}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == reason


# -- Options ---------------------------------------------------------------------------


async def test_options_flow_scan_interval(hass: HomeAssistant) -> None:
    """The polling interval is configurable and defaults to 30 seconds."""
    entry = make_entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    assert next(iter(result["data_schema"].schema)).default() == 30

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 45}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_SCAN_INTERVAL: 45}
