"""Async client and typed models for the Smart Frame Engine HTTP API.

Every path, method, parameter and field here is taken from ``docs/API_CONTRACT.md`` and
``docs/openapi.json`` of https://github.com/ant1fr/Smart-Frame-Engine (contract v1.1).
Unknown response fields and unknown capability flags are ignored on purpose (contract
section 1, "Graceful degradation").

The client never creates its own session: Home Assistant's shared one is injected.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
import enum
import json
from typing import Any, Final, Literal, TypeIs

import aiohttp
from yarl import URL

from .const import (
    AUTH_OPEN,
    DEFAULT_PATH,
    REQUEST_TIMEOUT,
    SUPPORTED_API_VERSION,
)

# --------------------------------------------------------------------------------------
# Exceptions
# --------------------------------------------------------------------------------------


class SmartFrameError(Exception):
    """Base class for every error raised by this client."""


class SmartFrameConnectionError(SmartFrameError):
    """The engine could not be reached, timed out, or did not answer like an engine."""


class SmartFrameAuthError(SmartFrameError):
    """HTTP 401: credentials are missing or wrong."""


class SmartFrameApiError(SmartFrameError):
    """The engine answered with an error status (other than 401)."""

    def __init__(self, status: int, detail: str) -> None:
        """Store the HTTP status and the engine's ``detail`` message."""
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


class SmartFrameValidationError(SmartFrameApiError):
    """HTTP 422: the engine rejected the request payload."""


UpdateTarget = Literal["integration", "server"]


class SmartFrameIncompatibleError(SmartFrameError):
    """The engine speaks a contract version this integration cannot use."""

    def __init__(self, api_version: int, min_api_version: int, update_target: UpdateTarget) -> None:
        """Store both sides of the mismatch and which one must be updated."""
        super().__init__(
            f"Engine speaks contract {min_api_version}..{api_version}, "
            f"integration needs {SUPPORTED_API_VERSION} (update the {update_target})"
        )
        self.api_version = api_version
        self.min_api_version = min_api_version
        self.update_target: UpdateTarget = update_target


# --------------------------------------------------------------------------------------
# Compatibility handshake
# --------------------------------------------------------------------------------------


def compatibility_target(
    api_version: int, min_api_version: int, supported: int = SUPPORTED_API_VERSION
) -> UpdateTarget | None:
    """Return which side must be updated, or ``None`` when the versions are compatible.

    Contract rule: compatible iff ``min_api_version <= supported <= api_version``.
    ``supported > api_version`` means the engine is older than this integration.
    ``supported < min_api_version`` means this integration is older than the engine.
    """
    if supported > api_version:
        return "server"
    if supported < min_api_version:
        return "integration"
    return None


# --------------------------------------------------------------------------------------
# Tolerant JSON helpers
# --------------------------------------------------------------------------------------


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _bool(value: Any, default: bool = False) -> bool:
    return value if isinstance(value, bool) else default


def _is_int(value: Any) -> TypeIs[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _str_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


# --------------------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HealthInfo:
    """``GET /api/health``: liveness and compatibility handshake (always public)."""

    version: str
    api_version: int
    min_api_version: int
    auth: str
    capabilities: frozenset[str]
    # Contract v1.1. ``None`` on engines older than 1.0.0rc2.
    instance_id: str | None = None
    instance_name: str | None = None

    @classmethod
    def from_dict(cls, data: Any) -> HealthInfo:
        """Parse a health payload, rejecting anything that is not an engine."""
        if not isinstance(data, Mapping):
            raise SmartFrameConnectionError("Unexpected health response (not an object)")
        version = data.get("version")
        api_version = data.get("api_version")
        min_api_version = data.get("min_api_version")
        auth = data.get("auth")
        capabilities = data.get("capabilities")
        if not (
            isinstance(version, str)
            and _is_int(api_version)
            and _is_int(min_api_version)
            and isinstance(auth, str)
            and isinstance(capabilities, list)
        ):
            raise SmartFrameConnectionError("Unexpected health response (missing fields)")
        return cls(
            version=version,
            api_version=api_version,
            min_api_version=min_api_version,
            auth=auth,
            capabilities=frozenset(c for c in capabilities if isinstance(c, str)),
            instance_id=_str(data.get("instance_id")) or None,
            instance_name=_str(data.get("instance_name")) or None,
        )

    @property
    def requires_key(self) -> bool:
        """Whether some endpoints need credentials (any mode other than ``open``)."""
        return self.auth != AUTH_OPEN

    @property
    def update_target(self) -> UpdateTarget | None:
        """Which side must be updated, or ``None`` when compatible."""
        return compatibility_target(self.api_version, self.min_api_version)

    @property
    def is_compatible(self) -> bool:
        """Whether this engine speaks a contract version this integration supports."""
        return self.update_target is None

    def ensure_compatible(self) -> None:
        """Raise ``SmartFrameIncompatibleError`` when the engine cannot be used."""
        target = self.update_target
        if target is not None:
            raise SmartFrameIncompatibleError(self.api_version, self.min_api_version, target)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable view (used by diagnostics)."""
        return {
            "version": self.version,
            "api_version": self.api_version,
            "min_api_version": self.min_api_version,
            "auth": self.auth,
            "capabilities": sorted(self.capabilities),
            "instance_id": self.instance_id,
            "instance_name": self.instance_name,
        }


@dataclass(frozen=True, slots=True)
class CurrentImage:
    """Metadata of a rendered image (``CurrentImageMeta``)."""

    source: str | None = None
    title: str | None = None
    template: str | None = None
    ephemeral: bool = False
    pushed: bool = True
    content_key: str | None = None
    processed_at: datetime | None = None
    expires_at: datetime | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CurrentImage:
        """Parse ``CurrentImageMeta`` tolerantly."""
        return cls(
            source=_str(data.get("source")),
            title=_str(data.get("title")),
            template=_str(data.get("template")),
            ephemeral=_bool(data.get("ephemeral")),
            pushed=_bool(data.get("pushed"), True),
            content_key=_str(data.get("content_key")),
            processed_at=_dt(data.get("processed_at")),
            expires_at=_dt(data.get("expires_at")),
        )


@dataclass(frozen=True, slots=True)
class EphemeralState:
    """The temporary image (now playing, poster, title card) shown or queued."""

    active: bool = False
    pending: bool = False
    has_base: bool = False
    expires_at: datetime | None = None
    source: str | None = None
    template: str | None = None
    title: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EphemeralState:
        """Parse ``EphemeralView`` tolerantly."""
        return cls(
            active=_bool(data.get("active")),
            pending=_bool(data.get("pending")),
            has_base=_bool(data.get("has_base")),
            expires_at=_dt(data.get("expires_at")),
            source=_str(data.get("source")),
            template=_str(data.get("template")),
            title=_str(data.get("title")),
        )


@dataclass(frozen=True, slots=True)
class FrameState:
    """Configured frame characteristics (``FrameStatus``)."""

    configured: bool = False
    panel: str | None = None
    panel_label: str | None = None
    orientation: str | None = None
    power_mode: str | None = None
    telemetry: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FrameState:
        """Parse ``FrameStatus`` tolerantly."""
        return cls(
            configured=_bool(data.get("configured")),
            panel=_str(data.get("panel")),
            panel_label=_str(data.get("panel_label")),
            orientation=_str(data.get("orientation")),
            power_mode=_str(data.get("power_mode")),
            telemetry=dict(_mapping(data.get("telemetry"))),
        )


@dataclass(frozen=True, slots=True)
class DeliveryState:
    """Upload queue state (``DeliveryStatus``)."""

    pending: bool = False
    last_delivered_at: datetime | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DeliveryState:
        """Parse ``DeliveryStatus`` tolerantly."""
        return cls(
            pending=_bool(data.get("pending")),
            last_delivered_at=_dt(_mapping(data.get("last_delivery")).get("delivered_at")),
        )


@dataclass(frozen=True, slots=True)
class TimedFlag:
    """A boolean with an expiry."""

    value: bool
    expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class TimedText:
    """One word or phrase with an expiry."""

    value: str
    expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class TimedList:
    """A list of names or phrases with an expiry."""

    values: tuple[str, ...]
    expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class AmbientState:
    """The live ambient fields (``None`` = not set)."""

    family_mode: TimedFlag | None = None
    presence: TimedList | None = None
    weather: TimedText | None = None
    season: TimedText | None = None
    boost_tags: TimedList | None = None
    boost_people: TimedList | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AmbientState:
        """Parse ``AmbientState`` tolerantly."""

        def flag(key: str) -> TimedFlag | None:
            raw = data.get(key)
            if not isinstance(raw, Mapping) or not isinstance(raw.get("value"), bool):
                return None
            return TimedFlag(raw["value"], _dt(raw.get("expires_at")))

        def text(key: str) -> TimedText | None:
            raw = data.get(key)
            if not isinstance(raw, Mapping) or not isinstance(raw.get("value"), str):
                return None
            return TimedText(raw["value"], _dt(raw.get("expires_at")))

        def items(key: str) -> TimedList | None:
            raw = data.get(key)
            if not isinstance(raw, Mapping) or not isinstance(raw.get("values"), list):
                return None
            return TimedList(_str_tuple(raw["values"]), _dt(raw.get("expires_at")))

        return cls(
            family_mode=flag("family_mode"),
            presence=items("presence"),
            weather=text("weather"),
            season=text("season"),
            boost_tags=items("boost_tags"),
            boost_people=items("boost_people"),
        )


@dataclass(frozen=True, slots=True)
class SchedulerState:
    """Autonomous refresh state (``SchedulerSnapshot``)."""

    enabled: bool = False
    running: bool = False
    in_quiet_hours: bool = False
    interval_hours: float | None = None
    next_run_at: datetime | None = None
    last_display_at: datetime | None = None
    hold_until: datetime | None = None
    last_run_status: str | None = None
    last_run_reason: str | None = None
    ambient: AmbientState = field(default_factory=AmbientState)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SchedulerState:
        """Parse ``SchedulerSnapshot`` tolerantly."""
        last_run = _mapping(data.get("last_run"))
        return cls(
            enabled=_bool(data.get("enabled")),
            running=_bool(data.get("running")),
            in_quiet_hours=_bool(data.get("in_quiet_hours")),
            interval_hours=_float(data.get("interval_hours")),
            next_run_at=_dt(data.get("next_run_at")),
            last_display_at=_dt(data.get("last_display_at")),
            hold_until=_dt(data.get("hold_until")),
            last_run_status=_str(last_run.get("status")),
            last_run_reason=_str(last_run.get("reason")),
            ambient=AmbientState.from_dict(_mapping(data.get("ambient"))),
        )


@dataclass(frozen=True, slots=True)
class EngineStatus:
    """``GET /api/status``: overall system status."""

    version: str
    uptime_s: float
    auth_mode: str
    current_image: CurrentImage | None
    ephemeral: EphemeralState
    frame: FrameState
    delivery: DeliveryState
    scheduler: SchedulerState
    immich_configured: bool
    tmdb_configured: bool

    @classmethod
    def from_dict(cls, data: Any) -> EngineStatus:
        """Parse ``StatusResponse``, rejecting anything that is not an object."""
        if not isinstance(data, Mapping):
            raise SmartFrameConnectionError("Unexpected status response (not an object)")
        image = data.get("current_image")
        services = _mapping(data.get("services"))
        return cls(
            version=_str(data.get("version")) or "",
            uptime_s=_float(data.get("uptime_s")) or 0.0,
            auth_mode=_str(data.get("auth_mode")) or "",
            current_image=CurrentImage.from_dict(image) if isinstance(image, Mapping) else None,
            ephemeral=EphemeralState.from_dict(_mapping(data.get("ephemeral"))),
            frame=FrameState.from_dict(_mapping(data.get("frame"))),
            delivery=DeliveryState.from_dict(_mapping(data.get("delivery"))),
            scheduler=SchedulerState.from_dict(_mapping(data.get("scheduler"))),
            immich_configured=_bool(services.get("immich")),
            tmdb_configured=_bool(services.get("tmdb")),
        )


@dataclass(frozen=True, slots=True)
class AmbientView:
    """``GET /api/context``: the live ambient context and what it currently does."""

    context: AmbientState
    family_active: bool = False
    smart_mix_enabled: bool = False
    extra_candidates: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: Any) -> AmbientView:
        """Parse ``AmbientView`` tolerantly."""
        if not isinstance(data, Mapping):
            raise SmartFrameConnectionError("Unexpected context response (not an object)")
        return cls(
            context=AmbientState.from_dict(_mapping(data.get("context"))),
            family_active=_bool(data.get("family_active")),
            smart_mix_enabled=_bool(data.get("smart_mix_enabled")),
            extra_candidates=_str_tuple(data.get("extra_candidates")),
        )


@dataclass(frozen=True, slots=True)
class ConfigView:
    """``GET``/``PUT /api/config``: effective configuration (secrets are masked)."""

    config: Mapping[str, Any]
    sources: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Any) -> ConfigView:
        """Parse ``ConfigView`` tolerantly."""
        if not isinstance(data, Mapping):
            raise SmartFrameConnectionError("Unexpected config response (not an object)")
        return cls(
            config=dict(_mapping(data.get("config"))),
            sources=dict(_mapping(data.get("sources"))),
        )

    @property
    def family_enabled(self) -> bool | None:
        """The persistent Family Mode setting (``config.family.enabled``), if present."""
        enabled = _mapping(self.config.get("family")).get("enabled")
        return enabled if isinstance(enabled, bool) else None


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    """Result of a submit, push or flush (``DeliveryOutcome``)."""

    status: str
    reason: str | None = None
    error: str | None = None
    duration_s: float | None = None
    sha256: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DeliveryOutcome:
        """Parse ``DeliveryOutcome`` tolerantly."""
        return cls(
            status=_str(data.get("status")) or "",
            reason=_str(data.get("reason")),
            error=_str(data.get("error")),
            duration_s=_float(data.get("duration_s")),
            sha256=_str(data.get("sha256")),
        )


@dataclass(frozen=True, slots=True)
class DisplayResult:
    """Result of displaying an image (``DisplayResult``)."""

    image: CurrentImage
    delivery: DeliveryOutcome | None = None
    deduplicated: bool = False

    @classmethod
    def from_dict(cls, data: Any) -> DisplayResult:
        """Parse ``DisplayResult`` tolerantly."""
        if not isinstance(data, Mapping):
            raise SmartFrameConnectionError("Unexpected display response (not an object)")
        delivery = data.get("delivery")
        return cls(
            image=CurrentImage.from_dict(_mapping(data.get("image"))),
            delivery=DeliveryOutcome.from_dict(delivery) if isinstance(delivery, Mapping) else None,
            deduplicated=_bool(data.get("deduplicated")),
        )


@dataclass(frozen=True, slots=True)
class RefreshResult:
    """Outcome of a manual refresh (``RefreshResult``)."""

    display: DisplayResult
    rule_kind: str | None = None
    rule_source: str | None = None
    entry_label: str | None = None

    @classmethod
    def from_dict(cls, data: Any) -> RefreshResult:
        """Parse ``RefreshResult`` tolerantly."""
        if not isinstance(data, Mapping):
            raise SmartFrameConnectionError("Unexpected refresh response (not an object)")
        return cls(
            display=DisplayResult.from_dict(_mapping(data.get("display"))),
            rule_kind=_str(data.get("rule_kind")),
            rule_source=_str(data.get("rule_source")),
            entry_label=_str(data.get("entry_label")),
        )


@dataclass(frozen=True, slots=True)
class WakeupResult:
    """Result of ``POST /api/fraimic/wakeup``.

    A non-blocking wake-up answers ``WakeupAccepted`` (``accepted`` + ``pending``); with
    ``wait=true`` it answers a ``DeliveryOutcome``.
    """

    accepted: bool
    pending: bool | None = None
    outcome: DeliveryOutcome | None = None

    @classmethod
    def from_dict(cls, data: Any) -> WakeupResult:
        """Parse either wake-up response shape."""
        if not isinstance(data, Mapping):
            raise SmartFrameConnectionError("Unexpected wake-up response (not an object)")
        if "accepted" in data:
            pending = data.get("pending")
            return cls(
                accepted=_bool(data.get("accepted"), True),
                pending=pending if isinstance(pending, bool) else None,
            )
        return cls(accepted=True, outcome=DeliveryOutcome.from_dict(data))


# --------------------------------------------------------------------------------------
# Request helpers
# --------------------------------------------------------------------------------------

AmbientField = Literal["presence", "family_mode", "weather", "season", "boost_tags", "boost_people"]
Season = Literal["spring", "summer", "autumn", "winter"]
Accent = Literal["red", "yellow", "blue", "green", "white"]


class _Unset(enum.Enum):
    UNSET = enum.auto()


UNSET: Final = _Unset.UNSET
"""Marker for "argument not given" where ``None`` is itself a meaningful value."""


@dataclass(frozen=True, slots=True)
class WithTtl[T]:
    """An ambient value with its own lifetime, overriding the request-wide ``ttl_minutes``."""

    value: T
    ttl_minutes: int


def _encode_ambient(value: Any) -> Any:
    """Encode one ambient field as a bare value or ``{value|values, ttl_minutes}``."""
    if isinstance(value, WithTtl):
        inner = value.value
        key = "values" if isinstance(inner, Sequence) and not isinstance(inner, str) else "value"
        return {key: list(inner) if key == "values" else inner, "ttl_minutes": value.ttl_minutes}
    if isinstance(value, Sequence) and not isinstance(value, str):
        return list(value)
    return value


def _error_detail(body: bytes) -> str:
    """Extract a readable message from an error body (never echoes submitted values)."""
    text = body.decode("utf-8", errors="replace")
    try:
        payload = json.loads(text)
    except ValueError:
        return text.strip()[:200]
    detail = payload.get("detail") if isinstance(payload, Mapping) else None
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        parts: list[str] = []
        for item in detail:
            if isinstance(item, Mapping):
                loc = ".".join(str(p) for p in item.get("loc", []) if p != "body")
                msg = str(item.get("msg", "")).strip()
                parts.append(f"{loc}: {msg}" if loc else msg)
        return "; ".join(p for p in parts if p)[:500]
    return text.strip()[:200]


@dataclass(frozen=True, slots=True)
class _Response:
    status: int
    body: bytes


# --------------------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------------------


class SmartFrameClient:
    """Async client for one Smart Frame Engine."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        path: str = DEFAULT_PATH,
        api_key: str | None = None,
    ) -> None:
        """Create a client bound to Home Assistant's shared ``session``."""
        self._session = session
        self._host = host
        self._port = port
        stripped = path.strip("/")
        self._base_path = f"/{stripped}" if stripped else ""
        self._api_key = api_key or None

    @property
    def host(self) -> str:
        """Engine host (name or IP)."""
        return self._host

    @property
    def port(self) -> int:
        """Engine port."""
        return self._port

    @property
    def base_url(self) -> str:
        """Root URL of the engine, including its ``ROOT_PATH`` (used for the device link)."""
        return str(
            URL.build(scheme="http", host=self._host, port=self._port, path=self._base_path or "/")
        )

    def _url(self, endpoint: str) -> URL:
        return URL.build(
            scheme="http", host=self._host, port=self._port, path=f"{self._base_path}{endpoint}"
        )

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        params: Mapping[str, str] | None = None,
        ok_statuses: tuple[int, ...] = (200,),
    ) -> _Response:
        """Send one request and map failures onto the client's exceptions."""
        headers: dict[str, str] = {}
        if self._api_key:
            headers["X-API-Key"] = self._api_key
        try:
            async with (
                asyncio.timeout(REQUEST_TIMEOUT),
                self._session.request(
                    method,
                    self._url(endpoint),
                    json=json_body,
                    params=params,
                    headers=headers,
                ) as response,
            ):
                response_body = await response.read()
                status = response.status
        except TimeoutError as err:
            raise SmartFrameConnectionError(f"Timed out talking to {self._host}") from err
        except aiohttp.ClientError as err:
            raise SmartFrameConnectionError(f"Cannot reach {self._host}: {err}") from err

        if status == 401:
            raise SmartFrameAuthError("Missing or invalid API key")
        if status in ok_statuses:
            return _Response(status, response_body)
        detail = _error_detail(response_body)
        if status == 422:
            raise SmartFrameValidationError(status, detail)
        raise SmartFrameApiError(status, detail)

    async def _json(
        self,
        method: str,
        endpoint: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        params: Mapping[str, str] | None = None,
        ok_statuses: tuple[int, ...] = (200,),
    ) -> tuple[int, Any]:
        response = await self._request(
            method, endpoint, json_body=json_body, params=params, ok_statuses=ok_statuses
        )
        try:
            return response.status, json.loads(response.body)
        except ValueError as err:
            raise SmartFrameConnectionError(f"Invalid JSON from {endpoint}") from err

    # -- Health, status, configuration ---------------------------------------------------

    async def async_get_health(self) -> HealthInfo:
        """``GET /api/health`` (public in every auth mode)."""
        _, data = await self._json("GET", "/api/health")
        return HealthInfo.from_dict(data)

    async def async_validate_key(self) -> None:
        """Check that the configured credentials are accepted.

        ``GET /api/status`` is public in ``key`` mode and so cannot prove a key is valid;
        ``GET /api/config`` needs the key in both ``key`` and ``password`` modes (and is
        public in ``open`` mode, where there is nothing to validate).

        Raises ``SmartFrameAuthError`` on HTTP 401.
        """
        await self._request("GET", "/api/config")

    async def async_get_status(self) -> EngineStatus:
        """``GET /api/status``."""
        _, data = await self._json("GET", "/api/status")
        return EngineStatus.from_dict(data)

    async def async_get_config(self) -> ConfigView:
        """``GET /api/config`` (needs the key in ``key`` and ``password`` modes)."""
        _, data = await self._json("GET", "/api/config")
        return ConfigView.from_dict(data)

    async def async_set_family_mode(self, enabled: bool) -> ConfigView:
        """Persistently toggle Family Mode: ``PUT /api/config`` with ``{"family": {"enabled"}}``."""
        _, data = await self._json("PUT", "/api/config", json_body={"family": {"enabled": enabled}})
        return ConfigView.from_dict(data)

    # -- Ambient context -----------------------------------------------------------------

    async def async_get_context(self) -> AmbientView:
        """``GET /api/context``."""
        _, data = await self._json("GET", "/api/context")
        return AmbientView.from_dict(data)

    async def async_set_context(
        self,
        *,
        presence: Sequence[str] | WithTtl[Sequence[str]] | None = None,
        family_mode: bool | WithTtl[bool] | None = None,
        weather: str | WithTtl[str] | None = None,
        season: Season | WithTtl[Season] | None = None,
        boost_tags: Sequence[str] | WithTtl[Sequence[str]] | None = None,
        boost_people: Sequence[str] | WithTtl[Sequence[str]] | None = None,
        ttl_minutes: int | None = None,
    ) -> AmbientView:
        """``POST /api/context``: set ambient fields; each one expires on its own.

        Fields left as ``None`` keep their current value. Pass ``WithTtl`` for a per-field
        lifetime, or ``ttl_minutes`` for the default lifetime of every field of this call
        (1..10080 minutes; the engine validates the range).
        """
        fields: dict[str, Any] = {
            "presence": presence,
            "family_mode": family_mode,
            "weather": weather,
            "season": season,
            "boost_tags": boost_tags,
            "boost_people": boost_people,
        }
        body = {key: _encode_ambient(value) for key, value in fields.items() if value is not None}
        if not body:
            raise ValueError("At least one ambient context field must be given")
        if ttl_minutes is not None:
            body["ttl_minutes"] = ttl_minutes
        _, data = await self._json("POST", "/api/context", json_body=body)
        return AmbientView.from_dict(data)

    async def async_clear_context(self, field_name: AmbientField | None = None) -> AmbientView:
        """``DELETE /api/context``: clear one field, or everything when ``None``."""
        params = {"field": field_name} if field_name else None
        _, data = await self._json("DELETE", "/api/context", params=params)
        return AmbientView.from_dict(data)

    # -- Display triggers ----------------------------------------------------------------

    async def async_refresh(self) -> RefreshResult:
        """``POST /api/refresh``: re-run the active source rule now."""
        _, data = await self._json("POST", "/api/refresh")
        return RefreshResult.from_dict(data)

    async def async_title_card(
        self,
        title: str,
        *,
        subtitle: str | None = None,
        caption: str | None = None,
        accent: Accent | None = None,
        ephemeral: bool | None = None,
        ttl_minutes: int | _Unset | None = UNSET,
        hold_minutes: int | None = None,
        push_to_fraimic: bool | None = None,
    ) -> DisplayResult:
        """``POST /api/display/title-card``: show a typographic card.

        Omitted options keep the engine's defaults. ``ttl_minutes=None`` is sent as ``null``
        (the card stays until cancelled or replaced), which differs from leaving it unset
        (the engine's 60-minute default).
        """
        body: dict[str, Any] = {"title": title}
        optional: dict[str, Any] = {
            "subtitle": subtitle,
            "caption": caption,
            "accent": accent,
            "ephemeral": ephemeral,
            "hold_minutes": hold_minutes,
            "push_to_fraimic": push_to_fraimic,
        }
        body.update({key: value for key, value in optional.items() if value is not None})
        if ttl_minutes is not UNSET:
            body["ttl_minutes"] = ttl_minutes
        _, data = await self._json("POST", "/api/display/title-card", json_body=body)
        return DisplayResult.from_dict(data)

    async def async_wakeup(self, *, wait: bool = False) -> WakeupResult:
        """``POST /api/fraimic/wakeup``: tell a deep-sleep engine the frame woke up."""
        _, data = await self._json(
            "POST",
            "/api/fraimic/wakeup",
            params={"wait": "true"} if wait else None,
            ok_statuses=(200, 202),
        )
        return WakeupResult.from_dict(data)

    async def async_push(self, *, from_preview: bool = False) -> DeliveryOutcome:
        """``POST /api/fraimic/push``: send the current (or preview) image to the frame.

        HTTP 202 (frame unreachable, image stays queued) is a normal outcome, reported as
        ``status == "queued"``.
        """
        _, data = await self._json(
            "POST",
            "/api/fraimic/push",
            params={"from_preview": "true"} if from_preview else None,
            ok_statuses=(200, 202),
        )
        if not isinstance(data, Mapping):
            raise SmartFrameConnectionError("Unexpected push response (not an object)")
        return DeliveryOutcome.from_dict(data)

    # -- Images --------------------------------------------------------------------------

    async def _image(self, endpoint: str) -> bytes | None:
        try:
            response = await self._request("GET", endpoint)
        except SmartFrameApiError as err:
            if err.status == 404:
                return None
            raise
        return response.body

    async def async_get_current_image(self) -> bytes | None:
        """``GET /api/current.jpg``: the image currently rendered; ``None`` before the first."""
        return await self._image("/api/current.jpg")

    async def async_get_preview_image(self) -> bytes | None:
        """``GET /api/preview.jpg``: the most recent render, pushed or not."""
        return await self._image("/api/preview.jpg")
