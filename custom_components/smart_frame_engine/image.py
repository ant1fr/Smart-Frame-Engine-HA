"""Live preview of the image currently rendered for the frame."""

from __future__ import annotations

from datetime import datetime
import logging

from homeassistant.components.image import ImageEntity, ImageEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
import homeassistant.util.dt as dt_util

from .api import SmartFrameError
from .coordinator import SmartFrameConfigEntry, SmartFrameCoordinator
from .entity import SmartFrameEntity

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

CURRENT_IMAGE = ImageEntityDescription(key="current_image", translation_key="current_image")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmartFrameConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the preview image of one engine."""
    async_add_entities([SmartFrameImage(hass, entry.runtime_data)])


class SmartFrameImage(SmartFrameEntity, ImageEntity):
    """The current frame image, fetched lazily and cached until the image changes.

    ``GET /api/current.jpg`` is the image being displayed; before the first image exists it
    answers 404 and the most recent render (``GET /api/preview.jpg``) is used instead.
    """

    _attr_content_type = "image/jpeg"

    def __init__(self, hass: HomeAssistant, coordinator: SmartFrameCoordinator) -> None:
        """Initialise the image entity."""
        SmartFrameEntity.__init__(self, coordinator, CURRENT_IMAGE)
        ImageEntity.__init__(self, hass)
        self._identity: tuple[str | None, datetime | None] | None = None
        self._cached: bytes | None = None
        self._async_track_image()

    @callback
    def _async_track_image(self) -> None:
        """Invalidate the cache and bump ``image_last_updated`` when the image changed."""
        image = self.coordinator.data.status.current_image
        identity = image.identity if image else None
        if identity == self._identity:
            return
        self._identity = identity
        self._cached = None
        if image is None:
            self._attr_image_last_updated = None
        else:
            self._attr_image_last_updated = image.processed_at or dt_util.utcnow()
            self.async_update_token()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Track the displayed image on every poll."""
        self._async_track_image()
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        """Return the JPEG, from the cache when the displayed image has not changed."""
        if self._cached is not None:
            return self._cached
        identity = self._identity
        client = self.coordinator.client
        try:
            content = await client.async_get_current_image()
            if content is None:
                content = await client.async_get_preview_image()
        except SmartFrameError as err:
            _LOGGER.warning("Cannot fetch the frame image: %s", err)
            return None
        # Do not cache a download that raced with a newer image.
        if content is not None and identity == self._identity:
            self._cached = content
        return content
