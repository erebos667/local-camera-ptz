from __future__ import annotations

import asyncio
import json
import logging
import shutil
from typing import Any
from urllib.parse import urlsplit

import tinytuya
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_DEVICE_ID, CONF_HOST, CONF_LOCAL_KEY, CONF_PROTOCOL

_LOGGER = logging.getLogger(__name__)


async def _read_tuya_status(entry: ConfigEntry) -> dict[str, Any]:
    host = entry.data[CONF_HOST]
    device_id = entry.data[CONF_DEVICE_ID]
    local_key = entry.data[CONF_LOCAL_KEY]
    version = float(entry.data.get(CONF_PROTOCOL, 3.3))

    def _read() -> dict[str, Any]:
        device = tinytuya.Device(
            dev_id=device_id,
            address=host,
            local_key=local_key,
            version=version,
            connection_timeout=5,
            connection_retry_limit=1,
            connection_retry_delay=0,
        )
        device.set_socketPersistent(False)
        result = device.status()
        if result is None:
            raise RuntimeError("Tuya returned no status")
        if not isinstance(result, dict):
            raise RuntimeError(f"Unexpected Tuya response type: {type(result).__name__}")
        return result

    return await asyncio.to_thread(_read)


def _extract_dps(result: dict[str, Any]) -> dict[str, Any]:
    dps = result.get("dps")
    if isinstance(dps, dict):
        return dps
    payload = result.get("Payload") or result.get("payload")
    if isinstance(payload, dict):
        nested = payload.get("dps")
        if isinstance(nested, dict):
            return nested
    return {}


def _is_tuya_error(result: dict[str, Any]) -> bool:
    return any(key in result for key in ("Error", "Err", "error", "error_code"))


async def _get_tuya_cloud_stream_source(
    hass: HomeAssistant, device_id: str
) -> str | None:
    """Ask the official HA Tuya integration for the same RTSP source it uses."""
    for entry in hass.config_entries.async_entries("tuya"):
        runtime_data = getattr(entry, "runtime_data", None)
        manager = getattr(runtime_data, "manager", None)
        if manager is None:
            continue

        device_map = getattr(manager, "device_map", {})
        device = device_map.get(device_id)
        if device is None:
            continue

        getter = getattr(manager, "get_device_stream_allocate", None)
        if getter is None:
            return None

        return await hass.async_add_executor_job(getter, device.id, "rtsp")

    return None


def _sanitize_stream_source(source: str) -> dict[str, Any]:
    """Expose stream metadata without leaking credentials or tokens."""
    parsed = urlsplit(source)
    hostname = parsed.hostname
    port = parsed.port
    path = parsed.path or "/"
    lower = source.lower()
    resolution_hints = [
        token for token in ("1296", "1080", "720", "640", "360") if token in lower
    ]
    return {
        "scheme": parsed.scheme,
        "host": hostname,
        "port": port,
        "path": path,
        "has_credentials": parsed.username is not None or parsed.password is not None,
        "has_query": bool(parsed.query),
        "resolution_hints": resolution_hints,
    }


async def _probe_stream(hass: HomeAssistant, source: str) -> dict[str, Any]:
    """Probe the allocated stream without exposing its URL in HA state."""
    ffprobe = shutil.which("ffprobe") or "/usr/bin/ffprobe"
    command = [
        ffprobe,
        "-hide_banner",
        "-loglevel",
        "error",
        "-rtsp_transport",
        "tcp",
        "-timeout",
        "8000000",
        "-show_entries",
        "stream=index,codec_type,codec_name,width,height,r_frame_rate,bit_rate",
        "-of",
        "json",
        source,
    ]

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15)
    except asyncio.TimeoutError:
        try:
            process.kill()
        except Exception:  # noqa: BLE001
            pass
        return {"probe_status": "timeout"}
    except Exception as err:  # noqa: BLE001
        return {"probe_status": "error", "probe_error": str(err)}

    if process.returncode != 0:
        error = stderr.decode("utf-8", errors="replace").strip()
        return {
            "probe_status": "failed",
            "probe_returncode": process.returncode,
            "probe_error": error[-500:] if error else "ffprobe failed",
        }

    try:
        data = json.loads(stdout.decode("utf-8"))
    except json.JSONDecodeError as err:
        return {"probe_status": "invalid_json", "probe_error": str(err)}

    streams = data.get("streams", [])
    video = [s for s in streams if s.get("codec_type") == "video"]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    result: dict[str, Any] = {
        "probe_status": "ok",
        "video_streams": len(video),
        "audio_streams": len(audio),
    }
    if video:
        first = video[0]
        result.update(
            {
                "video_codec": first.get("codec_name"),
                "video_width": first.get("width"),
                "video_height": first.get("height"),
                "video_fps": first.get("r_frame_rate"),
                "video_bitrate": first.get("bit_rate"),
            }
        )
    if audio:
        result["audio_codec"] = audio[0].get("codec_name")
    return result


class LocalCameraPTZConnectionSensor(SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Local connection"
    _attr_icon = "mdi:lan-connect"

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_local_connection"
        self._state = "unknown"
        self._attrs: dict[str, Any] = {}

    @property
    def native_value(self) -> str:
        return self._state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._attrs

    async def async_update(self) -> None:
        host = self._entry.data[CONF_HOST]
        try:
            result = await _read_tuya_status(self._entry)
            dps = _extract_dps(result)
            raw = json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)
            self._state = "tuya_error" if _is_tuya_error(result) else (
                "authenticated" if dps else "connected_no_dps"
            )
            self._attrs = {
                "host": host,
                "port": 6668,
                "protocol": self._entry.data.get(CONF_PROTOCOL, 3.3),
                "dps_count": len(dps),
                "raw_response": raw,
            }
        except Exception as err:  # noqa: BLE001
            self._state = "error"
            self._attrs = {
                "host": host,
                "port": 6668,
                "protocol": self._entry.data.get(CONF_PROTOCOL, 3.3),
                "error": str(err),
                "error_type": type(err).__name__,
            }
            _LOGGER.warning("Local Tuya status failed for %s: %s", host, err)


class LocalCameraPTZDpsSensor(SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Local DPS"
    _attr_icon = "mdi:code-json"

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_local_dps"
        self._state = 0
        self._attrs: dict[str, Any] = {}

    @property
    def native_value(self) -> int:
        return self._state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._attrs

    async def async_update(self) -> None:
        try:
            result = await _read_tuya_status(self._entry)
            dps = _extract_dps(result)
            self._state = len(dps)
            self._attrs = {f"dp_{key}": value for key, value in dps.items()}
            self._attrs["raw_response"] = json.dumps(
                result, ensure_ascii=False, sort_keys=True, default=str
            )
            if _is_tuya_error(result):
                self._attrs["tuya_error"] = True
        except Exception as err:  # noqa: BLE001
            self._state = 0
            self._attrs = {"error": str(err), "error_type": type(err).__name__}


class TuyaStreamDiagnosticsSensor(SensorEntity):
    """Inspect and probe the stream source returned by HA's official Tuya integration."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Tuya stream"
    _attr_icon = "mdi:video-wireless"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_tuya_stream"
        self._state = "unknown"
        self._attrs: dict[str, Any] = {}

    @property
    def native_value(self) -> str:
        return self._state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._attrs

    async def async_update(self) -> None:
        try:
            source = await _get_tuya_cloud_stream_source(
                self.hass, self._entry.data[CONF_DEVICE_ID]
            )
            if not source:
                self._state = "unavailable"
                self._attrs = {
                    "device_id": self._entry.data[CONF_DEVICE_ID],
                    "reason": "Tuya did not return an RTSP stream source",
                }
                return

            metadata = _sanitize_stream_source(source)
            probe = await _probe_stream(self.hass, source)
            self._state = probe.get("probe_status", "available")
            self._attrs = {
                "device_id": self._entry.data[CONF_DEVICE_ID],
                **metadata,
                **probe,
            }
        except Exception as err:  # noqa: BLE001
            self._state = "error"
            self._attrs = {
                "error": str(err),
                "error_type": type(err).__name__,
            }
            _LOGGER.warning("Tuya stream allocation/probe failed: %s", err)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([
        LocalCameraPTZConnectionSensor(entry),
        LocalCameraPTZDpsSensor(entry),
        TuyaStreamDiagnosticsSensor(hass, entry),
    ])
