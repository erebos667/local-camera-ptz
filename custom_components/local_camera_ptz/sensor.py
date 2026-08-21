from __future__ import annotations

import asyncio
import logging

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
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=3)
    except Exception:
        return False
    writer.close()
    await writer.wait_closed()
    return True


def _tuya_status(device_id: str, host: str, local_key: str, protocol: float):
    """Perform a read-only Tuya LAN status request."""
    device = tinytuya.Device(device_id, host, local_key)
    device.set_version(protocol)
    return device.status()


class LocalCameraPTZStatusSensor(SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Local connection"
    _attr_icon = "mdi:lan-connect"

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_local_connection"
        self._state = "unknown"

    @property
    def native_value(self) -> str:
        return self._state

    async def async_update(self) -> None:
        data = self._entry.data
        host = data[CONF_HOST]
        if not await _probe(host, 6668):
            self._state = "unreachable"
            return

        try:
            result = await self.hass.async_add_executor_job(
                _tuya_status,
                data[CONF_DEVICE_ID],
                host,
                data[CONF_LOCAL_KEY],
                data[CONF_PROTOCOL],
            )
            self._state = "authenticated" if isinstance(result, dict) else "connected"
        except Exception as err:
            _LOGGER.debug("Tuya LAN status probe failed: %s", err)
            self._state = "port_open_key_failed"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self.async_update()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([LocalCameraPTZStatusSensor(entry)])
