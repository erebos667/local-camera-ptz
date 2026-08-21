from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant


async def get_tuya_webrtc_config(
    hass: HomeAssistant, device_id: str
) -> dict[str, Any] | None:
    """Get Tuya WebRTC config through the authenticated Sharing API.

    The public Tuya documentation uses /v1.0/users/{uid}/devices/{device_id}/webrtc-configs.
    The Home Assistant Tuya sharing SDK keeps the current user's UID as the ownerId of
    the loaded homes, so use that UID instead of the public endpoint without a user ID.
    """
    for entry in hass.config_entries.async_entries("tuya"):
        runtime_data = getattr(entry, "runtime_data", None)
        manager = getattr(runtime_data, "manager", None)
        if manager is None:
            continue
        if device_id not in getattr(manager, "device_map", {}):
            continue

        customer_api = getattr(manager, "customer_api", None)
        if customer_api is None:
            return None

        homes = getattr(manager, "user_homes", []) or []
        uid = next(
            (str(home.id) for home in homes if getattr(home, "id", None)),
            None,
        )
        if uid is None:
            return None

        def _get() -> dict[str, Any] | None:
            response = customer_api.get(
                f"/v1.0/users/{uid}/devices/{device_id}/webrtc-configs"
            )
            if not response:
                return None
            return response.get("result")

        return await hass.async_add_executor_job(_get)

    return None
