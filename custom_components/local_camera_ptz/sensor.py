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

from .const import CONF_DEVICE_ID, CONF_HOST, CONF_LOCAL_KEY, CONF_PROTOCOL, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def _probe(host: str, port: int) -> bool:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=3
        )
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False


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
            connection_timeout=3,
            connection_retry_limit=1,
            connection_retry_delay=0,
        )
        device.set_socketPersistent(False)
        result = device.status()
        if result is None:
            raise RuntimeError("Tuya returned no status")
        return result

    return await asyncio.to_thread(_read)


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
        if not await _probe(host, 6668):
            self._state = "unreachable"
            self._attrs = {"host": host, "port": 6668}
            return

        try:
            result = await _read_tuya_status(self._entry)
            dps = result.get("dps", {}) if isinstance(result, dict) else {}
            self._state = "authenticated"
            self._attrs = {
                "host": host,
                "port": 6668,
                "protocol": self._entry.data.get(CONF_PROTOCOL, 3.3),
                "dps_count": len(dps),
                "dps": json.dumps(dps, ensure_ascii=False, sort_keys=True),
            }
        except Exception as err:  # noqa: BLE001
            self._state = "port_open_key_failed"
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
            dps = result.get("dps", {}) if isinstance(result, dict) else {}
            self._state = len(dps)
            self._attrs = {f"dp_{key}": value for key, value in dps.items()}
            self._attrs["raw"] = json.dumps(result, ensure_ascii=False, sort_keys=True)
        except Exception as err:  # noqa: BLE001
            self._state = 0
            self._attrs = {"error": str(err), "error_type": type(err).__name__}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities(
        [
            LocalCameraPTZConnectionSensor(entry),
            LocalCameraPTZDpsSensor(entry),
        ]
    )
