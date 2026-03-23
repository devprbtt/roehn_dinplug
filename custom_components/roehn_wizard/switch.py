"""Switch platform for Roehn Wizard integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import RoehnRuntimeData, module_device_info
from .coordinator import RoehnCoordinator
from .protocol import DeviceInfo
from .resources import ModuleDriverInfo, ResourcesIndex


@dataclass(slots=True)
class RelayChannelDescription:
    serial_hex: str
    model: str
    extended_model: str
    driver_info: ModuleDriverInfo | None
    channel: int
    device_id: int
    hsnet_id: int


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Roehn Wizard relay entities as switches."""
    runtime: RoehnRuntimeData = entry.runtime_data
    coordinator: RoehnCoordinator = runtime.coordinator

    entities: list[RoehnRelaySwitch] = []
    known_channels: set[tuple[str, int]] = set()

    for device in coordinator.data.devices:
        for description in _describe_relay_channels(device, runtime.resources):
            key = (description.serial_hex, description.channel)
            known_channels.add(key)
            entities.append(RoehnRelaySwitch(coordinator, entry, description))

    async_add_entities(entities)

    @callback
    def _add_new_channels() -> None:
        if coordinator.data is None:
            return

        new_entities: list[RoehnRelaySwitch] = []
        for device in coordinator.data.devices:
            for description in _describe_relay_channels(device, runtime.resources):
                key = (description.serial_hex, description.channel)
                if key in known_channels:
                    continue
                known_channels.add(key)
                new_entities.append(RoehnRelaySwitch(coordinator, entry, description))

        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_add_new_channels))


class RoehnRelaySwitch(CoordinatorEntity[RoehnCoordinator], SwitchEntity):
    """One relay channel on a Roehn module."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_assumed_state = True
    _attr_device_class = SwitchDeviceClass.SWITCH

    def __init__(
        self,
        coordinator: RoehnCoordinator,
        entry: ConfigEntry,
        description: RelayChannelDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self.description = description
        self._is_on = False

        self._attr_name = f"Channel {description.channel}"
        serial_token = description.serial_hex.lower().replace(":", "")
        self._attr_unique_id = f"{entry.entry_id}-switch-{serial_token}-{description.channel}"
        self._attr_device_info = module_device_info(
            entry,
            description.serial_hex,
            description.model,
            description.extended_model,
            description.hsnet_id,
            description.driver_info.model_base_name if description.driver_info else None,
            description.driver_info.image if description.driver_info else None,
        )
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
        await self.coordinator.async_set_load(_resolve_control_address(device), self.description.channel, 100)
        self._is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        device = self._device
        if device is None:
            return
        await self.coordinator.async_set_load(_resolve_control_address(device), self.description.channel, 0)
        self._is_on = False
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


def _describe_relay_channels(device: DeviceInfo, resources: ResourcesIndex) -> list[RelayChannelDescription]:
    driver_info = resources.lookup(device.model, device.extended_model, device.dev_model)
    channels: list[RelayChannelDescription] = []
    for channel in _iter_relay_channels(driver_info):
        channels.append(
            RelayChannelDescription(
                serial_hex=device.serial_hex,
                model=device.model,
                extended_model=device.extended_model,
                driver_info=driver_info,
                channel=channel,
                device_id=device.device_id,
                hsnet_id=device.hsnet_id,
            )
        )
    return channels


def _iter_relay_channels(driver_info: ModuleDriverInfo | None) -> list[int]:
    if driver_info is None:
        return []
    channels: list[int] = []
    seen: set[int] = set()
    for slot in driver_info.slots:
        if slot.slot_name != "relay" or slot.capacity <= 0:
            continue
        start_channel = slot.initial_port if slot.initial_port > 0 else 1
        for index in range(slot.capacity):
            channel = start_channel + index
            if channel in seen:
                continue
            seen.add(channel)
            channels.append(channel)
    return channels


def _resolve_control_address(device: DeviceInfo) -> int:
    if device.hsnet_id > 0:
        return device.hsnet_id
    return device.device_id
