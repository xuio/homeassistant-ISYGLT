import logging
from typing import Any, Dict, List, Optional

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import callback, HomeAssistant
# No longer using cv.yaml_dumper; keep cv import for basic validators
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import selector

from .const import (
    DOMAIN,
    CONF_HUB,
    CONF_DEVICES,
    CONF_TYPE,
    CONF_ADDRESS,
    CONF_CCT_MIN,
    CONF_CCT_MAX,
    CONF_POLL_INTERVAL,
    CONF_PRESCALER,
    CONF_BIT,
)

BUS_ADDR_KEY = "bus_addresses"

_LOGGER = logging.getLogger(__name__)


class IsyGltConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    reauth_entry: config_entries.ConfigEntry | None = None

    # -------- FIRST STEP: Choose configuration mode --------
    async def async_step_user(self, user_input: Dict[str, Any] | None = None):
        """Entry step – let user choose manual entry or YAML paste import."""
        if user_input is None:
            return self.async_show_menu(
                step_id="user",
                menu_options={
                    "manual": "Configure manually",
                    "import_yaml": "Import YAML configuration",
                },
            )

        # Should never get here since async_show_menu handles routing
        return self.async_abort(reason="invalid_selection")

    # -------- MANUAL FLOW (existing behavior) --------
    async def async_step_manual(self, user_input: Dict[str, Any] | None = None):
        """Manual setup: ask for hub name and poll interval. Devices are added later via options."""

        errors: Dict[str, str] = {}
        if user_input is not None:
            return self.async_create_entry(title=user_input[CONF_HUB], data=user_input)

        schema = vol.Schema({
            vol.Required(CONF_HUB): str,
            vol.Optional(CONF_POLL_INTERVAL, default=1.0): vol.All(vol.Coerce(float), vol.Range(min=0.1)),
        })
        return self.async_show_form(step_id="manual", data_schema=schema, errors=errors)

    # -------- YAML IMPORT FLOW --------
    async def async_step_import_yaml(self, user_input: Dict[str, Any] | None = None):
        """Paste YAML configuration to import."""
        import yaml

        if user_input is not None:
            raw_yaml = user_input.get("yaml_config", "")
            try:
                data = yaml.safe_load(raw_yaml) or {}
                if not isinstance(data, dict) or DOMAIN not in data:
                    raise ValueError("root_missing")

                cfg = data[DOMAIN]
                hub = cfg.get(CONF_HUB)
                devices = cfg.get(CONF_DEVICES, [])
                poll = cfg.get(CONF_POLL_INTERVAL, 1.0)

                if not hub or not isinstance(hub, str):
                    raise ValueError("hub")

                # Validate devices list minimal structure; skip deep validation here
                if not isinstance(devices, list):
                    raise ValueError("devices")

                # Create entry with hub & poll interval; devices stored into options
                entry = self.async_create_entry(
                    title=hub,
                    data={CONF_HUB: hub, CONF_POLL_INTERVAL: poll},
                    options={CONF_DEVICES: devices},
                )
                return entry
            except Exception as exc:
                _LOGGER.debug("YAML import failed: %s", exc)
                text_sel = selector.TextSelector(selector.TextSelectorConfig(multiline=True))
                return self.async_show_form(
                    step_id="import_yaml",
                    data_schema=vol.Schema({vol.Required("yaml_config", default=raw_yaml): text_sel}),
                    errors={"base": "invalid_yaml"},
                )

        sample = (
            f"{DOMAIN}:\n  {CONF_HUB}: MyHub\n  {CONF_POLL_INTERVAL}: 1.0\n  {CONF_DEVICES}:\n    - {CONF_TYPE}: dmx_rgb\n      {CONF_NAME}: Living Room\n      {CONF_ADDRESS}: 10\n"
        )
        text_sel = selector.TextSelector(selector.TextSelectorConfig(multiline=True))
        schema = vol.Schema({vol.Required("yaml_config", default=sample): text_sel})
        return self.async_show_form(step_id="import_yaml", data_schema=schema)

    async def async_step_import(self, import_data: Dict[str, Any]):
        """Handle import from YAML."""
        _LOGGER.debug("Importing ISYGLT YAML configuration")
        return await self.async_step_user(import_data)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return IsyGltOptionsFlow(config_entry)


class IsyGltOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry):
        self.config_entry = config_entry
        # Retrieve existing devices; prefer options, fall back to entry data (for legacy entries)
        if CONF_DEVICES in config_entry.options:
            self.devices = list(config_entry.options[CONF_DEVICES])  # type: ignore[arg-type]
        else:
            self.devices = list(config_entry.data.get(CONF_DEVICES, []))  # type: ignore[arg-type]
        self._device_index: Optional[int] = None  # index of device being edited/removed
        self._device_type: Optional[str] = None   # type selected when adding device

        # Cached bus addresses for script generation
        self.bus_addrs: Dict[str, int] = dict(config_entry.options.get(BUS_ADDR_KEY, {}))

    # -------------- MENU / ROOT STEP --------------
    async def async_step_init(self, user_input: Dict[str, Any] | None = None):
        """Initial step: show main menu for managing devices."""

        if user_input is not None:
            action = user_input["action"]
            if action == "add":
                return await self.async_step_add_select_type()
            if action == "edit":
                if not self.devices:
                    return await self._show_init_form(errors={"base": "no_devices"})
                return await self.async_step_edit_select_device()
            if action == "remove":
                if not self.devices:
                    return await self._show_init_form(errors={"base": "no_devices"})
                return await self.async_step_remove_select_device()
            # 'generate_script' temporarily disabled
            # finish -> save and exit
            return self.async_create_entry(title="Devices", data={CONF_DEVICES: self.devices})

        return await self._show_init_form()

    async def _show_init_form(self, errors: Optional[Dict[str, str]] = None):
        """Helper to render the root menu."""
        if errors is None:
            errors = {}

        menu = {
            "add": "Add device",
            "edit": "Edit device",
            "remove": "Remove device",
            # "generate_script": "Generate ISYGLT script",  # disabled for now
            "finish": "Save & finish",
        }
        schema = vol.Schema({vol.Required("action"): vol.In(menu)})
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)

    # -------------- ADD DEVICE FLOW --------------
    async def async_step_add_select_type(self, user_input: Dict[str, Any] | None = None):
        """Step to select the type of device to add."""
        from .const import (
            DEVICE_TYPE_RGB_LIGHT,
            DEVICE_TYPE_WHITE_LIGHT,
            DEVICE_TYPE_MOTION_SENSOR,
            DEVICE_TYPE_BUTTON_GRID,
            DEVICE_TYPE_IO_MODULE,
            DEVICE_TYPE_DIMMER,
            DEVICE_TYPE_GROUP_SWITCH,
        )

        types = {
            DEVICE_TYPE_RGB_LIGHT: "RGB DMX Light",
            DEVICE_TYPE_WHITE_LIGHT: "White Light",
            DEVICE_TYPE_DIMMER: "Dimmer",
            DEVICE_TYPE_MOTION_SENSOR: "Motion Sensor",
            DEVICE_TYPE_BUTTON_GRID: "Button Grid",
            DEVICE_TYPE_IO_MODULE: "I/O Module",
            DEVICE_TYPE_GROUP_SWITCH: "Group Switch",
        }

        if user_input is not None:
            self._device_type = user_input[CONF_TYPE]
            return await self.async_step_add_device_details()

        schema = vol.Schema({vol.Required(CONF_TYPE): vol.In(types)})
        return self.async_show_form(step_id="add_select_type", data_schema=schema)

    async def async_step_add_device_details(self, user_input: Dict[str, Any] | None = None):
        """Collect details for the new device based on the selected type."""
        assert self._device_type is not None  # mypy

        schema_dict = {
            vol.Required(CONF_NAME): str,
            vol.Required(CONF_ADDRESS): cv.positive_int,
        }

        if self._device_type == "white_light":
            schema_dict[vol.Optional(CONF_CCT_MIN, default=2512)] = cv.positive_int
            schema_dict[vol.Optional(CONF_CCT_MAX, default=5000)] = cv.positive_int
        if self._device_type == "group_switch":
            schema_dict[vol.Required(CONF_BIT)] = vol.All(vol.Coerce(int), vol.Range(min=1, max=8))

        # optional prescaler for some types
        if self._device_type in {"dmx_rgb", "white_light", "dimmer"}:
            schema_dict[vol.Optional(CONF_PRESCALER, default=1)] = vol.All(vol.Coerce(float), vol.Range(min=0.0001))

        schema = vol.Schema(schema_dict)

        if user_input is not None:
            device = {CONF_TYPE: self._device_type, **user_input}
            self.devices.append(device)
            _LOGGER.debug("Added device %s", device)
            # Reset temp vars
            self._device_type = None
            return await self.async_step_init()

        return self.async_show_form(step_id="add_device_details", data_schema=schema)

    # -------------- EDIT DEVICE FLOW --------------
    async def async_step_edit_select_device(self, user_input: Dict[str, Any] | None = None):
        """Choose which device to edit."""
        if not self.devices:
            return await self.async_step_init()

        choices = {str(idx): f"{d.get(CONF_NAME, 'Unnamed')} ({d[CONF_TYPE]} @ {d[CONF_ADDRESS]})" for idx, d in enumerate(self.devices)}

        if user_input is not None:
            self._device_index = int(user_input["idx"])
            # Pre-select type and move to edit details
            self._device_type = self.devices[self._device_index][CONF_TYPE]
            return await self.async_step_edit_device_details()

        schema = vol.Schema({vol.Required("idx"): vol.In(choices)})
        return self.async_show_form(step_id="edit_select_device", data_schema=schema)

    async def async_step_edit_device_details(self, user_input: Dict[str, Any] | None = None):
        """Edit details of the selected device."""
        if self._device_index is None:
            return await self.async_step_init()

        current = self.devices[self._device_index]

        schema_dict = {
            vol.Required(CONF_NAME, default=current.get(CONF_NAME, "")): str,
            vol.Required(CONF_ADDRESS, default=current.get(CONF_ADDRESS, 0)): cv.positive_int,
        }

        if current[CONF_TYPE] == "white_light":
            schema_dict[vol.Optional(CONF_CCT_MIN, default=current.get(CONF_CCT_MIN, 2512))] = cv.positive_int
            schema_dict[vol.Optional(CONF_CCT_MAX, default=current.get(CONF_CCT_MAX, 5000))] = cv.positive_int
        if current[CONF_TYPE] == "group_switch":
            schema_dict[vol.Required(CONF_BIT, default=current.get(CONF_BIT, 1))] = vol.All(vol.Coerce(int), vol.Range(min=1, max=8))
        if current[CONF_TYPE] in {"dmx_rgb", "white_light", "dimmer"}:
            schema_dict[vol.Optional(CONF_PRESCALER, default=current.get(CONF_PRESCALER, 1))] = vol.All(vol.Coerce(float), vol.Range(min=0.0001))

        schema = vol.Schema(schema_dict)

        if user_input is not None:
            # Replace existing device definition
            new_device = {CONF_TYPE: current[CONF_TYPE], **user_input}
            self.devices[self._device_index] = new_device
            _LOGGER.debug("Updated device idx %s to %s", self._device_index, new_device)
            self._device_index = None
            return await self.async_step_init()

        return self.async_show_form(step_id="edit_device_details", data_schema=schema)

    # -------------- REMOVE DEVICE FLOW --------------
    async def async_step_remove_select_device(self, user_input: Dict[str, Any] | None = None):
        """Select device(s) to remove."""
        if not self.devices:
            return await self.async_step_init()

        choices = {str(idx): f"{d.get(CONF_NAME, 'Unnamed')} ({d[CONF_TYPE]} @ {d[CONF_ADDRESS]})" for idx, d in enumerate(self.devices)}

        if user_input is not None:
            idx = int(user_input["idx"])
            removed = self.devices.pop(idx)
            _LOGGER.debug("Removed device %s", removed)
            return await self.async_step_init()

        schema = vol.Schema({vol.Required("idx"): vol.In(choices)})
        return self.async_show_form(step_id="remove_select_device", data_schema=schema)

    # -------------- GENERATE SCRIPT FLOW --------------

    async def async_step_generate_script(self, user_input: Dict[str, Any] | None = None):
        """Ask for extra parameters needed for script generation or display script."""

        # First ensure all bus addresses collected
        missing_keys = []
        for dev in self.devices:
            key = self._device_key(dev)
            if key not in self.bus_addrs:
                missing_keys.append(key)

        if missing_keys and user_input is None:
            # present bus address form
            fields = {}
            for dev in self.devices:
                key = self._device_key(dev)
                default = self.bus_addrs.get(key, dev.get(CONF_ADDRESS, 0))
                label = f"{dev.get(CONF_NAME, dev[CONF_TYPE])} (type {dev[CONF_TYPE]}, addr {dev[CONF_ADDRESS]})"
                fields[vol.Required(key, description={"suggested_value": default})] = cv.positive_int

            fields[vol.Optional("save", default=True)] = bool
            schema = vol.Schema(fields)
            return self.async_show_form(step_id="generate_script", data_schema=schema)

        if missing_keys and user_input is not None:
            # Update bus addresses cache
            save = user_input.pop("save", True)
            for key, val in user_input.items():
                self.bus_addrs[key] = val

            if save:
                new_options = {**self.config_entry.options, BUS_ADDR_KEY: self.bus_addrs}
                self.hass.config_entries.async_update_entry(self.config_entry, options=new_options)  # type: ignore[attr-defined]

            # After updating addresses, ask for params
            params_schema = vol.Schema({
                vol.Optional("dim_start_ne", default=30): cv.positive_int,
                vol.Optional("autooff_seconds", default=1): cv.positive_int,
            })
            return self.async_show_form(step_id="generate_script_params", data_schema=params_schema)

        # If we arrive here without missing addresses, check which step
        if user_input is None:
            # All addresses present, ask for params
            params_schema = vol.Schema({
                vol.Optional("dim_start_ne", default=30): cv.positive_int,
                vol.Optional("autooff_seconds", default=1): cv.positive_int,
            })
            return self.async_show_form(step_id="generate_script_params", data_schema=params_schema)

        # user_input from params step
        return await self._generate_script_show(user_input)

    async def async_step_generate_script_params(self, user_input: Dict[str, Any] | None = None):
        # Shouldn't be called directly; handled above
        if user_input is None:
            return await self.async_step_generate_script()
        return await self._generate_script_show(user_input)

    def _device_key(self, dev: Dict[str, Any]) -> str:
        """Return unique key for device for bus address cache."""
        from homeassistant.util import slugify
        return slugify(f"{dev.get(CONF_NAME, '')}_{dev[CONF_TYPE]}_{dev[CONF_ADDRESS]}")

    async def _generate_script_show(self, params: Dict[str, Any]):
        """Generate script and show."""
        dim_start_ne: int = params.get("dim_start_ne", 30)
        autooff: int = params.get("autooff_seconds", 1)

        script_lines: list[str] = []

        # ---- Motion sensors ----
        bwm_devices = [d for d in self.devices if d[CONF_TYPE] == "motion_sensor"]
        bwm_devices.sort(key=lambda d: d[CONF_ADDRESS])

        for dev in bwm_devices:
            addr_bus = self.bus_addrs[self._device_key(dev)]
            channel = addr_bus  # channel equals bus address for BWM

            script_lines.append(f"; BWM {addr_bus}  ->  NE{channel}\n")
            script_lines.append(f"TRF NE{channel} = E{addr_bus}.1, E{addr_bus}.2, E{addr_bus}.3, E{addr_bus}.4\n\n")
            script_lines.append(f"KOPIE A{addr_bus}.1 = NE{channel}.8\n\n")
            script_lines.append(f"TRFAD NE{channel+1} AE{addr_bus}.1 1\n")
            script_lines.append(f"TRFAD NE{channel+2} AE{addr_bus}.2 1\n\n")

        if bwm_devices:
            script_lines.append("\n")

        # ---- Dimmers / white lights / rgb ----
        dim_devices = [d for d in self.devices if d[CONF_TYPE] in ("dimmer", "white_light", "dmx_rgb")]
        dim_devices.sort(key=lambda d: d[CONF_ADDRESS])

        channel = dim_start_ne
        for idx, dev in enumerate(dim_devices, start=1):
            addr_bus = self.bus_addrs[self._device_key(dev)]
            name = dev.get(CONF_NAME, f"Dimmer {idx}")

            base_ch = channel
            dim_ch = channel + 1

            script_lines.append(f"; {name}\n; DIM {idx}\n\n")

            script_lines.append(f"TRFDA AA{addr_bus}.1 NE{dim_ch} 2s NE{base_ch}.1\n")
            script_lines.append(f"TRFDA AA{addr_bus}.1 %0 2s !NE{base_ch}.1\n")
            script_lines.append(f"TRFB NE{dim_ch} %100 !NE{base_ch}.2\n\n\n")

            script_lines.append(f"; {name} (Channel 2)\n; DIM {idx}\n\n")
            script_lines.append(f"TRFDA AA{addr_bus}.2 NE{dim_ch+2} 2s NE{base_ch+2}.1\n")
            script_lines.append(f"TRFDA AA{addr_bus}.2 %0 2s !NE{base_ch+2}.1\n")
            script_lines.append(f"TRFB NE{dim_ch+2} %100 !NE{base_ch+2}.2\n")

            channel += 4

        if dim_devices:
            script_lines.append("\n")

        # ---- Button grids ----
        grid_devices = [d for d in self.devices if d[CONF_TYPE] == "button_grid"]
        grid_devices.sort(key=lambda d: d[CONF_ADDRESS])

        for dev in grid_devices:
            addr_bus = self.bus_addrs[self._device_key(dev)]
            ne_addr = addr_bus

            script_lines.append(f"; SW {addr_bus}  -> NE{ne_addr}\n\n")
            script_lines.append("; BUTTONS\n\n")
            script_lines.append(f"; turn bits off again after {autooff}s, give modbus enough time to read them\n")
            for bit in range(1, 7):
                script_lines.append(f"AUTOOFF NE{ne_addr}.{bit} {autooff}s\n")
            script_lines.append("\n; rising edge detection\n")
            for bit in range(1, 7):
                script_lines.append(f"HFLANKE M{addr_bus}.{bit} E{addr_bus}.{bit}\n")
            script_lines.append("\n; set bit on on press\n")
            for bit in range(1, 7):
                script_lines.append(f"SET NE{ne_addr}.{bit} M{addr_bus}.{bit}\n")

            led_ne = ne_addr + 1
            script_lines.append("\n; LEDs\n")
            for bit in range(1, 8):
                script_lines.append(f"KOPIE A{addr_bus}.{bit} NE{led_ne}.{bit}\n")

            script_lines.append("\n")

        full_script = "\n".join(script_lines).strip()

        text_sel = selector.TextSelector(selector.TextSelectorConfig(multiline=True))
        return self.async_show_form(
            step_id="generate_script_result",
            data_schema=vol.Schema({vol.Optional("script", default=full_script): text_sel}),
            description_placeholders={"note": "Copy the generated code and paste it into ISYGLT"},
        ) 

    async def async_step_generate_script_result(self, user_input: Dict[str, Any] | None = None):
        """Handle closing of the generated script view and return to main menu."""
        # Simply go back to the root menu
        return await self.async_step_init() 