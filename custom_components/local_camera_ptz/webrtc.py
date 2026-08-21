from __future__ import annotations

import json
from typing import Any

from homeassistant.core import HomeAssistant


def _extract_skill(result: dict[str, Any]) -> dict[str, Any]:
    skill = result.get("skill")
    if isinstance(skill, str):
        try:
            parsed = json.loads(skill)
            if isinstance(parsed, dict):
                result = {**result, "skill_parsed": parsed}
        except json.JSONDecodeError:
            pass
    return result


async def get_tuya_webrtc_config(
    hass: HomeAssistant, device_id: str
) -> dict[str, Any] | None:
    """Get Tuya WebRTC config using the authenticated HA Tuya session.

    Home Assistant's official Tuya integration stores the real Tuya UID in
    config_entry.data['token_info']['uid'].  The previous implementation
    incorrectly used a Smart Life home ID, which causes Tuya error 2008.
    """
    for entry in hass.config_entries.async_entries("tuya"):
        runtime_data = getattr(entry, "runtime_data", None)
        manager = getattr(runtime_data, "manager", None)
        if manager is None or device_id not in getattr(manager, "device_map", {}):
            continue

        customer_api = getattr(manager, "customer_api", None)
        if customer_api is None:
            return None

        token_info = entry.data.get("token_info", {})
        uid = token_info.get("uid") if isinstance(token_info, dict) else None
        if not uid:
            return {"error": "Tuya UID not available in official integration token"}

        def _get() -> dict[str, Any] | None:
            errors: list[str] = []
            # First try the public documented endpoint through the authenticated
            # CustomerApi session.  This preserves the HA Tuya authentication.
            for path in (
                f"/v1.0/users/{uid}/devices/{device_id}/webrtc-configs",
                f"/v1.0/m/ipc/{device_id}/webrtc-configs",
            ):
                try:
                    response = customer_api.get(path)
                    if isinstance(response, dict) and response.get("success", True):
                        result = response.get("result")
                        if isinstance(result, dict):
                            return _extract_skill(result)
                    if isinstance(response, dict):
                        errors.append(str(response.get("msg") or response.get("code") or "unknown response"))
                except Exception as err:  # noqa: BLE001
                    errors.append(str(err))

            return {"error": "; ".join(errors[-2:]) or "WebRTC config unavailable"}

        return await hass.async_add_executor_job(_get)

    return None
