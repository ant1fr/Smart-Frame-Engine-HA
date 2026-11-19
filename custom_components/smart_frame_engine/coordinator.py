"""DataUpdateCoordinator for Smart Frame Engine.

Every refresh re-validates the compatibility handshake (``GET /api/health``) so an engine
that is updated, downgraded or replaced is noticed within one polling interval. An
incompatible engine raises a persistent Repair issue that is removed automatically as soon
as the versions are compatible again.
"""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    AmbientView,
    EngineStatus,
    HealthInfo,
    SmartFrameAuthError,
    SmartFrameClient,
    SmartFrameError,
    UpdateTarget,
)
from .const import (
    CAPABILITY_AMBIENT_CONTEXT,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ENGINE_RELEASES_URL,
    INTEGRATION_RELEASES_URL,
    ISSUE_INCOMPATIBLE_PREFIX,
    ISSUE_INTEGRATION_OUTDATED,
    ISSUE_SERVER_OUTDATED,
    MANUFACTURER,
    MODEL,
    SUPPORTED_API_VERSION,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SmartFrameData:
    """One coherent snapshot of the engine."""

    health: HealthInfo
    status: EngineStatus
    # ``None`` when the engine does not advertise the ``ambient_context`` capability.
    ambient: AmbientView | None


SmartFrameConfigEntry = ConfigEntry["SmartFrameCoordinator"]


def _issue_id(entry_id: str) -> str:
    return f"{ISSUE_INCOMPATIBLE_PREFIX}_{entry_id}"


@callback
def async_delete_compatibility_issue(hass: HomeAssistant, entry_id: str) -> None:
    """Remove the incompatibility Repair issue of a config entry (no-op when absent)."""
    ir.async_delete_issue(hass, DOMAIN, _issue_id(entry_id))


class SmartFrameCoordinator(DataUpdateCoordinator[SmartFrameData]):
    """Poll health, status and ambient context of one engine."""

    config_entry: SmartFrameConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: SmartFrameConfigEntry,
        client: SmartFrameClient,
    ) -> None:
        """Initialise the coordinator; the polling interval comes from the entry options."""
        interval = int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {entry.title}",
            config_entry=entry,
            update_interval=timedelta(seconds=interval),
        )
        self.client = client
        # Capability flags of the connected engine; entity platforms and services read this
        # to adapt (contract: check ``capabilities``, never ``version``).
        self.capabilities: frozenset[str] = frozenset()

    @property
    def instance_id(self) -> str:
        """Stable engine identity: the config entry's unique ID (the engine's ``instance_id``)."""
        return self.config_entry.unique_id or self.config_entry.entry_id

    @property
    def device_info(self) -> DeviceInfo:
        """Shared ``DeviceInfo`` for every entity of this engine."""
        health = self.data.health if self.data is not None else None
        return DeviceInfo(
            identifiers={(DOMAIN, self.instance_id)},
            name=(health.instance_name if health else None) or self.config_entry.title,
            manufacturer=MANUFACTURER,
            model=MODEL,
            sw_version=health.version if health else None,
            configuration_url=self.client.base_url,
        )

    async def _async_call[T](self, awaitable: Awaitable[T]) -> T:
        """Await a client call and translate its errors for the coordinator."""
        try:
            return await awaitable
        except SmartFrameAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from err
        except SmartFrameError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err

    async def _async_update_data(self) -> SmartFrameData:
        """Fetch health (and re-check compatibility), then status and ambient context."""
        health = await self._async_call(self.client.async_get_health())
        self._async_check_instance(health)
        self._async_check_compatibility(health)

        status = await self._async_call(self.client.async_get_status())
        ambient: AmbientView | None = None
        if CAPABILITY_AMBIENT_CONTEXT in health.capabilities:
            # Also proves the key is still valid in ``key`` mode, where ``/api/status`` is public.
            ambient = await self._async_call(self.client.async_get_context())

        self.capabilities = health.capabilities
        return SmartFrameData(health=health, status=status, ambient=ambient)

    def _async_check_instance(self, health: HealthInfo) -> None:
        """Refuse to poll a different engine that took over this address."""
        expected = self.config_entry.unique_id
        if health.instance_id and expected and health.instance_id != expected:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="instance_mismatch",
                translation_placeholders={"name": self.config_entry.title},
            )

    def _async_check_compatibility(self, health: HealthInfo) -> None:
        """Create/delete the Repair issue and fail the refresh when incompatible."""
        target = health.update_target
        if target is None:
            async_delete_compatibility_issue(self.hass, self.config_entry.entry_id)
            return

        self._async_create_compatibility_issue(health, target)
        raise UpdateFailed(
            translation_domain=DOMAIN,
            translation_key=(
                "incompatible_server" if target == "server" else "incompatible_integration"
            ),
            translation_placeholders={
                "api_version": str(health.api_version),
                "min_api_version": str(health.min_api_version),
                "supported_version": str(SUPPORTED_API_VERSION),
            },
        )

    def _async_create_compatibility_issue(self, health: HealthInfo, target: UpdateTarget) -> None:
        """Create a persistent Repair issue saying which side needs updating."""
        outdated_integration = target == "integration"
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            _issue_id(self.config_entry.entry_id),
            is_fixable=False,
            is_persistent=True,
            severity=ir.IssueSeverity.ERROR,
            learn_more_url=INTEGRATION_RELEASES_URL
            if outdated_integration
            else ENGINE_RELEASES_URL,
            translation_key=(
                ISSUE_INTEGRATION_OUTDATED if outdated_integration else ISSUE_SERVER_OUTDATED
            ),
            translation_placeholders={
                "name": self.config_entry.title,
                "server_version": health.version,
                "api_version": str(health.api_version),
                "min_api_version": str(health.min_api_version),
                "supported_version": str(SUPPORTED_API_VERSION),
            },
        )
