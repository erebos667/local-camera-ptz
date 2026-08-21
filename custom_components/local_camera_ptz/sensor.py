from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

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
            self._attrs["raw_response"] = json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)
            if _is_tuya_error(result):
                self._attrs["tuya_error"] = True
        except Exception as err:  # noqa: BLE001
            self._state = 0
            self._attrs = {"error": str(err), "error_type": type(err).__name__}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([
        LocalCameraPTZConnectionSensor(entry),
        LocalCameraPTZDpsSensor(entry),
    ])
