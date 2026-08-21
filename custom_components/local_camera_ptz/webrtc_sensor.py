from __future__ import annotations

import json
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_DEVICE_ID
from .webrtc import get_tuya_webrtc_config


class TuyaWebRTCDiagnosticsSensor(SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Tuya WebRTC"
    _attr_icon = "mdi:video-wireless-outline"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_tuya_webrtc_v2"
        self._state = "unknown"
        self._attrs: dict[str, Any] = {}

    @property
    def native_value(self) -> str:
        return self._state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._attrs

    async def async_update(self) -> None:
        result = await get_tuya_webrtc_config(
            self.hass, self._entry.data[CONF_DEVICE_ID]
        )
        if result is None:
            self._state = "unavailable"
            self._attrs = {"error": "Official Tuya integration not available"}
            return

        # Keep the raw response in diagnostics, but strip secrets/tokens.
        safe = dict(result)
        for key in ("url", "uri", "token", "access_token", "sign", "signInfo"):
            safe.pop(key, None)

        if result.get("error"):
            self._state = "error"
            self._attrs = {
                "error": result["error"],
                "raw_keys": sorted(result.keys()),
                "raw_response": json.dumps(safe, ensure_ascii=False, default=str)[:4000],
            }
            return

        skill = result.get("skill_parsed", {})
        videos = skill.get("videos", []) if isinstance(skill, dict) else []
        resolutions: list[str] = []
        stream_types: list[int] = []
        video_details: list[dict[str, Any]] = []
        for video in videos:
            if not isinstance(video, dict):
                continue
            width = video.get("width")
            height = video.get("height")
            stream_type = video.get("streamType")
            if width and height:
                resolutions.append(f"{width}x{height}")
            if isinstance(stream_type, int):
                stream_types.append(stream_type)
            video_details.append({
                "width": width,
                "height": height,
                "stream_type": stream_type,
                "codec": video.get("codec"),
                "fps": video.get("fps"),
            })

        self._state = "supported" if result.get("supports_webrtc") else "unknown"
        self._attrs = {
            "supports_webrtc": result.get("supports_webrtc"),
            "video_clarity": result.get("vedio_clarity"),
            "video_claritys": result.get("vedio_claritys"),
            "resolutions": resolutions,
            "stream_types": stream_types,
            "video_details": video_details,
            "protocol_version": result.get("protocol_version"),
            "video_count": len(videos),
            "raw_keys": sorted(result.keys()),
        }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([TuyaWebRTCDiagnosticsSensor(hass, entry)])
