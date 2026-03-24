"""Sensor platform for Roehn Wizard integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import RoehnRuntimeData, module_device_info, processor_device_info
from .coordinator import RoehnCoordinator
from .protocol import DeviceInfo
from .resources import KeypadDriverInfo, ModuleDriverInfo, ResourcesIndex


@dataclass(slots=True)
class ModuleEntityDescription:
    serial_hex: str
    model: str
    extended_model: str
    driver_info: ModuleDriverInfo | None
    device_id: int
    hsnet_id: int


@dataclass(slots=True)
class KeypadButtonEntityDescription:
    serial_hex: str
    model: str
    extended_model: str
    control_address: int
    button_id: int
    device_id: int
    hsnet_id: int
    keypad_info: KeypadDriverInfo | None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Roehn Wizard sensors from a config entry."""
    runtime: RoehnRuntimeData = entry.runtime_data
    coordinator: RoehnCoordinator = runtime.coordinator

    entities: list[SensorEntity] = [
        ProcessorNameSensor(coordinator, entry),
        ProcessorBiosSensor(coordinator, entry),
        ConnectedModulesSensor(coordinator, entry),
    ]

    known_modules: set[str] = set()
    known_keypad_buttons: set[tuple[str, int]] = set()

    for device in coordinator.data.devices:
        entities.append(
            ModuleStatusSensor(
                coordinator,
                entry,
                _describe_module(device, runtime.resources),
            )
        )
        known_modules.add(device.serial_hex)
        for keypad_desc in _describe_keypad_buttons(device, runtime.resources):
            entities.append(KeypadButtonActionSensor(coordinator, entry, keypad_desc))
            known_keypad_buttons.add((keypad_desc.serial_hex, keypad_desc.button_id))

    async_add_entities(entities)

    @callback
    def _add_new_modules() -> None:
        if coordinator.data is None:
            return

        new_entities: list[SensorEntity] = []
        for device in coordinator.data.devices:
            if device.serial_hex not in known_modules:
                known_modules.add(device.serial_hex)
                new_entities.append(
                    ModuleStatusSensor(
                        coordinator,
                        entry,
                        _describe_module(device, runtime.resources),
                    )
                )

            for keypad_desc in _describe_keypad_buttons(device, runtime.resources):
                key = (keypad_desc.serial_hex, keypad_desc.button_id)
                if key in known_keypad_buttons:
                    continue
                known_keypad_buttons.add(key)
                new_entities.append(KeypadButtonActionSensor(coordinator, entry, keypad_desc))

        if new_entities:
            async_add_entities(new_entities)

    @callback
    def _add_new_buttons_from_events(device_address: int, button_id: int, action: str) -> None:
        if coordinator.data is None:
            return
        device = _find_device_by_control_address(coordinator.data.devices, device_address)
        if device is None:
            return
        if not _is_keypad_device(device, runtime.resources):
            return
        key = (device.serial_hex, button_id)
        if key in known_keypad_buttons:
            return

        desc = _describe_keypad_button(
            device,
            runtime.resources,
            button_id,
        )
        known_keypad_buttons.add(key)
        async_add_entities([KeypadButtonActionSensor(coordinator, entry, desc)])

    entry.async_on_unload(coordinator.async_add_listener(_add_new_modules))
    entry.async_on_unload(coordinator.async_add_button_listener(_add_new_buttons_from_events))


class RoehnSensorBase(CoordinatorEntity[RoehnCoordinator], SensorEntity):
    """Base class for Roehn sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: RoehnCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self.entry = entry


class ProcessorNameSensor(RoehnSensorBase):
    """Processor name/state sensor."""

    _attr_name = "Processor"

    def __init__(self, coordinator: RoehnCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}-processor"
        self._attr_device_info = processor_device_info(entry)

    @property
    def native_value(self) -> str:
        data = self.coordinator.data
        if data is None or data.processor is None:
            return "unavailable"
        return data.processor.name or "processor"

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        data = self.coordinator.data
        if data is None or data.processor is None:
            return {}

        processor = data.processor
        return {
            "source_ip": processor.source_ip,
            "processor_version": processor.version,
            "serial": processor.serial,
            "ip": processor.ip,
            "mask": processor.mask,
            "gateway": processor.gateway,
            "mac": processor.mac,
        }


class ProcessorBiosSensor(RoehnSensorBase):
    """Processor BIOS version sensor."""

    _attr_name = "BIOS"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: RoehnCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}-bios"
        self._attr_device_info = processor_device_info(entry)

    @property
    def native_value(self) -> str:
        data = self.coordinator.data
        if data is None or data.bios is None:
            return "unknown"
        return data.bios.version

    @property
    def extra_state_attributes(self) -> dict[str, int]:
        data = self.coordinator.data
        if data is None or data.bios is None:
            return {}

        bios = data.bios
        return {
            "max_modules": bios.max_modules,
            "max_units": bios.max_units,
            "event_block": bios.event_block,
            "string_var_block": bios.string_var_block,
            "max_scripts": bios.max_scripts,
            "cad_scripts": bios.cad_scripts,
            "max_procedures": bios.max_procedures,
            "cad_procedures": bios.cad_procedures,
            "max_var": bios.max_var,
            "cad_var": bios.cad_var,
            "max_scenes": bios.max_scenes,
            "cad_scenes": bios.cad_scenes,
        }


class ConnectedModulesSensor(RoehnSensorBase):
    """Count of connected modules."""

    _attr_name = "Connected modules"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: RoehnCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}-module-count"
        self._attr_device_info = processor_device_info(entry)

    @property
    def native_value(self) -> int:
        data = self.coordinator.data
        if data is None:
            return 0
        return len(data.devices)


class ModuleStatusSensor(RoehnSensorBase):
    """Per-module diagnostic sensor."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: RoehnCoordinator,
        entry: ConfigEntry,
        module: ModuleEntityDescription,
    ) -> None:
        super().__init__(coordinator, entry)
        self.module = module
        self._attr_name = "Status"
        self._attr_unique_id = f"{entry.entry_id}-module-{module.serial_hex.lower().replace(':', '')}-status"
        self._attr_device_info = module_device_info(
            entry,
            module.serial_hex,
            module.model,
            module.extended_model,
            module.hsnet_id,
            module.driver_info.model_base_name if module.driver_info else None,
        )

    @property
    def native_value(self) -> int | None:
        device = self._device
        if device is None:
            return None
        return device.status

    @property
    def available(self) -> bool:
        return self._device is not None

    @property
    def extra_state_attributes(self) -> dict[str, str | int | list[dict[str, str | int | None | list[str]]]]:
        device = self._device
        if device is None:
            return {}
        attributes: dict[str, str | int | list[dict[str, str | int | None | list[str]]]] = {
            "processor_ip": device.processor_ip,
            "port": device.port,
            "hsnet_id": device.hsnet_id,
            "device_id": device.device_id,
            "dev_model": device.dev_model,
            "firmware": device.fw,
            "model": device.model,
            "extended_model": device.extended_model,
            "serial_hex": device.serial_hex,
            "crc": device.crc,
            "eeprom_address": device.eeprom_address,
            "bitmap": device.bitmap,
        }
        if self.module.driver_info is not None:
            driver = self.module.driver_info
            slot_payload: list[dict[str, str | int | None | list[str]]] = []
            for slot in driver.slots:
                slot_payload.append(
                    {
                        "initial_port": slot.initial_port,
                        "capacity": slot.capacity,
                        "slot_type": slot.slot_type,
                        "slot_name": slot.slot_name,
                        "io": slot.io,
                        "unit_composers": slot.unit_composers,
                    }
                )

            attributes.update(
                {
                    "driver_guid": driver.guid,
                    "driver_model_base_name": driver.model_base_name,
                    "driver_firmware_name": driver.firmware_name,
                    "driver_firmware_extended_name": driver.firmware_extended_name,
                    "driver_type_category": driver.type_category or 0,
                    "driver_max_units": driver.max_units or 0,
                    "driver_max_units_in_scene": driver.max_units_in_scene or 0,
                    "driver_source_file": driver.source_file,
                    "driver_slots": slot_payload,
                }
            )
        return attributes

    @property
    def _device(self) -> DeviceInfo | None:
        data = self.coordinator.data
        if data is None:
            return None
        for device in data.devices:
            if device.serial_hex == self.module.serial_hex:
                return device
        return None


class KeypadButtonActionSensor(RoehnSensorBase):
    """Represents last action for one keypad button."""

    def __init__(
        self,
        coordinator: RoehnCoordinator,
        entry: ConfigEntry,
        keypad_button: KeypadButtonEntityDescription,
    ) -> None:
        super().__init__(coordinator, entry)
        self.keypad_button = keypad_button

        self._attr_name = f"Button {keypad_button.button_id}"
        serial_token = keypad_button.serial_hex.lower().replace(":", "")
        self._attr_unique_id = f"{entry.entry_id}-keypad-{serial_token}-button-{keypad_button.button_id}"
        self._attr_device_info = module_device_info(
            entry,
            keypad_button.serial_hex,
            keypad_button.model,
            keypad_button.extended_model,
            keypad_button.hsnet_id,
            keypad_button.keypad_info.model_base_name if keypad_button.keypad_info else None,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_button_listener(self._handle_button_event))

    @callback
    def _handle_button_event(self, device_address: int, button_id: int, action: str) -> None:
        if device_address != self.keypad_button.control_address:
            return
        if button_id != self.keypad_button.button_id:
            return
        self.async_write_ha_state()

    @property
    def native_value(self) -> str | None:
        action = self.coordinator.get_button_state(
            self.keypad_button.control_address,
            self.keypad_button.button_id,
        )
        if action is None:
            return None
        return action.lower()

    @property
    def available(self) -> bool:
        device = self._device
        return device is not None and device.status == 3

    @property
    def extra_state_attributes(self) -> dict[str, str | int]:
        last_changed = self.coordinator.get_button_last_changed(
            self.keypad_button.control_address,
            self.keypad_button.button_id,
        )
        attrs: dict[str, str | int] = {
            "button_id": self.keypad_button.button_id,
            "control_address": self.keypad_button.control_address,
        }
        if last_changed is not None:
            attrs["last_event_utc"] = last_changed.isoformat()
        return attrs

    @property
    def _device(self) -> DeviceInfo | None:
        data = self.coordinator.data
        if data is None:
            return None
        for device in data.devices:
            if device.serial_hex == self.keypad_button.serial_hex:
                return device
        return None


def _describe_module(device: DeviceInfo, resources: ResourcesIndex) -> ModuleEntityDescription:
    driver_info = resources.lookup(device.model, device.extended_model, device.dev_model)
    return ModuleEntityDescription(
        serial_hex=device.serial_hex,
        model=device.model,
        extended_model=device.extended_model,
        driver_info=driver_info,
        device_id=device.device_id,
        hsnet_id=device.hsnet_id,
    )


def _describe_keypad_buttons(
    device: DeviceInfo,
    resources: ResourcesIndex,
) -> list[KeypadButtonEntityDescription]:
    if not _is_keypad_device(device, resources):
        return []
    button_count = _infer_keypad_button_count(device, resources)
    return [
        _describe_keypad_button(device, resources, button_id)
        for button_id in range(1, button_count + 1)
    ]


def _describe_keypad_button(
    device: DeviceInfo,
    resources: ResourcesIndex,
    button_id: int,
) -> KeypadButtonEntityDescription:
    keypad_info = resources.lookup_keypad(device.model, device.extended_model, device.dev_model)
    return KeypadButtonEntityDescription(
        serial_hex=device.serial_hex,
        model=device.model,
        extended_model=device.extended_model,
        control_address=_resolve_control_address(device),
        button_id=button_id,
        device_id=device.device_id,
        hsnet_id=device.hsnet_id,
        keypad_info=keypad_info,
    )


def _is_keypad_device(device: DeviceInfo, resources: ResourcesIndex) -> bool:
    if resources.lookup_keypad(device.model, device.extended_model, device.dev_model) is not None:
        return True
    driver = resources.lookup(device.model, device.extended_model, device.dev_model)
    if driver is not None:
        for slot in driver.slots:
            if slot.slot_name == "key":
                return True
    model_token = _normalize_model_token(device.model)
    ext_token = _normalize_model_token(device.extended_model)
    if model_token == "STAP":
        return True
    if ext_token in {"RQUA", "RION", "KBPRO", "RQUAL"}:
        return True
    return False


def _infer_keypad_button_count(device: DeviceInfo, resources: ResourcesIndex) -> int:
    keypad_info = resources.lookup_keypad(device.model, device.extended_model, device.dev_model)
    if keypad_info is not None and keypad_info.max_buttons > 0:
        return max(8, keypad_info.max_buttons)

    ext_token = _normalize_model_token(device.extended_model)
    model_token = _normalize_model_token(device.model)

    if ext_token in {"RQUA", "RQUAL", "RION", "KBPRO"}:
        return 8
    if model_token == "QUICKLIGHT":
        return 12
    if model_token == "STAP":
        return 8
    return 8


def _resolve_control_address(device: DeviceInfo) -> int:
    if device.hsnet_id > 0:
        return device.hsnet_id
    return device.device_id


def _find_device_by_control_address(devices: list[DeviceInfo], control_address: int) -> DeviceInfo | None:
    for device in devices:
        if _resolve_control_address(device) == control_address:
            return device
    return None


def _normalize_model_token(value: str | None) -> str:
    if not value:
        return ""
    return "".join(ch for ch in value.upper() if ch.isalnum())
