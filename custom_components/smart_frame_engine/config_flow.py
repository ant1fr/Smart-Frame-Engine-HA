"""Config flow for Smart Frame Engine.

Discovery (Zeroconf) and manual setup both start from ``GET /api/health``, which is public
in every auth mode: it tells us whether the engine is compatible, which auth mode it uses
and its stable ``instance_id`` (the config entry's unique ID).
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
import voluptuous as vol

from .api import (
    HealthInfo,
    SmartFrameAuthError,
    SmartFrameClient,
    SmartFrameError,
    compatibility_target,
)
from .const import (
    CONF_PATH,
    CONF_SCAN_INTERVAL,
    DEFAULT_PATH,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    TXT_API_VERSION,
    TXT_INSTANCE_ID,
    TXT_MIN_API_VERSION,
    TXT_PATH,
)

_LOGGER = logging.getLogger(__name__)


def _form_schema(fields: dict[Any, Any]) -> Any:
    """Build a form schema.

    Home Assistant 2026.10 moved to ``probatio`` and types ``data_schema`` accordingly, but it
    still serves ``import voluptuous`` through a runtime shim, which is what keeps this
    integration working on every 2026.x release (the minimum in ``hacs.json`` is 2026.1.0).
    Returning ``Any`` here avoids a type-only mismatch between the two libraries.
    """
    return vol.Schema(fields)


_API_KEY_SCHEMA = _form_schema(
    {
        vol.Required(CONF_API_KEY): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="off")
        )
    }
)


def _abort_reason_for(health: HealthInfo) -> str | None:
    """Return the abort reason that makes this engine unusable, or ``None``."""
    target = health.update_target
    if target == "server":
        return "server_outdated"
    if target == "integration":
        return "integration_outdated"
    if health.instance_id is None:
        # Engines older than 1.0.0rc2 have no stable ID to key the config entry on.
        return "instance_id_missing"
    return None


class SmartFrameConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Smart Frame Engine."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow state."""
        self._host = ""
        self._port = DEFAULT_PORT
        self._path = DEFAULT_PATH
        self._health: HealthInfo | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> SmartFrameOptionsFlow:
        """Return the options flow."""
        return SmartFrameOptionsFlow()

    # -- Helpers -------------------------------------------------------------------------

    def _client(
        self, host: str, port: int, path: str, api_key: str | None = None
    ) -> SmartFrameClient:
        return SmartFrameClient(async_get_clientsession(self.hass), host, port, path, api_key)

    async def _async_validate_key(self, api_key: str) -> str | None:
        """Return a form error key if ``api_key`` is not accepted, else ``None``."""
        client = self._client(self._host, self._port, self._path, api_key)
        try:
            await client.async_validate_key()
        except SmartFrameAuthError:
            return "invalid_auth"
        except SmartFrameError:
            return "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error while validating the API key")
            return "unknown"
        return None

    def _async_create_entry(self, api_key: str | None = None) -> ConfigFlowResult:
        health = self._health
        assert health is not None
        data: dict[str, Any] = {
            CONF_HOST: self._host,
            CONF_PORT: self._port,
            CONF_PATH: self._path,
        }
        if api_key:
            data[CONF_API_KEY] = api_key
        return self.async_create_entry(
            title=health.instance_name or health.instance_id or self._host, data=data
        )

    async def _async_register_engine(self, health: HealthInfo) -> None:
        """Make ``health.instance_id`` the flow's unique ID, updating a known entry's address."""
        await self.async_set_unique_id(health.instance_id)
        self._abort_if_unique_id_configured(
            updates={CONF_HOST: self._host, CONF_PORT: self._port, CONF_PATH: self._path}
        )

    # -- Manual setup --------------------------------------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for host and port, then probe ``GET /api/health``."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._host = user_input[CONF_HOST].strip()
            self._port = int(user_input[CONF_PORT])
            self._path = DEFAULT_PATH
            try:
                health = await self._client(self._host, self._port, self._path).async_get_health()
            except SmartFrameError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error while probing the engine")
                errors["base"] = "unknown"
            else:
                if reason := _abort_reason_for(health):
                    return self.async_abort(reason=reason)
                self._health = health
                await self._async_register_engine(health)
                if health.requires_key:
                    return await self.async_step_auth()
                return self._async_create_entry()

        schema = _form_schema(
            {
                vol.Required(CONF_HOST, default=self._host): str,
                vol.Required(CONF_PORT, default=self._port): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=65535)
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_auth(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the API key (only reached when the engine's ``auth`` is not ``open``)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            if (error := await self._async_validate_key(api_key)) is None:
                return self._async_create_entry(api_key)
            errors["base"] = error
        return self.async_show_form(step_id="auth", data_schema=_API_KEY_SCHEMA, errors=errors)

    # -- Zeroconf discovery --------------------------------------------------------------

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        """Handle a ``_smartframe._tcp.local.`` record."""
        props = discovery_info.properties
        self._host = discovery_info.host
        self._port = discovery_info.port or DEFAULT_PORT
        self._path = props.get(TXT_PATH) or DEFAULT_PATH

        # The TXT record alone is enough to reject an incompatible engine.
        try:
            api_version = int(props[TXT_API_VERSION] or "")
            min_api_version = int(props[TXT_MIN_API_VERSION] or "")
        except (KeyError, ValueError):
            pass
        else:
            if (target := compatibility_target(api_version, min_api_version)) is not None:
                return self.async_abort(reason=f"{target}_outdated")

        # Known engine: update its address without any network call.
        txt_instance_id = props.get(TXT_INSTANCE_ID)
        if txt_instance_id:
            await self.async_set_unique_id(txt_instance_id)
            self._abort_if_unique_id_configured(
                updates={CONF_HOST: self._host, CONF_PORT: self._port, CONF_PATH: self._path}
            )

        try:
            health = await self._client(self._host, self._port, self._path).async_get_health()
        except SmartFrameError:
            return self.async_abort(reason="cannot_connect")

        if reason := _abort_reason_for(health):
            return self.async_abort(reason=reason)
        if txt_instance_id and health.instance_id != txt_instance_id:
            return self.async_abort(reason="wrong_instance")

        self._health = health
        await self._async_register_engine(health)
        self.context["title_placeholders"] = {"name": health.instance_name or self._host}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm a discovered engine; ask for the API key only when one is required."""
        health = self._health
        assert health is not None
        placeholders = {
            "name": health.instance_name or self._host,
            "host": self._host,
            "port": str(self._port),
        }
        errors: dict[str, str] = {}

        if not health.requires_key:
            if user_input is not None:
                return self._async_create_entry()
            self._set_confirm_only()
            return self.async_show_form(
                step_id="zeroconf_confirm", description_placeholders=placeholders
            )

        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            if (error := await self._async_validate_key(api_key)) is None:
                return self._async_create_entry(api_key)
            errors["base"] = error
        return self.async_show_form(
            step_id="zeroconf_confirm",
            data_schema=_API_KEY_SCHEMA,
            description_placeholders=placeholders,
            errors=errors,
        )

    # -- Reauthentication ----------------------------------------------------------------

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start reauthentication after the engine answered 401."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new API key."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            client = self._client(
                entry.data[CONF_HOST],
                entry.data[CONF_PORT],
                entry.data.get(CONF_PATH, DEFAULT_PATH),
                api_key,
            )
            try:
                health = await client.async_get_health()
                await client.async_validate_key()
            except SmartFrameAuthError:
                errors["base"] = "invalid_auth"
            except SmartFrameError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during reauthentication")
                errors["base"] = "unknown"
            else:
                if health.update_target == "server":
                    return self.async_abort(reason="server_outdated")
                if health.update_target == "integration":
                    return self.async_abort(reason="integration_outdated")
                if health.instance_id and health.instance_id != entry.unique_id:
                    return self.async_abort(reason="wrong_instance")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_API_KEY: api_key}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_API_KEY_SCHEMA,
            description_placeholders={"name": entry.title},
            errors=errors,
        )


class SmartFrameOptionsFlow(OptionsFlowWithReload):
    """Options: the polling interval (the entry reloads automatically on change)."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage the polling interval."""
        if user_input is not None:
            return self.async_create_entry(
                data={CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL])}
            )

        current = self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        schema = _form_schema(
            {
                vol.Required(CONF_SCAN_INTERVAL, default=current): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        max=MAX_SCAN_INTERVAL,
                        step=1,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                )
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
