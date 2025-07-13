import logging
from typing import Any, List
from datetime import timedelta
import asyncio

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_RGB_COLOR,
    ATTR_COLOR_TEMP_KELVIN,
    ColorMode,
    LightEntity,
)
from homeassistant.const import CONF_NAME
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers import device_registry as dr
from homeassistant.util import slugify

from .const import (
    DOMAIN,
    CONF_ADDRESS,
    CONF_CCT_MIN,
    CONF_CCT_MAX,
    DEVICE_TYPE_RGB_LIGHT,
    DEVICE_TYPE_WHITE_LIGHT,
    DEVICE_TYPE_DIMMER,
    LIGHT_REGISTER_COUNT_RGB,
    LIGHT_REGISTER_COUNT_WHITE,
    LIGHT_REGISTER_COUNT_DIMMER,
)
from .helpers import IsyGltModbusMixin

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=1)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up ISYGLT lights from discovery info."""
    if discovery_info is None:
        return

    devices = discovery_info.get("devices", [])
    hub_name = discovery_info.get("hub")

    entities: List[Entity] = []

    # Adjust scan interval if provided
    if "poll_interval" in discovery_info:
        global SCAN_INTERVAL
        base = float(discovery_info["poll_interval"])
        SCAN_INTERVAL = timedelta(seconds=base * 2)

    for dev in devices:
        if dev["type"] == DEVICE_TYPE_RGB_LIGHT:
            entities.append(IsyGltRGBLight(hass, hub_name, dev))
        elif dev["type"] == DEVICE_TYPE_WHITE_LIGHT:
            entities.append(IsyGltWhiteLight(hass, hub_name, dev))
        elif dev["type"] == DEVICE_TYPE_DIMMER:
            entities.append(IsyGltDimmerLight(hass, hub_name, dev))

    if entities:
        async_add_entities(entities)


class IsyGltBaseLight(IsyGltModbusMixin, LightEntity):
    """Common logic for ISYGLT lights."""

    def __init__(self, hass, hub_name: str, cfg: dict):
        IsyGltModbusMixin.__init__(self, hass, hub_name)
        self._name = cfg[CONF_NAME]
        self._address = cfg[CONF_ADDRESS]
        # Device info for grouping
        base_id = f"{hub_name}_{slugify(cfg[CONF_NAME])}_{cfg['type']}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, base_id)},
            "name": cfg[CONF_NAME],
            "manufacturer": "ISYGLT",
            "model": cfg["type"],
        }

        self.device_entry = self.ensure_device_entry(base_id, cfg[CONF_NAME], cfg["type"])
        # base unique id - subclasses append more if needed
        self._base_unique = base_id

        # Ensure device exists in registry and link entity when using YAML (no ConfigEntry)
        # Device registry entry will be created automatically from _attr_device_info
        self._is_on = False
        self._brightness = 255
        self._rgb_color = (255, 255, 255)
        self._available = True
        self._unsubscribe_dispatcher = async_dispatcher_connect(
            hass, "isyglt_reg_updated", self.async_schedule_update_ha_state
        )

    @property
    def available(self) -> bool:
        return self._available

    @property
    def name(self):
        return self._name

    async def async_will_remove_from_hass(self):
        if self._unsubscribe_dispatcher:
            self._unsubscribe_dispatcher()

    # Home Assistant's default async_turn_on/turn_off expect blocking versions.
    # Provide sync stubs executed in executor threads which delegate to our async implementation.

    def turn_on(self, **kwargs):  # type: ignore[override]
        asyncio.run_coroutine_threadsafe(self._async_set_power(True, **kwargs), self.hass.loop).result()

    def turn_off(self, **kwargs):  # type: ignore[override]
        asyncio.run_coroutine_threadsafe(self._async_set_power(False, **kwargs), self.hass.loop).result()

    async def _async_set_power(self, turn_on: bool, **kwargs):
        raise NotImplementedError()


class IsyGltRGBLight(IsyGltBaseLight):
    """Representation of an ISYGLT DMX RGB light."""

    _attr_supported_color_modes = {ColorMode.RGB}

    def __init__(self, hass, hub_name: str, cfg: dict):
        super().__init__(hass, hub_name, cfg)
        self._attr_unique_id = f"{self._base_unique}_rgb"
        self._address_end = self._address + LIGHT_REGISTER_COUNT_RGB - 1

    @property
    def color_mode(self):
        return ColorMode.RGB

    @property
    def rgb_color(self):
        return self._rgb_color

    @property
    def brightness(self):
        return self._brightness

    @property
    def is_on(self):
        return self._is_on

    async def async_update(self):
        regs = await self.async_read_registers(self._address, LIGHT_REGISTER_COUNT_RGB)
        if regs is None:
            self._available = False
            return
        self._available = True
        # each register low byte only holds 0-255; high bytes may hold next channel
        ch0 = regs[0] & 0xFF
        dim_val = regs[1] & 0xFF
        r = regs[2] & 0xFF
        g = regs[3] & 0xFF
        b = regs[4] & 0xFF
        self._rgb_color = (r, g, b)
        self._is_on = bool(ch0 & 0x01)
        self._brightness = dim_val if (ch0 & 0x02) else 255

        # If color enable bit not set, HA isn't controlling; still reflect values but could note.

    async def _async_set_power(self, turn_on: bool, **kwargs):
        # Determine target color and brightness
        current_rgb = self._rgb_color
        rgb_param = kwargs.get(ATTR_RGB_COLOR)
        brightness_param = kwargs.get(ATTR_BRIGHTNESS)

        if rgb_param is not None:
            current_rgb = rgb_param

        if brightness_param is None:
            target_brightness = self._brightness
        else:
            target_brightness = brightness_param

        if not turn_on:
            # Simply clear power bit and keep current dim/RGB values.
            regs = await self.async_read_registers(self._address, LIGHT_REGISTER_COUNT_RGB)
            if regs is None:
                return
            ch0 = regs[0] & 0xFE  # clear power bit (bit0)
            regs[0] = ch0
            await self.async_write_registers(self._address, regs)

            self._is_on = False
            # keep brightness and color unchanged
            return

        # No brightness scaling of RGB; use separate dim register
        rgb_raw = current_rgb

        dim_val = int(max(0, min(255, target_brightness)))

        ch0 = 0
        if turn_on:
            ch0 |= 0x01  # power
            ch0 |= 0x04  # COLOR_EN (bit2) so fixture follows our color
            if dim_val != 255:
                ch0 |= 0x02  # DIM_EN (bit1)

        regs = [ch0, dim_val, rgb_raw[0], rgb_raw[1], rgb_raw[2]]
        await self.async_write_registers(self._address, regs)

        # Optimistically update internal state
        self._rgb_color = current_rgb
        self._brightness = target_brightness
        self._is_on = turn_on


class IsyGltWhiteLight(IsyGltBaseLight):
    """Representation of an ISYGLT white CCT light."""

    _attr_supported_color_modes = {ColorMode.COLOR_TEMP}

    def __init__(self, hass, hub_name: str, cfg: dict):
        super().__init__(hass, hub_name, cfg)
        self._attr_unique_id = f"{self._base_unique}_white"
        self._cct_min = cfg[CONF_CCT_MIN]
        self._cct_max = cfg[CONF_CCT_MAX]

        # Home Assistant 2026.1 deprecates mireds defaults. Explicitly
        # expose Kelvin limits via the new attributes so we avoid the
        # frame warnings about min/max mireds.
        self._attr_min_color_temp_kelvin = self._cct_min
        self._attr_max_color_temp_kelvin = self._cct_max
        self._color_temp_kelvin = (self._cct_min + self._cct_max) // 2
        self._address_end = self._address + LIGHT_REGISTER_COUNT_WHITE - 1

    @property
    def color_mode(self):
        return ColorMode.COLOR_TEMP

    @property
    def color_temp_kelvin(self):
        return self._color_temp_kelvin

    @property
    def brightness(self):
        return self._brightness

    @property
    def is_on(self):
        return self._is_on

    async def async_update(self):
        regs = await self.async_read_registers(self._address, LIGHT_REGISTER_COUNT_WHITE)
        if regs is None:
            self._available = False
            return
        self._available = True
        ch0 = regs[0] & 0xFF
        cct_value = regs[1] & 0xFF
        dim_value = regs[2] & 0xFF

        self._is_on = bool(ch0 & 0x01)
        cct_enabled = bool(ch0 & 0x02)
        dim_enabled = bool(ch0 & 0x04)

        if cct_enabled:
            # Inverted mapping: 0 -> coldest (max Kelvin), 255 -> warmest (min Kelvin)
            self._color_temp_kelvin = int(
                self._cct_max - (self._cct_max - self._cct_min) * (cct_value / 255)
            )
        if dim_enabled:
            self._brightness = int(dim_value)
        else:
            self._brightness = 255

    async def _async_set_power(self, turn_on: bool, **kwargs):
        brightness = kwargs.get(ATTR_BRIGHTNESS, self._brightness)
        color_temp = kwargs.get(ATTR_COLOR_TEMP_KELVIN, self._color_temp_kelvin)

        ch0 = 0
        if turn_on:
            ch0 |= 0x01  # POWER ENABLE
        # Determine if CCT or dimming changed
        # Inverted mapping: higher Kelvin produces lower register value
        cct_value = int(
            max(
                0,
                min(
                    255,
                    int(
                        (self._cct_max - color_temp)
                        / (self._cct_max - self._cct_min)
                        * 255
                    ),
                ),
            )
        )
        if cct_value != 127:  # assume middle is no change
            ch0 |= 0x02  # CCT_ENABLE
        dim_value = int(max(0, min(255, brightness)))
        if dim_value != 255:
            ch0 |= 0x04  # DIM_MODE

        regs = [0, 0, 0]
        regs[0] = ch0
        regs[1] = cct_value
        regs[2] = dim_value

        await self.async_write_registers(self._address, regs)
        self._is_on = turn_on
        self._brightness = brightness
        self._color_temp_kelvin = color_temp 


class IsyGltDimmerLight(IsyGltBaseLight):
    """Simple dimmer light."""

    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, hass, hub_name: str, cfg: dict):
        super().__init__(hass, hub_name, cfg)
        self._attr_unique_id = f"{self._base_unique}_dimmer"
        self._address_end = self._address + LIGHT_REGISTER_COUNT_DIMMER - 1

    @property
    def brightness(self):
        return self._brightness

    @property
    def color_mode(self):
        return ColorMode.BRIGHTNESS

    @property
    def is_on(self):
        return self._is_on

    async def async_update(self):
        regs = await self.async_read_registers(self._address, LIGHT_REGISTER_COUNT_DIMMER)
        if regs is None:
            self._available = False
            return
        self._available = True
        ch0 = regs[0] & 0xFF
        dim_value = regs[1] & 0xFF

        self._is_on = bool(ch0 & 0x01)
        dim_enabled = bool(ch0 & 0x02)

        if dim_enabled:
            self._brightness = dim_value
        else:
            self._brightness = 255

    async def _async_set_power(self, turn_on: bool, **kwargs):
        brightness = kwargs.get(ATTR_BRIGHTNESS, self._brightness)

        ch0 = 0
        if turn_on:
            ch0 |= 0x01  # Power enable

        dim_value = int(max(0, min(255, brightness)))
        if dim_value != 255:
            ch0 |= 0x02  # DIM enable

        regs = [ch0, dim_value]
        await self.async_write_registers(self._address, regs)

        self._is_on = turn_on
        self._brightness = brightness 