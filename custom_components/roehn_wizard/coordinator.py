"""Coordinator for Roehn Wizard integration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import time
from typing import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .protocol import BiosInfo, DeviceInfo, ProcessorInfo, RoehnClient

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class RoehnSnapshot:
    processor: ProcessorInfo | None
    bios: BiosInfo | None
    devices: list[DeviceInfo]


class RoehnCoordinator(DataUpdateCoordinator[RoehnSnapshot]):
    """Fetches and caches Roehn processor data."""

    def __init__(
        self,
        hass,
        entry: ConfigEntry,
        client: RoehnClient,
        update_interval_seconds: int,
    ) -> None:
        self.client = client
        self.entry = entry
        self._button_states: dict[tuple[int, int], str] = {}
        self._button_last_changed: dict[tuple[int, int], datetime] = {}
        self._load_states: dict[tuple[int, int], int] = {}
        self._shade_states: dict[tuple[int, int], int] = {}
        self._button_listeners: list[Callable[[int, int, str], None]] = []
        self._load_listeners: list[Callable[[int, int, int], None]] = []
        self._shade_listeners: list[Callable[[int, int, int], None]] = []
        self._event_listener_task: asyncio.Task | None = None
        self._event_listener_running = False
        self._event_writer: asyncio.StreamWriter | None = None
        self._command_lock = asyncio.Lock()
        self._line_waiters: list[tuple[Callable[[str], bool], asyncio.Future[str]]] = []
        self._last_command_sent_at = 0.0
        super().__init__(
            hass,
            _LOGGER,
            name=entry.title,
            update_interval=timedelta(seconds=update_interval_seconds),
            always_update=True,
        )

    async def _async_update_data(self) -> RoehnSnapshot:
        try:
            processor, bios, devices = await asyncio.gather(
                asyncio.to_thread(self.client.query_processor_discovery_info),
                asyncio.to_thread(self.client.query_processor_bios_info),
                asyncio.to_thread(self.client.query_devices),
            )
        except Exception as err:  # pragma: no cover - safety net for network errors
            raise UpdateFailed(str(err)) from err

        if processor is None and bios is None and not devices:
            raise UpdateFailed("No response from Roehn processor")

        return RoehnSnapshot(processor=processor, bios=bios, devices=devices)

    async def async_set_load(self, device_address: int, channel: int, level: int) -> int | None:
        """Set load level through command interface."""
        normalized_level = max(0, min(100, int(level)))
        await self.async_send_command(f"LOAD {int(device_address)} {int(channel)} {normalized_level}")
        return self._load_states.get((int(device_address), int(channel)))

    async def async_query_load(self, device_address: int, channel: int) -> int | None:
        """Read load level through command interface."""
        key = (int(device_address), int(channel))
        matcher = _build_prefix_matcher(f"R:LOAD {key[0]} {key[1]} ")
        await self.async_send_command(
            f"GETLOAD {key[0]} {key[1]}",
            response_matcher=matcher,
            response_timeout=2.0,
        )
        return self._load_states.get(key)

    async def async_shade_up(self, device_address: int, channel: int) -> int | None:
        """Move shade up."""
        key = (int(device_address), int(channel))
        await self.async_send_command(f"SHADE UP {key[0]} {key[1]}")
        return self._shade_states.get(key)

    async def async_shade_down(self, device_address: int, channel: int) -> int | None:
        """Move shade down."""
        key = (int(device_address), int(channel))
        await self.async_send_command(f"SHADE DOWN {key[0]} {key[1]}")
        return self._shade_states.get(key)

    async def async_shade_stop(self, device_address: int, channel: int) -> int | None:
        """Stop shade movement."""
        key = (int(device_address), int(channel))
        await self.async_send_command(f"SHADE STOP {key[0]} {key[1]}")
        return self._shade_states.get(key)

    async def async_shade_set(self, device_address: int, channel: int, level: int) -> int | None:
        """Set shade level 0..100."""
        key = (int(device_address), int(channel))
        normalized_level = max(0, min(100, int(level)))
        matcher = _build_prefix_matcher(f"R:SHADE {key[0]} {key[1]} ")
        await self.async_send_command(
            f"SHADE SET {key[0]} {key[1]} {normalized_level}",
            response_matcher=matcher,
            response_timeout=2.0,
        )
        return self._shade_states.get(key, normalized_level)

    async def async_send_command(
        self,
        command: str,
        *,
        response_matcher: Callable[[str], bool] | None = None,
        response_timeout: float = 1.0,
    ) -> str | None:
        """Send one command over persistent TCP channel."""
        future: asyncio.Future[str] | None = None
        if response_matcher is not None:
            future = self.hass.loop.create_future()
            self._line_waiters.append((response_matcher, future))

        try:
            async with self._command_lock:
                await self._async_wait_for_event_connection()
                if self._event_writer is None:
                    raise RuntimeError("Roehn TCP connection unavailable")

                elapsed = time.monotonic() - self._last_command_sent_at
                if elapsed < 0.1:
                    await asyncio.sleep(0.1 - elapsed)

                self._event_writer.write(command.encode("ascii", errors="ignore") + b"\r\n")
                await self._event_writer.drain()
                self._last_command_sent_at = time.monotonic()
                _LOGGER.debug("Roehn TX: %s", command)

            if future is None:
                return None
            return await asyncio.wait_for(future, timeout=response_timeout)
        finally:
            if future is not None and not future.done():
                future.cancel()
            self._line_waiters = [(m, f) for (m, f) in self._line_waiters if f is not future]

    async def async_start_event_listener(self) -> None:
        """Start async listener for live telnet events."""
        if self._event_listener_task is not None:
            return
        self._event_listener_running = True
        self._event_listener_task = self.hass.loop.create_task(self._async_event_listener_loop())

    async def async_stop_event_listener(self) -> None:
        """Stop async listener for live telnet events."""
        self._event_listener_running = False
        if self._event_listener_task is None:
            return
        self._event_listener_task.cancel()
        try:
            await self._event_listener_task
        except asyncio.CancelledError:
            pass
        finally:
            self._event_writer = None
            self._event_listener_task = None

    def async_add_button_listener(self, listener: Callable[[int, int, str], None]) -> CALLBACK_TYPE:
        """Register callback for keypad button events."""
        self._button_listeners.append(listener)

        def _remove_listener() -> None:
            if listener in self._button_listeners:
                self._button_listeners.remove(listener)

        return _remove_listener

    def async_add_load_listener(self, listener: Callable[[int, int, int], None]) -> CALLBACK_TYPE:
        """Register callback for load feedback lines."""
        self._load_listeners.append(listener)

        def _remove_listener() -> None:
            if listener in self._load_listeners:
                self._load_listeners.remove(listener)

        return _remove_listener

    def async_add_shade_listener(self, listener: Callable[[int, int, int], None]) -> CALLBACK_TYPE:
        """Register callback for shade feedback lines."""
        self._shade_listeners.append(listener)

        def _remove_listener() -> None:
            if listener in self._shade_listeners:
                self._shade_listeners.remove(listener)

        return _remove_listener

    def get_button_state(self, device_address: int, button_id: int) -> str | None:
        """Return last known action for keypad button."""
        return self._button_states.get((device_address, button_id))

    def get_button_last_changed(self, device_address: int, button_id: int) -> datetime | None:
        """Return UTC timestamp for last keypad button event."""
        return self._button_last_changed.get((device_address, button_id))

    def get_load_level(self, device_address: int, channel: int) -> int | None:
        """Return cached load level from R:LOAD feedback."""
        return self._load_states.get((device_address, channel))

    def get_shade_level(self, device_address: int, channel: int) -> int | None:
        """Return cached shade level from R:SHADE feedback."""
        return self._shade_states.get((device_address, channel))

    async def _async_event_listener_loop(self) -> None:
        while self._event_listener_running:
            writer = None
            try:
                reader, writer = await asyncio.open_connection(
                    self.client.host,
                    self.client.command_port,
                )
                self._event_writer = writer
                _LOGGER.debug("Connected Roehn event listener to %s:%s", self.client.host, self.client.command_port)
                async with self._command_lock:
                    for raw in (b"REFRESH\r\n", b"MOD\r\n", b"STA\r\n"):
                        elapsed = time.monotonic() - self._last_command_sent_at
                        if elapsed < 0.1:
                            await asyncio.sleep(0.1 - elapsed)
                        writer.write(raw)
                        await writer.drain()
                        self._last_command_sent_at = time.monotonic()
                last_sta = time.monotonic()

                while self._event_listener_running:
                    try:
                        raw_line = await asyncio.wait_for(reader.readline(), timeout=1.0)
                    except TimeoutError:
                        now = time.monotonic()
                        if now - last_sta >= 10.0:
                            async with self._command_lock:
                                writer.write(b"STA\r\n")
                                await writer.drain()
                                self._last_command_sent_at = time.monotonic()
                            last_sta = now
                        continue
                    if not raw_line:
                        break
                    line = raw_line.decode("ascii", errors="ignore").strip()
                    if not line:
                        continue
                    _LOGGER.debug("Roehn RX: %s", line)
                    self._handle_event_line(line)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # pragma: no cover - network safety net
                _LOGGER.debug("Roehn event listener disconnected: %s", err)
            finally:
                self._event_writer = None
                if writer is not None:
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except Exception:  # pragma: no cover
                        pass
            if self._event_listener_running:
                await asyncio.sleep(2)

    def _handle_event_line(self, line: str) -> None:
        self._notify_line_waiters(line)
        parsed_load = _parse_level_line(line, "R:LOAD")
        if parsed_load is not None:
            addr, channel, level = parsed_load
            self._load_states[(addr, channel)] = level
            for listener in list(self._load_listeners):
                try:
                    listener(addr, channel, level)
                except Exception:  # pragma: no cover
                    _LOGGER.exception("Load listener failure")

        parsed_shade = _parse_level_line(line, "R:SHADE")
        if parsed_shade is not None:
            addr, channel, level = parsed_shade
            self._shade_states[(addr, channel)] = level
            for listener in list(self._shade_listeners):
                try:
                    listener(addr, channel, level)
                except Exception:  # pragma: no cover
                    _LOGGER.exception("Shade listener failure")

        parsed = _parse_button_event_line(line)
        if parsed is None:
            return
        device_address, button_id, action = parsed
        _LOGGER.debug("Roehn button event: action=%s device=%s button=%s", action, device_address, button_id)
        key = (device_address, button_id)
        self._button_states[key] = action
        self._button_last_changed[key] = datetime.now(timezone.utc)
        for listener in list(self._button_listeners):
            try:
                listener(device_address, button_id, action)
            except Exception:  # pragma: no cover - listener safety net
                _LOGGER.exception("Keypad button listener failure")

    async def _async_wait_for_event_connection(self, timeout: float = 4.0) -> None:
        """Wait until event listener has an open writer."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self._event_writer is not None and not self._event_writer.is_closing():
                return
            await asyncio.sleep(0.1)
        raise TimeoutError("Roehn TCP event connection unavailable")

    def _notify_line_waiters(self, line: str) -> None:
        remaining: list[tuple[Callable[[str], bool], asyncio.Future[str]]] = []
        for matcher, future in self._line_waiters:
            if future.done():
                continue
            matched = False
            try:
                matched = matcher(line)
            except Exception:  # pragma: no cover - matcher safety net
                _LOGGER.debug("Roehn matcher exception", exc_info=True)
            if matched:
                future.set_result(line)
            else:
                remaining.append((matcher, future))
        self._line_waiters = remaining


def _parse_button_event_line(line: str) -> tuple[int, int, str] | None:
    """Parse event like R:BTN PRESS <device_id> <button_id>."""
    if not line.startswith("R:BTN "):
        return None
    parts = line.split()
    if len(parts) < 4:
        return None
    action = parts[1].upper()
    if action not in {"PRESS", "RELEASE", "HOLD", "DOUBLE"}:
        return None
    try:
        device_address = int(parts[2])
        button_id = int(parts[3])
    except ValueError:
        return None
    if device_address <= 0 or button_id <= 0:
        return None
    return device_address, button_id, action


def _parse_level_line(line: str, prefix: str) -> tuple[int, int, int] | None:
    """Parse status line like: <prefix> <device> <channel> <level>."""
    if not line.startswith(f"{prefix} "):
        return None
    parts = line.split()
    if len(parts) < 4:
        return None
    try:
        return int(parts[1]), int(parts[2]), int(parts[3])
    except ValueError:
        return None


def _build_prefix_matcher(prefix: str) -> Callable[[str], bool]:
    return lambda line: line.startswith(prefix)
