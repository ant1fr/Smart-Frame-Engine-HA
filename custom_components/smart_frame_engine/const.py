"""Constants for the Smart Frame Engine integration.

Everything contract-related (auth modes, capability flags, API version) mirrors
``docs/API_CONTRACT.md`` of https://github.com/ant1fr/Smart-Frame-Engine.
"""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "smart_frame_engine"
MANUFACTURER: Final = "Smart Frame Engine"
MODEL: Final = "Smart Frame Engine"

# Contract version this integration speaks. The engine is compatible when
# ``min_api_version <= SUPPORTED_API_VERSION <= api_version``.
SUPPORTED_API_VERSION: Final = 1

# Config entry keys (``host``, ``port`` and ``api_key`` come from homeassistant.const).
CONF_PATH: Final = "path"
CONF_SCAN_INTERVAL: Final = "scan_interval"

DEFAULT_PORT: Final = 8080
DEFAULT_PATH: Final = "/"
DEFAULT_SCAN_INTERVAL: Final = 30  # seconds
MIN_SCAN_INTERVAL: Final = 10
MAX_SCAN_INTERVAL: Final = 3600
REQUEST_TIMEOUT: Final = 10  # seconds

# Authentication modes reported by ``GET /api/health`` and the Zeroconf ``auth`` TXT key.
AUTH_OPEN: Final = "open"
AUTH_KEY: Final = "key"
AUTH_PASSWORD: Final = "password"

# Zeroconf TXT keys (all values are strings).
TXT_VERSION: Final = "version"
TXT_API_VERSION: Final = "api_version"
TXT_MIN_API_VERSION: Final = "min_api_version"
TXT_PATH: Final = "path"
TXT_AUTH: Final = "auth"
TXT_INSTANCE_ID: Final = "instance_id"

# Capability flags advertised in ``GET /api/health`` (contract v1.0, 18 flags).
CAPABILITY_ACTIVITY_LOG: Final = "activity_log"
CAPABILITY_AMBIENT_CONTEXT: Final = "ambient_context"
CAPABILITY_DISPLAY_IMMICH: Final = "display_immich"
CAPABILITY_DISPLAY_SMART_SEARCH: Final = "display_smart_search"
CAPABILITY_DISPLAY_TMDB: Final = "display_tmdb"
CAPABILITY_EPHEMERAL_IMAGES: Final = "ephemeral_images"
CAPABILITY_FAMILY_MODE: Final = "family_mode"
CAPABILITY_FORCE_REFRESH: Final = "force_refresh"
CAPABILITY_FRAME_PUSH: Final = "frame_push"
CAPABILITY_FRAME_WAKEUP: Final = "frame_wakeup"
CAPABILITY_NOW_PLAYING: Final = "now_playing"
CAPABILITY_NOW_PLAYING_DEDUP: Final = "now_playing_dedup"
CAPABILITY_PLAYGROUND: Final = "playground"
CAPABILITY_RANDOM_DISCOVERY: Final = "random_discovery"
CAPABILITY_SMART_MIX: Final = "smart_mix"
CAPABILITY_SMART_MIX_CONTEXT: Final = "smart_mix_context"
CAPABILITY_TITLE_CARD: Final = "title_card"
CAPABILITY_UPLOAD_IMAGE: Final = "upload_image"

ALL_CAPABILITIES: Final[frozenset[str]] = frozenset(
    {
        CAPABILITY_ACTIVITY_LOG,
        CAPABILITY_AMBIENT_CONTEXT,
        CAPABILITY_DISPLAY_IMMICH,
        CAPABILITY_DISPLAY_SMART_SEARCH,
        CAPABILITY_DISPLAY_TMDB,
        CAPABILITY_EPHEMERAL_IMAGES,
        CAPABILITY_FAMILY_MODE,
        CAPABILITY_FORCE_REFRESH,
        CAPABILITY_FRAME_PUSH,
        CAPABILITY_FRAME_WAKEUP,
        CAPABILITY_NOW_PLAYING,
        CAPABILITY_NOW_PLAYING_DEDUP,
        CAPABILITY_PLAYGROUND,
        CAPABILITY_RANDOM_DISCOVERY,
        CAPABILITY_SMART_MIX,
        CAPABILITY_SMART_MIX_CONTEXT,
        CAPABILITY_TITLE_CARD,
        CAPABILITY_UPLOAD_IMAGE,
    }
)

# Repair issues.
ISSUE_INCOMPATIBLE_PREFIX: Final = "incompatible_api"
# Translation keys of the two Repair issues (which side needs an update).
ISSUE_INTEGRATION_OUTDATED: Final = "integration_outdated"
ISSUE_SERVER_OUTDATED: Final = "server_outdated"

# Where to read about updating.
ENGINE_RELEASES_URL: Final = "https://github.com/ant1fr/Smart-Frame-Engine/releases"
INTEGRATION_RELEASES_URL: Final = "https://github.com/ant1fr/Smart-Frame-Engine-HA/releases"
