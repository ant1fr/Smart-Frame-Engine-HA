"""Payloads and helpers shared by the tests (shapes follow openapi.json, contract v1.1)."""

from __future__ import annotations

from ipaddress import ip_address
from typing import Any

from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_PORT
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.smart_frame_engine.const import (
    ALL_CAPABILITIES,
    CONF_PATH,
    DOMAIN,
)

HOST = "192.168.1.50"
PORT = 8080
BASE = f"http://{HOST}:{PORT}"
API_KEY = "test-secret-key-0123456789"
INSTANCE_ID = "3f2b8c1e-5d0a-4c7e-9b61-0a1d2e3f4a5b"
OTHER_INSTANCE_ID = "9a8b7c6d-1111-4222-8333-444455556666"
INSTANCE_NAME = "Smart Frame Engine 3f2b8c"


def health_payload(
    *,
    auth: str = "open",
    api_version: int = 1,
    min_api_version: int = 1,
    capabilities: set[str] | frozenset[str] | None = None,
    instance_id: str | None = INSTANCE_ID,
    instance_name: str | None = INSTANCE_NAME,
    version: str = "1.0.0rc2",
) -> dict[str, Any]:
    """Return a ``GET /api/health`` body; ``instance_id=None`` mimics an rc.1 engine."""
    payload: dict[str, Any] = {
        "status": "ok",
        "version": version,
        "api_version": api_version,
        "min_api_version": min_api_version,
        "auth": auth,
        "capabilities": sorted(ALL_CAPABILITIES if capabilities is None else capabilities),
    }
    if instance_id is not None:
        payload["instance_id"] = instance_id
    if instance_name is not None:
        payload["instance_name"] = instance_name
    return payload


def status_payload() -> dict[str, Any]:
    """Return a ``GET /api/status`` body."""
    return {
        "version": "1.0.0rc2",
        "uptime_s": 1234.5,
        "auth_enabled": False,
        "auth_mode": "open",
        "public_image_endpoints": True,
        "current_image": {
            "source": "immich",
            "title": "Summer 2024",
            "template": None,
            "ephemeral": False,
            "pushed": True,
            "content_key": "immich:abc",
            "processed_at": "2026-11-19T10:00:00Z",
            "expires_at": None,
            "bin_size": 1000,
            "dither": "auto",
            "fit": "smart_crop",
            "frame_size": [1200, 1600],
            "orientation": "portrait",
            "panel": "133",
            "rotation": 0,
            "sha256": "0" * 64,
        },
        "ephemeral": {"active": False, "pending": False, "has_base": False},
        "frame": {
            "configured": True,
            "frame_size": [1200, 1600],
            "orientation": "portrait",
            "panel": "133",
            "panel_label": "13.3 inch",
            "power_mode": "deepsleep",
            "telemetry": {"battery": 80},
        },
        "delivery": {
            "pending": False,
            "last_delivery": {
                "bytes": 1,
                "delivered_at": "2026-11-19T10:00:05Z",
                "duration_s": 1.2,
                "reason": "submit",
                "sha256": "0" * 64,
            },
        },
        "scheduler": {
            "enabled": True,
            "running": False,
            "in_quiet_hours": False,
            "interval_hours": 6.0,
            "next_run_at": "2026-11-19T16:00:00+01:00",
            "last_display_at": "2026-11-19T10:00:00Z",
            "hold_until": None,
            "last_run": {"at": "2026-11-19T10:00:00Z", "reason": "scheduled", "status": "ok"},
            "ambient": {"weather": {"value": "rainy", "expires_at": "2026-11-19T12:00:00Z"}},
        },
        "services": {"immich": True, "tmdb": False},
    }


def context_payload() -> dict[str, Any]:
    """Return a ``GET /api/context`` body."""
    return {
        "context": {
            "family_mode": {"value": True, "expires_at": "2026-11-19T12:00:00Z"},
            "presence": {"values": ["Alice", "Bob"], "expires_at": "2026-11-19T12:00:00Z"},
        },
        "extra_candidates": ["rainy"],
        "family_active": True,
        "smart_mix_enabled": True,
    }


def config_payload(*, family_enabled: bool = False) -> dict[str, Any]:
    """Return a ``GET``/``PUT /api/config`` body."""
    return {
        "config": {"family": {"enabled": family_enabled, "members": []}},
        "frame_size": [1200, 1600],
        "sources": {"family.enabled": "default"},
    }


def delivery_payload(status: str = "delivered", reason: str = "force") -> dict[str, Any]:
    """Return a ``DeliveryOutcome`` body."""
    return {"status": status, "reason": reason, "error": None, "duration_s": 1.5, "sha256": "ab"}


def display_payload() -> dict[str, Any]:
    """Return a ``DisplayResult`` body."""
    return {
        "image": status_payload()["current_image"],
        "delivery": delivery_payload("delivered", "submit"),
        "deduplicated": False,
    }


def mock_engine(
    aioclient_mock: AiohttpClientMocker,
    *,
    base: str = BASE,
    **health_kwargs: Any,
) -> None:
    """Register the endpoints polled by the coordinator for a healthy engine."""
    health = health_payload(**health_kwargs)
    aioclient_mock.get(f"{base}/api/health", json=health)
    aioclient_mock.get(f"{base}/api/status", json=status_payload())
    aioclient_mock.get(f"{base}/api/context", json=context_payload())
    aioclient_mock.get(f"{base}/api/config", json=config_payload())


def make_entry(
    *, api_key: str | None = None, unique_id: str | None = INSTANCE_ID, host: str = HOST
) -> MockConfigEntry:
    """Return a config entry for the test engine."""
    data: dict[str, Any] = {CONF_HOST: host, CONF_PORT: PORT, CONF_PATH: "/"}
    if api_key:
        data[CONF_API_KEY] = api_key
    return MockConfigEntry(
        domain=DOMAIN, unique_id=unique_id, title=INSTANCE_NAME, data=data, version=1
    )


def zeroconf_info(
    *,
    host: str = HOST,
    port: int = PORT,
    instance_id: str | None = INSTANCE_ID,
    auth: str = "open",
    api_version: str = "1",
    min_api_version: str = "1",
    path: str = "/",
) -> ZeroconfServiceInfo:
    """Return a discovery record of ``_smartframe._tcp.local.``."""
    properties: dict[str, str | None] = {
        "version": "1.0.0rc2",
        "api_version": api_version,
        "min_api_version": min_api_version,
        "path": path,
        "auth": auth,
    }
    if instance_id is not None:
        properties["instance_id"] = instance_id
    return ZeroconfServiceInfo(
        ip_address=ip_address(host),
        ip_addresses=[ip_address(host)],
        hostname="engine.local.",
        name=f"{INSTANCE_NAME}._smartframe._tcp.local.",
        port=port,
        properties=properties,
        type="_smartframe._tcp.local.",
    )
