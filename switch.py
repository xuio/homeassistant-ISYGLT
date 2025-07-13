import logging
from typing import List
from datetime import timedelta
import asyncio

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import CONF_NAME
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers import device_registry as dr
from homeassistant.util import slugify

from .const import (
    CONF_ADDRESS,
    DEVICE_TYPE_IO_MODULE,
    DEVICE_TYPE_BUTTON_GRID,
    DEVICE_TYPE_MOTION_SENSOR,
    DEVICE_TYPE_GROUP_SWITCH,
    CONF_BIT,
    DOMAIN,
)
from .helpers import IsyGltModbusMixin

_LOGGER = logging.getLogger(__name__)

IO_OUTPUT_BITS = {
    1: 0x01,
    2: 0x02,
    3: 0x04,
    4: 0x08,
    5: 0x10,
    6: 0x20,
    7: 0x40,
    8: 0x80,
}

BUTTON_LED_BITS = {
    1: 0x01,
    2: 0x02,
    3: 0x04,
    4: 0x08,
    5: 0x10,
    6: 0x20,
}
BACKLIGHT_BIT = 0x40  # CH1 bit 7 (1-indexed)
MOTION_LED_BIT = 0x80  # CH0 bit 7

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
        if dev_type == DEVICE_TYPE_IO_MODULE:
            for idx in range(1, 9):
                entities.append(IsyGltIOOutputSwitch(hass, hub_name, dev, idx))
        elif dev_type == DEVICE_TYPE_MOTION_SENSOR:
            entities.append(IsyGltMotionLedSwitch(hass, hub_name, dev))
        elif dev_type == DEVICE_TYPE_GROUP_SWITCH:
            bit = dev.get(CONF_BIT, 1)
            entities.append(IsyGltGroupSwitch(hass, hub_name, dev, bit))
        elif dev_type == DEVICE_TYPE_BUTTON_GRID:
            for idx in range(1, 7):
                entities.append(IsyGltButtonLedSwitch(hass, hub_name, dev, idx))
            entities.append(IsyGltBacklightSwitch(hass, hub_name, dev))

    if entities:
        async_add_entities(entities)


class IsyGltBaseSwitch(IsyGltModbusMixin, SwitchEntity):
    def __init__(self, hass, hub_name, cfg):
        IsyGltModbusMixin.__init__(self, hass, hub_name)
        self._name_prefix = cfg[CONF_NAME]
        self._address = cfg[CONF_ADDRESS]
        base_id = f"{hub_name}_{slugify(cfg[CONF_NAME])}_{cfg['type']}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, base_id)},
            "name": self._name_prefix,
            "manufacturer": "ISYGLT",
            "model": cfg["type"],
        }
        self._base_unique = base_id

        dev_reg = dr.async_get(hass)
        # Device registry auto-creation via _attr_device_info
        self._available = True
        self._is_on = False
        self._unsubscribe = async_dispatcher_connect(
            hass, "isyglt_reg_updated", self.async_schedule_update_ha_state
        )

    @property
    def available(self):
        return self._available

    @property
    def is_on(self):
        return self._is_on

    async def async_will_remove_from_hass(self):
        if self._unsubscribe:
            self._unsubscribe()

    # Provide blocking API expected by ToggleEntity

    def turn_on(self, **kwargs):  # type: ignore[override]
        asyncio.run_coroutine_threadsafe(self.async_turn_on(**kwargs), self.hass.loop).result()

    def turn_off(self, **kwargs):  # type: ignore[override]
        asyncio.run_coroutine_threadsafe(self.async_turn_off(**kwargs), self.hass.loop).result()


class IsyGltIOOutputSwitch(IsyGltBaseSwitch):
    def __init__(self, hass, hub_name, cfg, output_idx: int):
        super().__init__(hass, hub_name, cfg)
        self._output_idx = output_idx
        self._bitmask = IO_OUTPUT_BITS[output_idx]
        self._attr_name = f"{self._name_prefix} Output {output_idx}"
        self._attr_unique_id = f"{self._base_unique}_out{output_idx}"

    async def async_update(self):
        regs = await self.async_read_registers(self._address, 1)
        if regs is None:
            self._available = False
            return
        self._available = True
        value = regs[0] & 0xFF
        self._is_on = bool(value & self._bitmask)

    async def async_turn_on(self, **kwargs):
        await self._write_state(True)

    async def async_turn_off(self, **kwargs):
        await self._write_state(False)

    async def _write_state(self, turn_on: bool):
        regs = await self.async_read_registers(self._address, 1)
        if regs is None:
            return
        value = regs[0] & 0xFF
        if turn_on:
            value |= self._bitmask
        else:
            value &= ~self._bitmask & 0xFF
        await self.async_write_registers(self._address, [value])
        self._is_on = turn_on


class IsyGltButtonLedSwitch(IsyGltBaseSwitch):
    """Status LED for each button on grid."""

    def __init__(self, hass, hub_name, cfg, button_idx: int):
        super().__init__(hass, hub_name, cfg)
        self._button_idx = button_idx
        self._bitmask = BUTTON_LED_BITS[button_idx]
        self._led_address = self._address + 1  # CH1
        self._attr_name = f"{self._name_prefix} Button {button_idx} LED"
        self._attr_unique_id = f"{self._base_unique}_led{button_idx}"

    async def async_update(self):
        regs = await self.async_read_registers(self._led_address, 1)
        if regs is None:
            self._available = False
            value = 0
        else:
            self._available = True
            value = regs[0] & 0xFF
        self._is_on = bool(value & self._bitmask)

    async def async_turn_on(self, **kwargs):
        await self._write_state(True)

    async def async_turn_off(self, **kwargs):
        await self._write_state(False)

    async def _write_state(self, turn_on: bool):
        regs = await self.async_read_registers(self._led_address, 1)
        if regs is None:
            value = 0
        else:
            value = regs[0] & 0xFF
        if turn_on:
            value |= self._bitmask
        else:
            value &= ~self._bitmask & 0xFF
        await self.async_write_registers(self._led_address, [value])
        self._is_on = turn_on


class IsyGltBacklightSwitch(IsyGltBaseSwitch):
    """Backlight LED for button grid."""

    def __init__(self, hass, hub_name, cfg):
        super().__init__(hass, hub_name, cfg)
        self._bitmask = BACKLIGHT_BIT
        self._led_address = self._address + 1  # CH1
        self._attr_name = f"{self._name_prefix} Backlight"
        self._attr_unique_id = f"{self._base_unique}_backlight"


class IsyGltGroupSwitch(IsyGltBaseSwitch):
    """Group switch controlling single bit in configured channel."""

    def __init__(self, hass, hub_name, cfg, bit_pos: int):
        super().__init__(hass, hub_name, cfg)
        self._bitmask = 1 << (bit_pos - 1)
        self._attr_name = f"{self._name_prefix} Group {bit_pos}"
        self._attr_unique_id = f"{self._base_unique}_grp{bit_pos}"

    async def async_update(self):
        regs = await self.async_read_registers(self._address, 1)
        if regs is None:
            self._available = False
            value = 0
        else:
            self._available = True
            value = regs[0] & 0xFF
        self._is_on = bool(value & self._bitmask)

    async def async_turn_on(self, **kwargs):
        await self._write_state(True)

    async def async_turn_off(self, **kwargs):
        await self._write_state(False)

    async def _write_state(self, turn_on: bool):
        regs = await self.async_read_registers(self._address, 1)
        if regs is None:
            value = 0
        else:
            value = regs[0] & 0xFF
        if turn_on:
            value |= self._bitmask
        else:
            value &= ~self._bitmask & 0xFF
        await self.async_write_registers(self._address, [value])
        self._is_on = turn_on


class IsyGltMotionLedSwitch(IsyGltBaseSwitch):
    """LED indicator on motion sensor"""

    def __init__(self, hass, hub_name, cfg):
        super().__init__(hass, hub_name, cfg)
        self._bitmask = MOTION_LED_BIT
        self._attr_name = f"{self._name_prefix} LED"
        self._attr_unique_id = f"{self._base_unique}_led"

    async def async_update(self):
        regs = await self.async_read_registers(self._address, 1)
        if regs is None:
            self._available = False
            return
        self._available = True
        value = regs[0] & 0xFF
        self._is_on = bool(value & self._bitmask)

    async def async_turn_on(self, **kwargs):
        await self._write_state(True)

    async def async_turn_off(self, **kwargs):
        await self._write_state(False)

    async def _write_state(self, turn_on: bool):
        regs = await self.async_read_registers(self._address, 1)
        if regs is None:
            return
        value = regs[0] & 0xFF
        if turn_on:
            value |= self._bitmask
        else:
            value &= ~self._bitmask & 0xFF
        await self.async_write_registers(self._address, [value])
        self._is_on = turn_on 