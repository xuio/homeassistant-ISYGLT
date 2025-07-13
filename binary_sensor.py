import logging
from typing import List
from datetime import timedelta

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.const import CONF_NAME
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers import device_registry as dr

from .const import (
    CONF_ADDRESS,
    DEVICE_TYPE_MOTION_SENSOR,
    DEVICE_TYPE_BUTTON_GRID,
    DEVICE_TYPE_IO_MODULE,
    ATTR_ZONE,
    ATTR_INPUT,
    DOMAIN,
)
from .helpers import IsyGltModbusMixin

_LOGGER = logging.getLogger(__name__)

MOTION_ZONE_BITS = {
    1: 0x01,
    2: 0x02,
    3: 0x04,
    4: 0x08,
}

BUTTON_BITS = {
    1: 0x01,
    2: 0x02,
    3: 0x04,
    4: 0x08,
    5: 0x10,
    6: 0x20,
}

IO_INPUT_BITS = {
    1: 0x01,
    2: 0x02,
    3: 0x04,
    4: 0x08,
    5: 0x10,
    6: 0x20,
    7: 0x40,
    8: 0x80,
}

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
        SCAN_INTERVAL = timedelta(seconds=base * 2)

    for dev in devices:
        dev_type = dev.get("type")
        if dev_type == DEVICE_TYPE_MOTION_SENSOR:
            for zone in range(1, 5):
                entities.append(IsyGltMotionZoneSensor(hass, hub_name, dev, zone))
            entities.append(IsyGltMotionAnySensor(hass, hub_name, dev))
        elif dev_type == DEVICE_TYPE_IO_MODULE:
            for idx in range(1, 9):
                entities.append(IsyGltIOInputSensor(hass, hub_name, dev, idx))
        elif dev_type == DEVICE_TYPE_BUTTON_GRID:
            for btn in range(1, 7):
                entities.append(IsyGltButtonSensor(hass, hub_name, dev, btn))

    if entities:
        async_add_entities(entities)


class IsyGltBaseBinarySensor(IsyGltModbusMixin, BinarySensorEntity):
    def __init__(self, hass, hub_name: str, cfg: dict):
        IsyGltModbusMixin.__init__(self, hass, hub_name)
        self._cfg = cfg
        self._name_prefix = cfg[CONF_NAME]
        self._address = cfg[CONF_ADDRESS]
        base_id = f"{hub_name}_{self._address}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, base_id)},
            "name": self._name_prefix,
            "manufacturer": "ISYGLT",
            "model": cfg["type"],
        }
        self._base_unique = base_id

        # Device registry auto-creation via _attr_device_info
        self._available = True
        self._unsubscribe = async_dispatcher_connect(
            hass, "isyglt_reg_updated", self.async_schedule_update_ha_state
        )

    @property
    def available(self):
        return self._available

    async def async_will_remove_from_hass(self):
        if self._unsubscribe:
            self._unsubscribe()


class IsyGltMotionZoneSensor(IsyGltBaseBinarySensor):
    _attr_device_class = BinarySensorDeviceClass.PRESENCE

    def __init__(self, hass, hub_name, cfg, zone: int):
        super().__init__(hass, hub_name, cfg)
        self._zone = zone
        self._bitmask = MOTION_ZONE_BITS[zone]
        self._attr_name = f"{self._name_prefix} Zone {zone} Presence"
        self._state = False
        self._attr_unique_id = f"{self._base_unique}_zone{zone}"

    @property
    def is_on(self):
        return self._state

    async def async_update(self):
        regs = await self.async_read_registers(self._address, 1)
        if regs is None:
            self._available = False
            return
        self._available = True
        value = regs[0] & 0xFF
        self._state = bool(value & self._bitmask)


class IsyGltMotionAnySensor(IsyGltBaseBinarySensor):
    _attr_device_class = BinarySensorDeviceClass.PRESENCE

    def __init__(self, hass, hub_name, cfg):
        super().__init__(hass, hub_name, cfg)
        self._attr_name = f"{self._name_prefix} Presence"
        self._state = False
        self._attr_unique_id = f"{self._base_unique}_motion"

    @property
    def is_on(self):
        return self._state

    async def async_update(self):
        regs = await self.async_read_registers(self._address, 1)
        if regs is None:
            self._available = False
            return
        self._available = True
        value = regs[0] & 0xFF
        self._state = value & 0x0F != 0  # any of first 4 bits


class IsyGltButtonSensor(IsyGltBaseBinarySensor):
    """Button press represented as binary sensor (on when pressed)."""

    def __init__(self, hass, hub_name, cfg, button_idx: int):
        super().__init__(hass, hub_name, cfg)
        self._button_idx = button_idx
        self._bitmask = BUTTON_BITS[button_idx]
        self._attr_name = f"{self._name_prefix} Button {button_idx}"
        self._state = False
        self._prev_state = False
        self._attr_unique_id = f"{self._base_unique}_btn{button_idx}"

    @property
    def is_on(self):
        return self._state

    async def async_update(self):
        regs = await self.async_read_registers(self._address, 1)
        if regs is None:
            self._available = False
            return
        self._available = True
        value = regs[0] & 0xFF
        self._state = bool(value & self._bitmask)

        # Emit event on rising edge
        if self._state and not self._prev_state:
            dev_id = getattr(self, "device_entry", None)
            self.hass.bus.async_fire(
                "isyglt_button_pressed",
                {
                    "device_id": dev_id.id if dev_id else None,
                    "button": self._button_idx,
                    "entity_id": self.entity_id,
                },
            )

        self._prev_state = self._state


class IsyGltIOInputSensor(IsyGltBaseBinarySensor):
    """Inputs of IO module."""

    def __init__(self, hass, hub_name, cfg, input_idx: int):
        super().__init__(hass, hub_name, cfg)
        self._input_idx = input_idx
        self._bitmask = IO_INPUT_BITS[input_idx]
        self._io_input_address = self._address + 1  # Inputs are on CH1 = base +1
        self._attr_name = f"{self._name_prefix} Input {input_idx}"
        self._state = False
        self._attr_unique_id = f"{self._base_unique}_in{input_idx}"

    @property
    def is_on(self):
        return self._state

    async def async_update(self):
        regs = await self.async_read_registers(self._io_input_address, 1)
        if regs is None:
            self._available = False
            return
        self._available = True
        value = regs[0] & 0xFF
        self._state = bool(value & self._bitmask) 