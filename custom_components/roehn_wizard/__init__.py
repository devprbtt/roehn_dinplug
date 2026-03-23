"""The Roehn Wizard integration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.components.http import StaticPathConfig
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.device_registry import DeviceInfo

from .const import CONF_SCAN_INTERVAL, DOMAIN, MANUFACTURER, MODEL, PLATFORMS
from .coordinator import RoehnCoordinator
from .protocol import RoehnClient
from .resources import ResourcesIndex, load_resources_index


@dataclass(slots=True)
class RoehnRuntimeData:
    """Runtime state for one config entry."""

    coordinator: RoehnCoordinator
    resources: ResourcesIndex
    images_url_base: str


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Roehn Wizard from a config entry."""
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    scan_interval = entry.data[CONF_SCAN_INTERVAL]

    client = RoehnClient(host=host, port=port, timeout=2.5, command_timeout=3.0)
    coordinator = RoehnCoordinator(
        hass=hass,
        entry=entry,
        client=client,
        update_interval_seconds=scan_interval,
    )
    domain_data = hass.data.setdefault(DOMAIN, {})
    images_url_base = domain_data.get("images_url_base")
    if images_url_base is None:
        images_url_base = await _async_register_images_path(hass)
        domain_data["images_url_base"] = images_url_base
    resources = await hass.async_add_executor_job(load_resources_index)

    try:
        await coordinator.async_config_entry_first_refresh()
        await coordinator.async_start_event_listener()
    except Exception as err:
        raise ConfigEntryNotReady(f"Unable to connect to Roehn processor at {host}:{port}") from err

    entry.runtime_data = RoehnRuntimeData(
        coordinator=coordinator,
        resources=resources,
        images_url_base=images_url_base,
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload Roehn Wizard entry."""
    runtime: RoehnRuntimeData = entry.runtime_data
    await runtime.coordinator.async_stop_event_listener()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


def processor_device_info(entry: ConfigEntry) -> DeviceInfo:
    """Return device info for the processor itself."""
    host = entry.data[CONF_HOST]
    name = entry.data[CONF_NAME]
    return DeviceInfo(
        manufacturer=MANUFACTURER,
        model=MODEL,
        name=name,
        identifiers={(DOMAIN, f"{host}:{entry.data[CONF_PORT]}")},
    )


def module_device_info(
    entry: ConfigEntry,
    serial_hex: str,
    model: str,
    ext_model: str,
    hsnet_id: int,
    model_base_name: str | None = None,
    driver_image: str | None = None,
) -> DeviceInfo:
    """Return device info for a module connected to the processor."""
    base_name = (model_base_name or "").strip() or ext_model or model or f"Module {serial_hex}"
    module_name = f"{hsnet_id} {base_name}"
    model_id = Path(driver_image).stem if driver_image else _normalize_model_id(base_name)
    return DeviceInfo(
        manufacturer=MANUFACTURER,
        model=base_name,
        model_id=model_id,
        name=module_name,
        identifiers={(DOMAIN, f"module:{serial_hex}")},
        via_device=(DOMAIN, f"{entry.data[CONF_HOST]}:{entry.data[CONF_PORT]}"),
    )


async def _async_register_images_path(hass: HomeAssistant) -> str:
    """Expose bundled module images so entities can reference them locally."""
    images_dir = Path(__file__).resolve().parent / "data" / "images"
    images_url_base = f"/api/{DOMAIN}/images"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(images_url_base, str(images_dir), cache_headers=False)]
    )
    return images_url_base


def _normalize_model_id(value: str) -> str:
    """Normalize a device model string for registry/frontend use."""
    return "".join(ch for ch in value.upper() if ch.isalnum() or ch in {"_", "-"}).strip("_-")
