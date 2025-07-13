from __future__ import annotations

import logging
from datetime import timedelta
from typing import List

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.const import CONF_NAME
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers import device_registry as dr
from homeassistant.util import slugify

from .const import (
    DEVICE_TYPE_MOTION_SENSOR,
    CONF_ADDRESS,
    CONF_PRESCALER,
    DOMAIN,
)
from .helpers import IsyGltModbusMixin

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=1)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    if discovery_info is None:
        return

    devices = discovery_info.get("devices", [])
    hub_name = discovery_info.get("hub")

    entities: List[Entity] = []

    if discovery_info and "poll_interval" in discovery_info:
        global SCAN_INTERVAL
        base = float(discovery_info["poll_interval"])
        # add small buffer to avoid warning from long update vs interval
        SCAN_INTERVAL = timedelta(seconds=base * 2.5)

    for dev in devices:
        if dev.get("type") == DEVICE_TYPE_MOTION_SENSOR:
            entities.append(IsyGltIlluminanceSensor(hass, hub_name, dev))

    if entities:
        async_add_entities(entities)


class IsyGltIlluminanceSensor(IsyGltModbusMixin, SensorEntity):
    """Illuminance sensor exposed by an ISYGLT motion sensor (16-bit value)."""

    _attr_device_class = SensorDeviceClass.ILLUMINANCE
    _attr_native_unit_of_measurement = "lx"

    def __init__(self, hass, hub_name: str, cfg: dict):
        IsyGltModbusMixin.__init__(self, hass, hub_name)
        self._name_prefix = cfg[CONF_NAME]
        self._address = cfg[CONF_ADDRESS] + 1  # CH1 is base+1 (high byte)
        self._prescaler: float = float(cfg.get(CONF_PRESCALER, 1))
        base_id = f"{hub_name}_{slugify(cfg[CONF_NAME])}_{cfg['type']}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, base_id)},
            "name": cfg[CONF_NAME],
            "manufacturer": "ISYGLT",
            "model": cfg["type"],
        }
        self._attr_unique_id = f"{base_id}_lux"

        dev_reg = dr.async_get(hass)
        # Device registry auto-creation via _attr_device_info
        self._available = True
        self._attr_name = f"{self._name_prefix} Illuminance"
        self._native_value: int | None = None
        self._unsubscribe = async_dispatcher_connect(
            hass, "isyglt_reg_updated", self.async_schedule_update_ha_state
        )

    async def async_will_remove_from_hass(self):
        if self._unsubscribe:
            self._unsubscribe()

    @property
    def available(self):
        return self._available

    @property
    def native_value(self):
        return self._native_value

    async def async_update(self):
        # read two registers: high byte (CH1) and low byte (CH2)
        regs = await self.async_read_registers(self._address, 2)
        if regs is None:
            self._available = False
            return
        self._available = True
        high = regs[0] & 0xFF
        low = regs[1] & 0xFF
        raw = (high << 8) | low
        self._native_value = raw / self._prescaler 