import logging
from typing import Any, Dict, List

import voluptuous as vol
from datetime import timedelta

from homeassistant.const import CONF_NAME
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import discovery
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from .const import (
    DOMAIN,
    CONF_HUB,
    CONF_DEVICES,
    CONF_TYPE,
    CONF_ADDRESS,
    CONF_CCT_MIN,
    CONF_CCT_MAX,
    DEVICE_TYPE_RGB_LIGHT,
    DEVICE_TYPE_WHITE_LIGHT,
    DEVICE_TYPE_MOTION_SENSOR,
    DEVICE_TYPE_BUTTON_GRID,
    DEVICE_TYPE_IO_MODULE,
    DEVICE_TYPE_DIMMER,
    DEVICE_TYPE_GROUP_SWITCH,
    CONF_POLL_INTERVAL,
    DEFAULT_POLL_INTERVAL,
    CONF_PRESCALER,
    LIGHT_REGISTER_COUNT_RGB,
    LIGHT_REGISTER_COUNT_WHITE,
    LIGHT_REGISTER_COUNT_DIMMER,
    CONF_BIT,
)

_LOGGER = logging.getLogger(__name__)

SUPPORTED_TYPES = {
    DEVICE_TYPE_RGB_LIGHT,
    DEVICE_TYPE_WHITE_LIGHT,
    DEVICE_TYPE_MOTION_SENSOR,
    DEVICE_TYPE_BUTTON_GRID,
    DEVICE_TYPE_IO_MODULE,
    DEVICE_TYPE_DIMMER,
    DEVICE_TYPE_GROUP_SWITCH,
}

# Schema definitions
DEVICE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TYPE): vol.In(SUPPORTED_TYPES),
        vol.Required(CONF_NAME): cv.string,
        vol.Required(CONF_ADDRESS): cv.positive_int,
        vol.Optional(CONF_CCT_MIN, default=2512): cv.positive_int,
        vol.Optional(CONF_CCT_MAX, default=5000): cv.positive_int,
        vol.Optional(CONF_PRESCALER, default=1): vol.All(vol.Coerce(float), vol.Range(min=0.0001)),
        # for group switch
        vol.Optional(CONF_BIT): vol.All(vol.Coerce(int), vol.Range(min=1, max=8)),
    }
)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_HUB): cv.string,
                vol.Required(CONF_DEVICES): vol.All(cv.ensure_list, [DEVICE_SCHEMA]),
                vol.Optional(CONF_POLL_INTERVAL, default=DEFAULT_POLL_INTERVAL): vol.All(vol.Coerce(float), vol.Range(min=0.1)),
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)

PLATFORMS = ["light", "binary_sensor", "switch", "sensor"]

async def async_setup(hass: HomeAssistant, config: Dict[str, Any]):
    """Set up the ISYGLT integration via YAML."""
    conf = config.get(DOMAIN)
    if conf is None:
        _LOGGER.debug("No ISYGLT configuration found")
        return True

    hub_name: str = conf[CONF_HUB]
    devices: List[Dict[str, Any]] = conf[CONF_DEVICES]
    poll_interval: float = conf.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)

    # Determine bulk Modbus range for this hub
    min_addr = min(d[CONF_ADDRESS] for d in devices)

    spans = []
    for d in devices:
        reg_cnt = 1
        if d[CONF_TYPE] == DEVICE_TYPE_RGB_LIGHT:
            reg_cnt = LIGHT_REGISTER_COUNT_RGB
        elif d[CONF_TYPE] == DEVICE_TYPE_WHITE_LIGHT:
            reg_cnt = LIGHT_REGISTER_COUNT_WHITE
        elif d[CONF_TYPE] == DEVICE_TYPE_DIMMER:
            reg_cnt = LIGHT_REGISTER_COUNT_DIMMER
        elif d[CONF_TYPE] == DEVICE_TYPE_MOTION_SENSOR:
            reg_cnt = 2  # motion sensor ch0+ch1 minimal
        elif d[CONF_TYPE] == DEVICE_TYPE_BUTTON_GRID:
            reg_cnt = 2  # ch0+ch1
        elif d[CONF_TYPE] == DEVICE_TYPE_IO_MODULE:
            reg_cnt = 2
        start = d[CONF_ADDRESS]
        end = start + reg_cnt - 1
        spans.append((start, end))

    spans.sort()
    BLOCK_LIMIT = 125
    GAP_THRESHOLD = 16  # registers; if gap larger, start new range
    ranges = []
    if spans:
        cur_start, cur_end = spans[0]
        for s, e in spans[1:]:
            if s - cur_end - 1 <= GAP_THRESHOLD and (e - cur_start + 1) <= BLOCK_LIMIT:
                cur_end = max(cur_end, e)
            else:
                ranges.append((cur_start, cur_end - cur_start + 1))
                cur_start, cur_end = s, e
        ranges.append((cur_start, cur_end - cur_start + 1))

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["hub"] = hub_name
    hass.data[DOMAIN]["devices"] = devices
    hass.data[DOMAIN]["poll_interval"] = poll_interval
    hass.data[DOMAIN].setdefault("bulk_throttle", {})[hub_name] = 0.0
    hass.data[DOMAIN].setdefault("last_write", {})[hub_name] = 0.0

    # --- Clean up stale entities ---
    valid_prefixes = [f"{hub_name}_{slugify(d[CONF_NAME])}_{d[CONF_TYPE]}" for d in devices]
    ent_reg = er.async_get(hass)
    for entry in list(ent_reg.entities.values()):
        if entry.domain not in ("light", "switch", "binary_sensor", "sensor"):
            continue
        if entry.platform != DOMAIN:
            continue
        if not any(entry.unique_id.startswith(p) for p in valid_prefixes):
            _LOGGER.debug("Removing stale ISYGLT entity %s", entry.entity_id)
            ent_reg.async_remove(entry.entity_id)

    valid_ranges = [r for r in ranges if r[1] <= BLOCK_LIMIT]
    if valid_ranges:
        hass.data[DOMAIN].setdefault("bulk_range", {})[hub_name] = valid_ranges

    scan_td = timedelta(seconds=poll_interval * 2)

    # Import platform modules in executor so setting SCAN_INTERVAL occurs before HA reads it
    import importlib
    for platform in PLATFORMS:
        module = await hass.async_add_executor_job(importlib.import_module, f". {platform}".replace(" ", ""), __name__)
        setattr(module, "SCAN_INTERVAL", scan_td)

        _LOGGER.debug("Forwarding setup for platform %s", platform)
        hass.async_create_task(
            discovery.async_load_platform(
                hass,
                platform,
                DOMAIN,
                {
                    "devices": devices,
                    "hub": hub_name,
                    "poll_interval": poll_interval,
                },
                config,
            )
        )

    return True 