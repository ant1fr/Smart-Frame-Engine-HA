# CLAUDE.md — Smart Frame Engine (Home Assistant Integration)

## Project Overview
Official Home Assistant custom integration (`smart_frame_engine`) for **Smart Frame Engine** (`[https://github.com/ant1fr/Smart-Frame-Engine](https://github.com/ant1fr/Smart-Frame-Engine)`), installable via HACS (Home Assistant Community Store) and manual installation.

- **Target Home Assistant Core:** 2026.1.0+ (Python 3.13.2+; CI and local tests run on Python 3.14, which latest HA and `pytest-homeassistant-custom-component` require)
- **Integration Domain:** `smart_frame_engine`
- **IoT Class:** `local_polling` (with Zeroconf mDNS discovery on `_smartframe._tcp.local.`)
- **Supported Server Contract:** `SUPPORTED_API_VERSION = 1` (contract v1.1: the engine's `instance_id` from `GET /api/health` / Zeroconf TXT is the config entry `unique_id` and the `DeviceInfo` identifier)
- **Upstream API Contract (Single Source of Truth):**
  - Human-readable contract: `[https://github.com/ant1fr/Smart-Frame-Engine/blob/main/docs/API_CONTRACT.md](https://github.com/ant1fr/Smart-Frame-Engine/blob/main/docs/API_CONTRACT.md)`
  - Machine-readable OpenAPI schema: `[https://github.com/ant1fr/Smart-Frame-Engine/blob/main/docs/openapi.json](https://github.com/ant1fr/Smart-Frame-Engine/blob/main/docs/openapi.json)`
  *(If `../Smart-Frame-Engine` is also checked out locally alongside this repo, you may inspect those files directly.)*

## Architecture & Directory Layout
- `custom_components/smart_frame_engine/__init__.py` — Entry setup/unload, coordinator init, service registration
- `custom_components/smart_frame_engine/api.py` — Async aiohttp client (`async_get_clientsession`, never raw `ClientSession`)
- `custom_components/smart_frame_engine/binary_sensor.py` — Engine connectivity & health status
- `custom_components/smart_frame_engine/button.py` — Force refresh / wake-up triggers
- `custom_components/smart_frame_engine/config_flow.py` — Zeroconf discovery, manual setup, auth detection, `OptionsFlow`
- `custom_components/smart_frame_engine/const.py` — Domain constants, `SUPPORTED_API_VERSION`, capability flag keys
- `custom_components/smart_frame_engine/coordinator.py` — `DataUpdateCoordinator` + API version compatibility & Repair issues
- `custom_components/smart_frame_engine/diagnostics.py` — Redacted diagnostic download (`async_redact_data`)
- `custom_components/smart_frame_engine/entity.py` — Base `CoordinatorEntity` with shared `DeviceInfo`
- `custom_components/smart_frame_engine/image.py` — Live preview entity of currently rendered frame image
- `custom_components/smart_frame_engine/manifest.json` — HA integration metadata (strict `hassfest` key sorting)
- `custom_components/smart_frame_engine/sensor.py` — Active source/mode, media title, engine version, ambient context
- `custom_components/smart_frame_engine/services.py` — Service handlers (capability-gated, `ServiceValidationError`), registered once from `async_setup`
- `custom_components/smart_frame_engine/services.yaml` — Service definitions & UI selectors
- `custom_components/smart_frame_engine/strings.json` — Base UI & error strings (must stay identical to `translations/en.json`)
- `custom_components/smart_frame_engine/switch.py` — Family Mode toggle
- `custom_components/smart_frame_engine/translations/en.json` & `fr.json` — Complete English & French translations
- `blueprints/automation/smart_frame_engine/` — Ready-to-use YAML blueprints (weather/presence context, title cards)
- `tests/` — `pytest` + `pytest-homeassistant-custom-component`
- `hacs.json` — HACS metadata
- `pyproject.toml` — `ruff`, `mypy`, `pytest` configuration
- `requirements_test.txt` — test/lint dependencies (`pytest-homeassistant-custom-component`, `ruff`, `mypy`)

## Development, Quality & GitHub Rules (CRITICAL)

### 1. API Contract Grounding (`ant1fr/Smart-Frame-Engine`)
- **Zero guesswork:** Every endpoint path, HTTP method, query parameter, request payload, response field, and capability flag in `api.py` and `const.py` MUST strictly match `docs/API_CONTRACT.md` and `docs/openapi.json` from `ant1fr/Smart-Frame-Engine`.
- Never hardcode or invent undocumented API fields.

### 2. Forward & Backward Compatibility Handshake
- Both `config_flow.py` and `coordinator.py` validate `GET /api/health` against `SUPPORTED_API_VERSION`:
  - Compatible iff `min_api_version <= SUPPORTED_API_VERSION <= api_version`.
  - On incompatibility, raise `ConfigEntryNotReady` / `UpdateFailed` AND create a persistent Repair issue via `homeassistant.helpers.issue_registry.async_create_issue` explaining whether the integration or the server requires an update. Automatically call `async_delete_issue` once compatibility returns.
- **Graceful Capability Degradation:** Gate optional entities at platform setup (`if CAPABILITY_X in coordinator.capabilities`). Validate capability flags in services and raise `ServiceValidationError` (with `translation_domain=DOMAIN` and `translation_key=...`) if unsupported by the connected server.

### 3. Home Assistant 2026 & HACS/Hassfest Standards
- **Async & Session Reuse:** Never perform blocking I/O. Always inject HA's shared HTTP session via `async_get_clientsession(hass)`.
- **Security & Privacy:** Redact `CONF_API_KEY` and any tokens in `diagnostics.py` (`async_redact_data`) and never log credentials.
- **Strict Typing:** Use `from __future__ import annotations`, typed config entries (`SmartFrameConfigEntry = ConfigEntry[SmartFrameCoordinator]`), and `CoordinatorEntity[SmartFrameCoordinator]`.
- **`manifest.json` Key Ordering (`hassfest` strict rule):** `domain` first, `name` second, then all remaining keys sorted strictly alphabetically (`codeowners`, `config_flow`, `documentation`, `iot_class`, `issue_tracker`, `requirements`, `version`, `zeroconf`).
- **Translations Parity:** `strings.json` and `translations/en.json` must remain byte-for-byte identical; `translations/fr.json` must cover 100% of config steps, errors, abort reasons, entity names, service fields, exceptions, and Repair issues.

### 4. Git Workflow & CI/CD
- **Branching:** Never commit directly to `main` (or `develop` once protected). Work on feature branches (`feat/...`, `fix/...`, `chore/...`) and open PRs.
- **Required CI Checks:** Every PR must pass:
  - `home-assistant/actions/hassfest`
  - `hacs/action` (`category: integration`)
  - `ruff check .` & `ruff format --check .`
  - `mypy custom_components`
  - `pytest` (with `pytest-homeassistant-custom-component`)

## Commands
- `ruff check .`
- `ruff format --check .`
- `mypy custom_components`
- `pytest` — HA's test plugin imports `fcntl`, so run it on Linux/macOS/WSL (not native Windows); `ruff` and `mypy` run anywhere
