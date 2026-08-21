from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
from typing import Any, Literal
from urllib.parse import urljoin, urlsplit, urlunsplit

import tinytuya
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
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


async def _get_tuya_manager(hass: HomeAssistant, device_id: str):
    """Return the manager from the official Tuya integration for a device."""
    for entry in hass.config_entries.async_entries("tuya"):
        runtime_data = getattr(entry, "runtime_data", None)
        manager = getattr(runtime_data, "manager", None)
        if manager is None:
            continue
        if device_id in getattr(manager, "device_map", {}):
            return manager
    return None


async def _get_tuya_cloud_stream_source(
    hass: HomeAssistant,
    device_id: str,
    stream_type: Literal["flv", "hls", "rtmp", "rtsp"] = "rtsp",
) -> str | None:
    """Ask the official HA Tuya integration for an allocated stream."""
    manager = await _get_tuya_manager(hass, device_id)
    if manager is None:
        return None

    getter = getattr(manager, "get_device_stream_allocate", None)
    if getter is None:
        return None

    return await hass.async_add_executor_job(getter, device_id, stream_type)


def _sanitize_stream_source(source: str) -> dict[str, Any]:
    """Expose stream metadata without leaking credentials or tokens."""
    parsed = urlsplit(source)
    hostname = parsed.hostname
    port = parsed.port
    path = parsed.path or "/"
    lower = source.lower()
    resolution_hints = [
        token for token in ("2304", "1296", "1080", "720", "640", "360") if token in lower
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


def _authorized_segment_url(source: str, segment_uri: str) -> str:
    """Resolve a media segment while retaining Tuya's signed query when needed."""
    segment_url = urljoin(source, segment_uri)
    source_parts = urlsplit(source)
    segment_parts = urlsplit(segment_url)
    if not segment_parts.query and source_parts.query:
        segment_url = urlunsplit(
            (
                segment_parts.scheme,
                segment_parts.netloc,
                segment_parts.path,
                source_parts.query,
                segment_parts.fragment,
            )
        )
    return segment_url


async def _probe_hls_playlist(hass: HomeAssistant, source: str) -> dict[str, Any]:
    """Inspect an HLS playlist and estimate bitrate from one media segment."""
    session = async_get_clientsession(hass)
    try:
        async with session.get(source, timeout=10) as response:
            response.raise_for_status()
            text = await response.text(errors="replace")
    except Exception as err:  # noqa: BLE001
        return {"hls_playlist_status": "error", "hls_playlist_error": str(err)}

    variants: list[dict[str, Any]] = []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF:"):
            continue
        attrs = line.split(":", 1)[1]
        bandwidth_match = re.search(r"(?:^|,)BANDWIDTH=(\d+)", attrs)
        resolution_match = re.search(r"(?:^|,)RESOLUTION=(\d+x\d+)", attrs)
        codecs_match = re.search(r"(?:^|,)CODECS=\"([^\"]+)\"", attrs)
        variants.append(
            {
                "bandwidth": int(bandwidth_match.group(1)) if bandwidth_match else None,
                "resolution": resolution_match.group(1) if resolution_match else None,
                "codecs": codecs_match.group(1) if codecs_match else None,
            }
        )
        _ = lines[index + 1] if index + 1 < len(lines) else None

    segment_uri: str | None = None
    segment_duration: float | None = None
    for index, line in enumerate(lines):
        if not line.startswith("#EXTINF:"):
            continue
        duration_text = line.split(":", 1)[1].split(",", 1)[0]
        try:
            segment_duration = float(duration_text)
        except ValueError:
            segment_duration = None
        if index + 1 < len(lines) and not lines[index + 1].startswith("#"):
            segment_uri = lines[index + 1]
            break

    result: dict[str, Any] = {
        "hls_playlist_status": "ok",
        "hls_playlist_type": "master" if variants else "media",
        "hls_variant_count": len(variants),
        "hls_variants": variants,
    }

    if segment_uri and segment_duration and segment_duration > 0:
        try:
            segment_url = _authorized_segment_url(source, segment_uri)
            async with session.get(segment_url, timeout=10) as response:
                response.raise_for_status()
                segment = await response.read()
            result["hls_segment_bytes"] = len(segment)
            result["hls_segment_duration"] = segment_duration
            result["hls_estimated_bitrate"] = round((len(segment) * 8) / segment_duration)
            result["hls_estimated_bitrate_kbps"] = round(
                (len(segment) * 8) / segment_duration / 1000, 1
            )
        except Exception as err:  # noqa: BLE001
            result["hls_segment_status"] = "error"
            result["hls_segment_error"] = str(err)
    else:
        result["hls_segment_status"] = "not_found"

    return result


async def _probe_stream(source: str, stream_type: str = "rtsp") -> dict[str, Any]:
    """Probe an allocated Tuya stream without exposing its URL in HA state."""
    ffprobe = shutil.which("ffprobe") or "/usr/bin/ffprobe"
    command = [ffprobe, "-hide_banner", "-loglevel", "error"]
    if stream_type == "rtsp":
        command.extend(["-rtsp_transport", "tcp", "-timeout", "8000000"])
    command.extend([
        "-show_entries",
        "stream=index,codec_type,codec_name,width,height,r_frame_rate,bit_rate",
        "-of",
        "json",
        source,
    ])

    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15)
    except asyncio.TimeoutError:
        if process is not None:
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
            self._state = "tuya_error" if _is_tuya_error(result) else ("authenticated" if dps else "connected_no_dps")
            self._attrs = {"host": host, "port": 6668, "protocol": self._entry.data.get(CONF_PROTOCOL, 3.3), "dps_count": len(dps), "raw_response": raw}
        except Exception as err:  # noqa: BLE001
            self._state = "error"
            self._attrs = {"host": host, "port": 6668, "protocol": self._entry.data.get(CONF_PROTOCOL, 3.3), "error": str(err), "error_type": type(err).__name__}
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
            self._attrs["raw_response"] = json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)
            if _is_tuya_error(result):
                self._attrs["tuya_error"] = True
        except Exception as err:  # noqa: BLE001
            self._state = 0
            self._attrs = {"error": str(err), "error_type": type(err).__name__}


class TuyaStreamDiagnosticsSensor(SensorEntity):
    """Probe every stream protocol offered by Tuya's HA integration."""

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
        device_id = self._entry.data[CONF_DEVICE_ID]
        results: dict[str, Any] = {}
        successful = 0

        for stream_type in ("rtsp", "hls", "flv"):
            try:
                source = await _get_tuya_cloud_stream_source(self.hass, device_id, stream_type)
                if not source:
                    results[f"{stream_type}_status"] = "no_url"
                    continue

                metadata = _sanitize_stream_source(source)
                probe = await _probe_stream(source, stream_type)
                results[f"{stream_type}_status"] = probe.get("probe_status")
                results[f"{stream_type}_scheme"] = metadata.get("scheme")
                results[f"{stream_type}_host"] = metadata.get("host")
                results[f"{stream_type}_port"] = metadata.get("port")
                results[f"{stream_type}_resolution_hints"] = metadata.get("resolution_hints", [])
                results[f"{stream_type}_width"] = probe.get("video_width")
                results[f"{stream_type}_height"] = probe.get("video_height")
                results[f"{stream_type}_codec"] = probe.get("video_codec")
                results[f"{stream_type}_fps"] = probe.get("video_fps")
                results[f"{stream_type}_bitrate"] = probe.get("video_bitrate")
                if stream_type == "hls":
                    results.update(await _probe_hls_playlist(self.hass, source))
                if probe.get("probe_status") == "ok":
                    successful += 1
            except Exception as err:  # noqa: BLE001
                results[f"{stream_type}_status"] = "error"
                results[f"{stream_type}_error"] = str(err)

        self._state = "ok" if successful else "failed"
        self._attrs = {"device_id": device_id, **results}


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
