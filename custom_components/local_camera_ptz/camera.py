from __future__ import annotations

import asyncio
import logging

from homeassistant.components.camera import CameraEntity, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_DEVICE_ID

_LOGGER = logging.getLogger(__name__)


class TuyaAllocatedStreamCamera(CameraEntity):
    """Expose the stream allocated by the official Tuya integration."""

    _attr_supported_features = CameraEntityFeature.STREAM
    _attr_name = "Tuya allocated stream"
    _attr_icon = "mdi:video-wireless"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__()
        self.hass = hass
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_tuya_allocated_stream"
        self._attr_brand = "Tuya"
        self._attr_model = "Allocated RTSP stream"

    def _get_manager(self):
        for tuya_entry in self.hass.config_entries.async_entries("tuya"):
            runtime_data = getattr(tuya_entry, "runtime_data", None)
            manager = getattr(runtime_data, "manager", None)
            if manager is not None:
                return manager
        return None

    async def stream_source(self) -> str | None:
        manager = self._get_manager()
        if manager is None:
            _LOGGER.error("Official Tuya integration manager not available")
            return None

        device_id = self._entry.data[CONF_DEVICE_ID]
        try:
            return await self.hass.async_add_executor_job(
                manager.get_device_stream_allocate,
                device_id,
                "rtsp",
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Unable to allocate Tuya stream: %s", err)
            return None

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([TuyaAllocatedStreamCamera(hass, entry)])
