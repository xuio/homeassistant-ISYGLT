import logging
from typing import Any, Dict, List

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import callback, HomeAssistant
from homeassistant.helpers import config_validation as cv

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

_LOGGER = logging.getLogger(__name__)


class IsyGltConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    reauth_entry: config_entries.ConfigEntry | None = None

    async def async_step_user(self, user_input: Dict[str, Any] | None = None):
        errors: Dict[str, str] = {}
        if user_input is not None:
            # Only ask for hub name and poll interval here; devices added via options flow
            return self.async_create_entry(title=user_input[CONF_HUB], data=user_input)

        schema = vol.Schema({
            vol.Required(CONF_HUB): str,
            vol.Optional(CONF_POLL_INTERVAL, default=1.0): vol.All(vol.Coerce(float), vol.Range(min=0.1)),
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

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
        self.devices: List[Dict[str, Any]] = list(config_entry.options.get(CONF_DEVICES, []))

    async def async_step_init(self, user_input: Dict[str, Any] | None = None):
        import yaml
        if user_input is not None:
            raw = user_input.get("devices_yaml", "[]")
            try:
                parsed = yaml.safe_load(raw) or []
                if not isinstance(parsed, list):
                    raise ValueError
                self.devices = parsed
                return self.async_create_entry(title="Devices", data={CONF_DEVICES: self.devices})
            except Exception:
                return self.async_show_form(
                    step_id="init",
                    data_schema=vol.Schema({vol.Optional("devices_yaml", default=raw): str}),
                    errors={"base": "invalid_yaml"},
                )

        import yaml
        devices_yaml = yaml.safe_dump(self.devices, sort_keys=False)
        schema = vol.Schema({vol.Optional("devices_yaml", default=devices_yaml): str})
        return self.async_show_form(step_id="init", data_schema=schema) 