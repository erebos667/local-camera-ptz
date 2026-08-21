from __future__ import annotations

from typing import override

from homeassistant.components import ffmpeg
from homeassistant.components.camera import Camera as CameraEntity
from homeassistant.components.camera import CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_DEVICE_ID


class TuyaAllocatedStreamCamera(CameraEntity):
    """Expose the high-resolution HLS stream allocated by the official Tuya integration."""

    _attr_supported_features = CameraEntityFeature.STREAM
    _attr_name = "Tuya allocated stream"
    _attr_icon = "mdi:video-wireless"
    _attr_brand = "Tuya"
    _attr_model = "Allocated HLS stream"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__()
        self.hass = hass
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_tuya_allocated_stream"

    def _get_manager(self):
        """Return the manager from the official Tuya integration."""
        for tuya_entry in self.hass.config_entries.async_entries("tuya"):
            runtime_data = getattr(tuya_entry, "runtime_data", None)
            manager = getattr(runtime_data, "manager", None)
            if manager is not None:
                return manager
        return None

    @override
    async def stream_source(self) -> str | None:
        """Return the HLS source allocated by Tuya.

        The RTSP allocation exposed by this camera is only 640x360 on the
        user's LSC camera, while Tuya's HLS allocation is 1920x1080. Use HLS
        here so Advanced Camera Card receives the higher-resolution stream.
        """
        manager = self._get_manager()
        if manager is None:
            return None

        return await self.hass.async_add_executor_job(
            manager.get_device_stream_allocate,
            self._entry.data[CONF_DEVICE_ID],
            "hls",
        )

    @override
    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a still image from the allocated HLS stream."""
        stream_source = await self.stream_source()
        if not stream_source:
            return None
        return await ffmpeg.async_get_image(
            self.hass,
            stream_source,
            width=width,
            height=height,
        )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the allocated stream camera."""
    async_add_entities([TuyaAllocatedStreamCamera(hass, entry)])
