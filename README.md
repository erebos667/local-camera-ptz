# Local Camera PTZ

Home Assistant custom integration for testing and controlling LSC/Tuya cameras over the local Tuya 3.3 protocol.

## Current goal

The first version is intentionally conservative. It does **not** modify the camera firmware and does not expose the video stream yet.

It provides a configuration flow for:

- camera IP / hostname
- Tuya device ID
- Local Key (stored in Home Assistant config, never in this repository)
- protocol version (default `3.3`)

It also creates a diagnostic sensor that checks whether TCP port `6668` is reachable.

## Installation

Install through HACS as a custom repository, type **Integration**, then restart Home Assistant.

After restart:

**Settings → Devices & services → Add integration → Local Camera PTZ**

## Configuration example

For the LSC PTZ Camera Dualband discussed in this project:

```text
Host / IP:     192.168.1.16
Device ID:     bfc4ba4b542c12334amznw
Local Key:     <enter in Home Assistant UI>
Protocol:      3.3
```

Do not put the Local Key in YAML, GitHub, screenshots, or issue reports.

## Next steps

The integration will be extended to probe the local Tuya 3.3 device and identify available camera services before attempting any RTSP/1296p implementation.
