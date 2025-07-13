# ISYGLT Modbus-TCP Integration

Home Assistant custom component for controlling ISYGLT devices over Modbus-TCP.

## Features

* Supports RGB/white lights, dimmers, motion sensors, button grids, I/O modules and group bits
* Bulk-read polling with caching and queue-based writes
* YAML import and UI configuration flow

## Installation

### HACS (recommended)
1. In HACS → **Integrations** click the three-dot menu → **Custom repositories**.
2. Add the GitHub URL of this repository and choose **Integration**.
3. Select the new entry and press **Download**.
4. Restart Home Assistant.

### Manual
1. Copy `custom_components/isyglt/` into `<config>/custom_components/`.
2. Restart Home Assistant.

## Configuration
Add a modbus hub configuration entry for isyglt with a dummy device. e.g.:
```yaml
modbus:
  - type: tcp
    name: ISYGLT
#    host: 192.168.2.165
    host: 10.70.0.10
    port: 502
    lights:
      - name: dummy
        unique_id: modbus_isyglt_dummy
        slave: 1
        address: 1500 # Coil address (decimal or hex, e.g., 0x0D)
        write_type: coil # Specifies single coil for on/off
        command_on: 1 # Value to write for ON (default: 1)
        command_off: 0 # Value to write for OFF (default: 0)
```

Add the integration from **Settings → Devices & Services**.  Supply your hub name (and optionally poll interval), then configure devices in the options flow.

## Updating / Removing
Updates are delivered through HACS like any other custom component.  To remove, delete the integration from **Devices & Services** and remove the files (or uninstall via HACS).

---
This project is not affiliated with ISYGLT. 