"""Resource metadata loader for Roehn Wizard integration."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
import os
from pathlib import Path
import re
from typing import Any

INTEGRATION_DIR = Path(__file__).resolve().parent
BUNDLED_MODULE_DRIVERS_PATH = INTEGRATION_DIR / "data" / "module_drivers.json"
BUNDLED_KEYPAD_DRIVERS_PATH = INTEGRATION_DIR / "data" / "keypad_drivers.json"

DEFAULT_RESOURCE_BASES: tuple[str, ...] = (
    r"I:\Roehn Wizard\Resources",
    r"G:\Roehn Wizard\Resources",
    r"C:\Program Files\Roehn Wizard\Resources",
    "/config/roehn_resources",
    "/data/roehn_resources",
)

RESOURCE_ENV_VAR = "ROEHN_WIZARD_RESOURCES_PATH"

SLOT_TYPE_NAMES: dict[int, str] = {
    1: "relay",
    2: "dimmer",
    3: "key",
    6: "sensor",
    7: "shade",
}


@dataclass(slots=True)
class SlotInfo:
    """A module slot descriptor extracted from Roehn drivers."""

    initial_port: int
    capacity: int
    slot_type: int
    slot_name: str
    io: int | None
    unit_composers: list[str]


@dataclass(slots=True)
class ModuleDriverInfo:
    """Module driver metadata from Roehn JSON resources."""

    source_file: str
    guid: str
    model_base_name: str
    firmware_name: str
    firmware_extended_name: str
    dev_model: int | None
    type_category: int | None
    max_units: int | None
    max_units_in_scene: int | None
    image: str
    slots: list[SlotInfo]

    @property
    def token_candidates(self) -> tuple[str, ...]:
        """Return normalized token candidates for matching."""
        return tuple(
            t
            for t in (
                _normalize_model_token(self.model_base_name),
                _normalize_model_token(self.firmware_name),
                _normalize_model_token(self.firmware_extended_name),
                _normalize_model_token(Path(self.source_file).stem),
            )
            if t
        )


@dataclass(slots=True)
class KeypadDriverInfo:
    """Keypad driver metadata from Roehn JSON resources."""

    source_file: str
    model_base_name: str
    firmware_name: str
    firmware_extended_name: str
    model_id: int | None
    max_buttons: int

    @property
    def token_candidates(self) -> tuple[str, ...]:
        return tuple(
            t
            for t in (
                _normalize_model_token(self.model_base_name),
                _normalize_model_token(self.firmware_name),
                _normalize_model_token(self.firmware_extended_name),
                _normalize_model_token(Path(self.source_file).stem),
            )
            if t
        )


@dataclass(slots=True)
class ResourcesIndex:
    """In-memory index for resource metadata lookup."""

    root_path: str | None
    modules: list[ModuleDriverInfo]
    modules_by_token: dict[str, ModuleDriverInfo]
    modules_by_dev_model: dict[int, ModuleDriverInfo]
    keypads: list[KeypadDriverInfo]
    keypads_by_token: dict[str, KeypadDriverInfo]
    keypads_by_model_id: dict[int, KeypadDriverInfo]

    def lookup(self, model: str, extended_model: str, dev_model: int) -> ModuleDriverInfo | None:
        """Find best module metadata candidate."""
        for token in (
            _normalize_model_token(extended_model),
            _normalize_model_token(model),
        ):
            if token and token in self.modules_by_token:
                return self.modules_by_token[token]
        return self.modules_by_dev_model.get(dev_model)

    def lookup_keypad(self, model: str, extended_model: str, dev_model: int) -> KeypadDriverInfo | None:
        """Find best keypad metadata candidate."""
        for token in (
            _normalize_model_token(extended_model),
            _normalize_model_token(model),
        ):
            if token and token in self.keypads_by_token:
                return self.keypads_by_token[token]
        return self.keypads_by_model_id.get(dev_model)


def _normalize_model_token(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _resolve_resources_root(resource_path: str | None) -> Path | None:
    candidates: list[str] = []
    if resource_path:
        candidates.append(resource_path)
    env_path = os.getenv(RESOURCE_ENV_VAR)
    if env_path:
        candidates.append(env_path)
    candidates.extend(DEFAULT_RESOURCE_BASES)

    for candidate in candidates:
        path = Path(candidate).expanduser()
        if not path.exists():
            continue
        if path.name.lower() == "resources" and (path / "Drivers" / "Modules").exists():
            return path
        if (path / "Resources" / "Drivers" / "Modules").exists():
            return path / "Resources"
    return None


def _read_json_file(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def _iter_module_payloads_from_modules_dir(modules_dir: Path) -> list[tuple[dict[str, Any], str]]:
    payloads: list[tuple[dict[str, Any], str]] = []
    for file_path in sorted(modules_dir.glob("*.json")):
        parsed = _read_json_file(file_path)
        if not isinstance(parsed, dict):
            continue
        payloads.append((parsed, str(file_path)))
    return payloads


def _iter_module_payloads_from_bundled_file(path: Path) -> list[tuple[dict[str, Any], str]]:
    parsed = _read_json_file(path)
    if not isinstance(parsed, list):
        return []
    payloads: list[tuple[dict[str, Any], str]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        source = str(path)
        source_file = item.get("_source_file")
        if isinstance(source_file, str) and source_file:
            source = f"{path}:{source_file}"
        payloads.append((item, source))
    return payloads


def _iter_keypad_payloads_from_dir(keypads_dir: Path) -> list[tuple[dict[str, Any], str]]:
    payloads: list[tuple[dict[str, Any], str]] = []
    for file_path in sorted(keypads_dir.glob("*.json")):
        parsed = _read_json_file(file_path)
        if not isinstance(parsed, dict):
            continue
        payloads.append((parsed, str(file_path)))
    return payloads


@lru_cache(maxsize=8)
def load_resources_index(resource_path: str | None = None) -> ResourcesIndex:
    """Load and index Roehn resource metadata."""
    module_payloads: list[tuple[dict[str, Any], str]] = []
    keypad_payloads: list[tuple[dict[str, Any], str]] = []

    # Always load bundled metadata first so integration works standalone.
    module_payloads.extend(_iter_module_payloads_from_bundled_file(BUNDLED_MODULE_DRIVERS_PATH))
    keypad_payloads.extend(_iter_module_payloads_from_bundled_file(BUNDLED_KEYPAD_DRIVERS_PATH))

    root = _resolve_resources_root(resource_path)
    if root is not None:
        # External resources (if present) can add/override bundled metadata.
        modules_dir = root / "Drivers" / "Modules"
        module_payloads.extend(_iter_module_payloads_from_modules_dir(modules_dir))
        keypads_dir = root / "Drivers" / "Keypads"
        keypad_payloads.extend(_iter_keypad_payloads_from_dir(keypads_dir))

    if not module_payloads and not keypad_payloads:
        return ResourcesIndex(
            root_path=None,
            modules=[],
            modules_by_token={},
            modules_by_dev_model={},
            keypads=[],
            keypads_by_token={},
            keypads_by_model_id={},
        )

    module_infos: list[ModuleDriverInfo] = []
    keypad_infos: list[KeypadDriverInfo] = []

    for payload, source_file in module_payloads:
        slots: list[SlotInfo] = []
        for raw_slot in payload.get("Slots", []):
            slot_type = _as_int(raw_slot.get("SlotType")) or 0
            unit_composer_names: list[str] = []
            for composer in raw_slot.get("UnitComposers", []):
                if isinstance(composer, dict):
                    name = str(composer.get("Name", "")).strip()
                    if name:
                        unit_composer_names.append(name)

            slots.append(
                SlotInfo(
                    initial_port=_as_int(raw_slot.get("InitialPort")) or 0,
                    capacity=_as_int(raw_slot.get("SlotCapacity")) or 0,
                    slot_type=slot_type,
                    slot_name=SLOT_TYPE_NAMES.get(slot_type, "unknown"),
                    io=_as_int(raw_slot.get("IO")),
                    unit_composers=unit_composer_names,
                )
            )

        image_value = ""
        image = payload.get("Image")
        if isinstance(image, dict):
            image_value = str(image.get("value", "")).strip()

        info = ModuleDriverInfo(
            source_file=source_file,
            guid=str(payload.get("Guid", "")),
            model_base_name=str(payload.get("modelBaseName", "")),
            firmware_name=str(payload.get("FirmwareName", "")),
            firmware_extended_name=str(payload.get("FirmwareExtendedName", "")),
            dev_model=_as_int(payload.get("Type")),
            type_category=_as_int(payload.get("TypeCategory")),
            max_units=_as_int(payload.get("MaxUnits")),
            max_units_in_scene=_as_int(payload.get("MaxUnitsInScene")),
            image=image_value,
            slots=slots,
        )
        module_infos.append(info)

    for payload, source_file in keypad_payloads:
        layouts = payload.get("layouts") or payload.get("Layouts") or []
        max_buttons = _as_int(payload.get("MaxButtons")) or 0
        if isinstance(layouts, list):
            for layout in layouts:
                if not isinstance(layout, dict):
                    continue
                button_count = _as_int(layout.get("buttonCount")) or 0
                if button_count > max_buttons:
                    max_buttons = button_count

        info = KeypadDriverInfo(
            source_file=source_file,
            model_base_name=str(payload.get("modelBaseName", "")),
            firmware_name=str(payload.get("FirmwareName", "")),
            firmware_extended_name=str(payload.get("FirmwareExtendedName", "")),
            model_id=_as_int(payload.get("ModelID")),
            max_buttons=max_buttons,
        )
        keypad_infos.append(info)

    by_token: dict[str, ModuleDriverInfo] = {}
    by_dev_model: dict[int, ModuleDriverInfo] = {}
    keypads_by_token: dict[str, KeypadDriverInfo] = {}
    keypads_by_model_id: dict[int, KeypadDriverInfo] = {}

    for info in module_infos:
        for token in info.token_candidates:
            by_token[token] = info
        if info.dev_model is not None:
            by_dev_model[info.dev_model] = info

    for info in keypad_infos:
        for token in info.token_candidates:
            existing = keypads_by_token.get(token)
            if existing is None or info.max_buttons >= existing.max_buttons:
                keypads_by_token[token] = info
        if info.model_id is not None:
            existing_model = keypads_by_model_id.get(info.model_id)
            if existing_model is None or info.max_buttons >= existing_model.max_buttons:
                keypads_by_model_id[info.model_id] = info

    return ResourcesIndex(
        root_path=str(root) if root is not None else str(BUNDLED_MODULE_DRIVERS_PATH),
        modules=module_infos,
        modules_by_token=by_token,
        modules_by_dev_model=by_dev_model,
        keypads=keypad_infos,
        keypads_by_token=keypads_by_token,
        keypads_by_model_id=keypads_by_model_id,
    )
