from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_DEVICE_ID,
    CONF_HOST,
    CONF_LOCAL_KEY,
    CONF_PROTOCOL,
    CONF_SOURCE,
    DEFAULT_PROTOCOL,
    DOMAIN,
    SOURCE_HA_TUYA,
    SOURCE_MANUAL,
)


class LocalCameraPTZConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_DEVICE_ID])
            self._abort_if_unique_id_configured()

            source = user_input[CONF_SOURCE]
            if source == SOURCE_HA_TUYA:
                local_key = await self._get_local_key_from_tuya(
                    user_input[CONF_DEVICE_ID]
                )
                if not local_key:
                    errors[CONF_LOCAL_KEY] = "local_key_unavailable"
                else:
                    user_input[CONF_LOCAL_KEY] = local_key
            
            if not errors:
                return self.async_create_entry(
                    title="Local Camera PTZ",
                    data=user_input,
                )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SOURCE,
                    default=SOURCE_HA_TUYA,
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(
                                value=SOURCE_HA_TUYA,
                                label="Utiliser l'intégration Tuya de Home Assistant",
                            ),
                            selector.SelectOptionDict(
                                value=SOURCE_MANUAL,
                                label="Saisir la Local Key manuellement",
                            ),
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_DEVICE_ID): str,
                vol.Optional(CONF_LOCAL_KEY, default=""): selector.TextSelector(
                    selector.TextSelectorConfig(type="password")
                ),
                vol.Required(
                    CONF_PROTOCOL, default=DEFAULT_PROTOCOL
                ): vol.Coerce(float),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def _get_local_key_from_tuya(self, device_id: str) -> str | None:
        """Get the LAN key from an already configured HA Tuya integration."""
        for entry in self.hass.config_entries.async_entries("tuya"):
            runtime_data = getattr(entry, "runtime_data", None)
            manager = getattr(runtime_data, "manager", None)
            if manager is None:
                continue

            device = manager.device_map.get(device_id)
            if device is None:
                continue

            local_key = getattr(device, "local_key", None)
            if local_key:
                return str(local_key)

            # Compatibility fallback for versions of tuya_sharing that keep
            # the value in the object's instance dictionary.
            local_key = getattr(device, "__dict__", {}).get("local_key")
            if local_key:
                return str(local_key)

        return None
