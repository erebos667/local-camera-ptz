from __future__ import annotations

import asyncio
import logging

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_DEVICE_ID, CONF_HOST, CONF_LOCAL_KEY, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def _probe(host: str, port: int) -> bool:
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=3)
    except Exception:
        return False
    writer.close()
    await writer.wait_closed()
    return True


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
        host = self._entry.data[CONF_HOST]
        self._state = "connected" if await _probe(host, 6668) else "unreachable"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self.async_update()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([LocalCameraPTZStatusSensor(entry)])
