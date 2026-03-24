"""Constants for ROEHN DINPLUG integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "roehn_wizard"

CONF_SCAN_INTERVAL = "scan_interval"
DEFAULT_NAME = "ROEHN DINPLUG"
DEFAULT_PORT = 2006
DEFAULT_SCAN_INTERVAL = 30

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.LIGHT, Platform.COVER]

MANUFACTURER = "Roehn"
MODEL = "Wizard Processor"
