"""Config flow for Roehn Wizard integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError

from .const import CONF_SCAN_INTERVAL, DEFAULT_NAME, DEFAULT_PORT, DEFAULT_SCAN_INTERVAL, DOMAIN
from .protocol import RoehnClient


STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): str,
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.All(int, vol.Range(min=5, max=3600)),
    }
)


@callback
def configured_hosts(hass) -> set[str]:
    """Return already configured hosts."""
    return {entry.data[CONF_HOST] for entry in hass.config_entries.async_entries(DOMAIN)}


async def validate_input(hass, data: dict[str, Any]) -> dict[str, Any]:
    """Validate user input by checking processor responses."""
    client = RoehnClient(host=data[CONF_HOST], port=data[CONF_PORT], timeout=2.5, command_timeout=3.0)
    processor = await hass.async_add_executor_job(client.query_processor_discovery_info)
    bios = await hass.async_add_executor_job(client.query_processor_bios_info)
    devices = await hass.async_add_executor_job(client.query_devices)

    if processor is None and bios is None and not devices:
        raise CannotConnect

    title = data[CONF_NAME]
    if processor and processor.name:
        title = processor.name

    return {"title": title}


class RoehnWizardConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle config flow for Roehn Wizard."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle first step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            if host in configured_hosts(self.hass):
                errors[CONF_HOST] = "already_configured"
            else:
                try:
                    info = await validate_input(self.hass, user_input)
                except CannotConnect:
                    errors["base"] = "cannot_connect"
                except Exception:  # pragma: no cover
                    errors["base"] = "unknown"
                else:
                    await self.async_set_unique_id(host)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors)


class CannotConnect(HomeAssistantError):
    """Error to indicate cannot connect."""
