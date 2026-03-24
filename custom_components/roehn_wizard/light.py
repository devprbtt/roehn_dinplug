"""Light platform for Roehn Wizard integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import RoehnRuntimeData, module_device_info
from .coordinator import RoehnCoordinator
from .protocol import DeviceInfo
from .resources import ModuleDriverInfo, ResourcesIndex


@dataclass(slots=True)
class LightChannelDescription:
    serial_hex: str
    model: str
    extended_model: str
    driver_info: ModuleDriverInfo | None
    channel: int
    device_id: int
    hsnet_id: int
    supports_brightness: bool


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Roehn Wizard light entities (dimmer + relay)."""
    runtime: RoehnRuntimeData = entry.runtime_data
    coordinator: RoehnCoordinator = runtime.coordinator

    entities: list[RoehnDimmerLight] = []
    known_channels: set[tuple[str, int]] = set()

    for device in coordinator.data.devices:
        for description in _describe_light_channels(device, runtime.resources):
            key = (description.serial_hex, description.channel)
            known_channels.add(key)
            entities.append(RoehnDimmerLight(coordinator, entry, description))

    async_add_entities(entities)

    @callback
    def _add_new_channels() -> None:
        if coordinator.data is None:
            return

        new_entities: list[RoehnDimmerLight] = []
        for device in coordinator.data.devices:
            for description in _describe_light_channels(device, runtime.resources):
                key = (description.serial_hex, description.channel)
                if key in known_channels:
                    continue
                known_channels.add(key)
                new_entities.append(RoehnDimmerLight(coordinator, entry, description))

        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_add_new_channels))


class RoehnDimmerLight(CoordinatorEntity[RoehnCoordinator], LightEntity):
    """One light channel on a Roehn module."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_assumed_state = False
    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}

    def __init__(
        self,
        coordinator: RoehnCoordinator,
        entry: ConfigEntry,
        description: LightChannelDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self.description = description
        self._is_on = False
        self._brightness: int | None = None

        self._attr_name = f"Channel {description.channel}"
        serial_token = description.serial_hex.lower().replace(":", "")
        self._attr_unique_id = f"{entry.entry_id}-light-{serial_token}-{description.channel}"
        self._attr_device_info = module_device_info(
            entry,
            description.serial_hex,
            description.model,
            description.extended_model,
            description.hsnet_id,
            description.driver_info.model_base_name if description.driver_info else None,
        )
        if description.supports_brightness:
            self._attr_color_mode = ColorMode.BRIGHTNESS
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
        self._address = description.hsnet_id if description.hsnet_id > 0 else description.device_id

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_load_listener(self._handle_load_feedback))

    @callback
    def _handle_load_feedback(self, device_address: int, channel: int, level: int) -> None:
        if device_address != self._address or channel != self.description.channel:
            return
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        device = self._device
        return device is not None and device.status == 3

    @property
    def is_on(self) -> bool:
        level = self.coordinator.get_load_level(self._address, self.description.channel)
        if level is not None:
            return level > 0
        return self._is_on

    @property
    def brightness(self) -> int | None:
        if not self.description.supports_brightness:
            return None
        level = self.coordinator.get_load_level(self._address, self.description.channel)
        if level is not None:
            return _level_to_brightness(level)
        return self._brightness

    @property
    def extra_state_attributes(self) -> dict[str, int]:
        device = self._device
        if device is None:
            return {}
        return {
            "channel": self.description.channel,
            "control_address": _resolve_control_address(device),
            "device_id": device.device_id,
            "device_status": device.status,
        }

    async def async_turn_on(self, **kwargs) -> None:
        device = self._device
        if device is None:
            return

        if ATTR_BRIGHTNESS in kwargs:
            level = _brightness_to_level(int(kwargs[ATTR_BRIGHTNESS])) if self.description.supports_brightness else 100
        elif self._brightness is not None:
            level = _brightness_to_level(self._brightness) if self.description.supports_brightness else 100
        else:
            level = 100

        await self.coordinator.async_set_load(
            _resolve_control_address(device),
            self.description.channel,
            level,
        )

        self._is_on = level > 0
        self._brightness = _level_to_brightness(level) if self.description.supports_brightness else None
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        device = self._device
        if device is None:
            return

        await self.coordinator.async_set_load(
            _resolve_control_address(device),
            self.description.channel,
            0,
        )
        self._is_on = False
        self._brightness = 0 if self.description.supports_brightness else None
        self.async_write_ha_state()

    @property
    def _device(self) -> DeviceInfo | None:
        data = self.coordinator.data
        if data is None:
            return None
        for device in data.devices:
            if device.serial_hex == self.description.serial_hex:
                return device
        return None


def _describe_light_channels(
    device: DeviceInfo,
    resources: ResourcesIndex,
) -> list[LightChannelDescription]:
    driver_info = resources.lookup(device.model, device.extended_model, device.dev_model)
    channels: list[LightChannelDescription] = []
    for channel, supports_brightness in _iter_light_channels(driver_info):
        channels.append(
            LightChannelDescription(
                serial_hex=device.serial_hex,
                model=device.model,
                extended_model=device.extended_model,
                driver_info=driver_info,
                channel=channel,
                device_id=device.device_id,
                hsnet_id=device.hsnet_id,
                supports_brightness=supports_brightness,
            )
        )
    return channels


def _iter_light_channels(driver_info: ModuleDriverInfo | None) -> list[tuple[int, bool]]:
    if driver_info is None:
        return []

    channels: list[tuple[int, bool]] = []
    seen: set[int] = set()

    for slot in driver_info.slots:
        if slot.slot_name not in ("dimmer", "relay") or slot.capacity <= 0:
            continue
        start_channel = slot.initial_port if slot.initial_port > 0 else 1
        for index in range(slot.capacity):
            channel = start_channel + index
            if channel in seen:
                continue
            seen.add(channel)
            channels.append((channel, slot.slot_name == "dimmer"))

    return channels


def _resolve_control_address(device: DeviceInfo) -> int:
    if device.hsnet_id > 0:
        return device.hsnet_id
    return device.device_id


def _brightness_to_level(brightness: int) -> int:
    return max(0, min(100, round((brightness / 255) * 100)))


def _level_to_brightness(level: int) -> int:
    return max(0, min(255, round((level / 100) * 255)))
