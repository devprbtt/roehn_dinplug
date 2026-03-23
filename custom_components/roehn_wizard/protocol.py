"""Protocol helpers for Roehn Wizard UDP."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import socket
import time

HEADER = b"HSN_S-UDP"
_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ProcessorInfo:
    source_ip: str
    name: str
    version: str = ""
    serial: str = ""
    ip: str = ""
    mask: str = ""
    gateway: str = ""
    mac: str = ""


@dataclass(slots=True)
class DeviceInfo:
    processor_ip: str
    port: int
    hsnet_id: int
    device_id: int
    dev_model: int
    fw: str
    model: str
    extended_model: str
    serial_hex: str
    status: int
    crc: int
    eeprom_address: int
    bitmap: int


@dataclass(slots=True)
class BiosInfo:
    version: str
    major_version: int
    minor_version: int
    patch_version: int
    max_modules: int = 0
    max_units: int = 0
    event_block: int = 0
    string_var_block: int = 0
    max_scripts: int = 0
    cad_scripts: int = 0
    max_procedures: int = 0
    cad_procedures: int = 0
    max_var: int = 0
    cad_var: int = 0
    max_scenes: int = 0
    cad_scenes: int = 0


def _c_string(data: bytes, start: int, max_len: int | None = None) -> str:
    if start < 0 or start >= len(data):
        return ""
    end = len(data) if max_len is None else min(len(data), start + max_len)
    chunk = data[start:end]
    nul = chunk.find(b"\x00")
    if nul >= 0:
        chunk = chunk[:nul]
    return chunk.decode("ascii", errors="ignore").strip()


def build_discover_packet() -> bytes:
    return HEADER + bytes((3, 0, 0, 0))


def build_get_connected_devices_packet(read_index: int) -> bytes:
    return HEADER + bytes((100, 1, read_index & 0xFF, (read_index >> 8) & 0xFF, 0))


def build_get_bios_packet() -> bytes:
    return HEADER + bytes((4, 9, 0))


def parse_processor_response(data: bytes, source_ip: str) -> ProcessorInfo | None:
    if len(data) < 15:
        return None
    if data[:9] != HEADER:
        return None
    if data[9] != 3 or data[10] != 0:
        return None

    info = ProcessorInfo(source_ip=source_ip, name=_c_string(data, 12))
    if len(data) >= 82:
        info.version = f"{data[32]}.{data[33]}.{data[34]}.{data[35]}"
        info.serial = _c_string(data, 42, 16)
        info.ip = ".".join(str(x) for x in data[62:66])
        info.mask = ".".join(str(x) for x in data[66:70])
        info.gateway = ".".join(str(x) for x in data[70:74])
        info.mac = ":".join(f"{x:02X}" for x in data[74:80])
    return info


def parse_bios_response(data: bytes) -> BiosInfo | None:
    if len(data) < 15:
        return None
    if data[:9] != HEADER:
        return None
    if data[9] != 4 or data[10] != 9:
        return None

    major = int(data[12])
    minor = int(data[13])
    patch = int(data[14])
    info = BiosInfo(
        version=f"{major}.{minor}.{patch}",
        major_version=major,
        minor_version=minor,
        patch_version=patch,
    )
    if len(data) >= 16:
        info.max_modules = int(data[15])
    if len(data) >= 18:
        info.max_units = int(data[16]) | (int(data[17]) << 8)
    if len(data) >= 22:
        info.event_block = int(data[20]) | (int(data[21]) << 8)
    if len(data) >= 24:
        info.string_var_block = int(data[22]) | (int(data[23]) << 8)
    if len(data) >= 28:
        info.max_scripts = int(data[26]) | (int(data[27]) << 8)
    if len(data) >= 30:
        info.cad_scripts = int(data[28]) | (int(data[29]) << 8)
    if len(data) >= 32:
        info.max_procedures = int(data[30]) | (int(data[31]) << 8)
    if len(data) >= 34:
        info.cad_procedures = int(data[32]) | (int(data[33]) << 8)
    if len(data) >= 36:
        info.max_var = int(data[34]) | (int(data[35]) << 8)
    if len(data) >= 38:
        info.cad_var = int(data[36]) | (int(data[37]) << 8)
        if info.cad_var > 65000:
            info.cad_var = 0
    if len(data) >= 40:
        info.max_scenes = int(data[38]) | (int(data[39]) << 8)
    if len(data) >= 42:
        info.cad_scenes = int(data[40]) | (int(data[41]) << 8)
    return info


def parse_devices_response(data: bytes, source_ip: str) -> tuple[list[DeviceInfo], int, int] | None:
    if len(data) <= 22:
        return None
    if data[:9] != HEADER:
        return None
    if data[9] != 100 or data[10] != 1:
        return None

    header_len = data[12]
    register_len = data[13]
    registers_qty = data[14]
    read_index = data[16] | (data[17] << 8)

    devices: list[DeviceInfo] = []
    base = 9 + 3 + header_len
    for i in range(registers_qty):
        pos = base + i * register_len
        if pos + 40 > len(data):
            break
        serial = data[pos + 28 : pos + 34]
        devices.append(
            DeviceInfo(
                processor_ip=source_ip,
                status=data[pos],
                port=data[pos + 1],
                hsnet_id=data[pos + 3] | (data[pos + 4] << 8),
                device_id=data[pos + 5] | (data[pos + 6] << 8),
                dev_model=data[pos + 7],
                fw=f"{data[pos + 8]}.{data[pos + 9]}.{data[pos + 10]}",
                model=data[pos + 11 : pos + 18].decode("ascii", errors="ignore").replace("\x00", "").strip(),
                extended_model=data[pos + 18 : pos + 28]
                .decode("ascii", errors="ignore")
                .replace("\x00", "")
                .strip(),
                serial_hex=":".join(f"{b:02X}" for b in serial),
                crc=(data[pos + 34] << 8) | data[pos + 35],
                eeprom_address=data[pos + 36] | (data[pos + 37] << 8),
                bitmap=data[pos + 38] | (data[pos + 39] << 8),
            )
        )

    return devices, registers_qty, read_index


class RoehnClient:
    """Synchronous UDP client for Roehn Wizard protocol."""

    def __init__(
        self,
        host: str,
        port: int,
        timeout: float = 1.0,
        command_port: int = 23,
        command_timeout: float | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.command_port = command_port
        self.command_timeout = timeout if command_timeout is None else command_timeout
        self._accepted_source_ips = self._resolve_host_ips(host)

    @staticmethod
    def _resolve_host_ips(host: str) -> set[str]:
        """Resolve configured host to accepted source IPs."""
        ips: set[str] = set()
        if not host:
            return ips
        ips.add(host)
        try:
            infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_DGRAM)
        except OSError:
            return ips
        for info in infos:
            sockaddr = info[4]
            if isinstance(sockaddr, tuple) and sockaddr:
                ips.add(str(sockaddr[0]))
        return ips

    def _is_expected_source(self, src_ip: str) -> bool:
        """Return True if response source IP matches configured host/IP."""
        if not self._accepted_source_ips:
            return True
        return src_ip in self._accepted_source_ips

    def query_processor_discovery_info(self, probes: int = 2) -> ProcessorInfo | None:
        packet = build_discover_packet()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(max(0.05, self.timeout))
            for _ in range(max(1, probes)):
                sock.sendto(packet, (self.host, self.port))
                deadline = time.time() + self.timeout
                while time.time() < deadline:
                    try:
                        data, (src_ip, _) = sock.recvfrom(4096)
                    except socket.timeout:
                        break
                    if not self._is_expected_source(src_ip):
                        continue
                    parsed = parse_processor_response(data, src_ip)
                    if parsed is not None:
                        return parsed
        finally:
            sock.close()
        return None

    def query_processor_bios_info(self, probes: int = 2) -> BiosInfo | None:
        packet = build_get_bios_packet()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(max(0.05, self.timeout))
            for _ in range(max(1, probes)):
                sock.sendto(packet, (self.host, self.port))
                deadline = time.time() + self.timeout
                while time.time() < deadline:
                    try:
                        data, (src_ip, _) = sock.recvfrom(4096)
                    except socket.timeout:
                        break
                    if not self._is_expected_source(src_ip):
                        continue
                    parsed = parse_bios_response(data)
                    if parsed is not None:
                        return parsed
        finally:
            sock.close()
        return None

    def query_devices(self, max_pages: int = 32) -> list[DeviceInfo]:
        all_devices: list[DeviceInfo] = []
        read_index = 0

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(max(0.05, self.timeout))
            for _ in range(max_pages):
                sock.sendto(build_get_connected_devices_packet(read_index), (self.host, self.port))
                deadline = time.time() + self.timeout
                parsed: tuple[list[DeviceInfo], int, int] | None = None
                while time.time() < deadline:
                    try:
                        data, (src_ip, _) = sock.recvfrom(8192)
                    except socket.timeout:
                        break
                    if not self._is_expected_source(src_ip):
                        continue
                    parsed = parse_devices_response(data, src_ip)
                    if parsed is not None:
                        break

                if parsed is None:
                    break

                devices, qty, current_index = parsed
                all_devices.extend(devices)
                if qty < 24:
                    break
                read_index = current_index + qty
        finally:
            sock.close()

        return all_devices

    def set_load(self, device_address: int, channel: int, level: int) -> int | None:
        """Set module load level and return reported level when available."""
        normalized_level = max(0, min(100, int(level)))
        command = f"LOAD {int(device_address)} {int(channel)} {normalized_level}"
        lines = self._send_text_command(command)

        for line in lines:
            parsed = self._parse_load_line(line)
            if parsed is None:
                continue
            address, parsed_channel, parsed_level = parsed
            if address == int(device_address) and parsed_channel == int(channel):
                return parsed_level

        return None

    def query_load(self, device_address: int, channel: int) -> int | None:
        """Read module load level using GETLOAD command."""
        command = f"GETLOAD {int(device_address)} {int(channel)}"
        lines = self._send_text_command(command)

        for line in lines:
            parsed = self._parse_load_line(line)
            if parsed is None:
                continue
            address, parsed_channel, parsed_level = parsed
            if address == int(device_address) and parsed_channel == int(channel):
                return parsed_level

        return None

    def shade_up(self, device_address: int, channel: int) -> int | None:
        """Move shade up and return reported level when available."""
        lines = self._send_text_command(f"SHADE UP {int(device_address)} {int(channel)}")
        return self._extract_shade_level(lines, device_address, channel)

    def shade_down(self, device_address: int, channel: int) -> int | None:
        """Move shade down and return reported level when available."""
        lines = self._send_text_command(f"SHADE DOWN {int(device_address)} {int(channel)}")
        return self._extract_shade_level(lines, device_address, channel)

    def shade_stop(self, device_address: int, channel: int) -> int | None:
        """Stop shade movement and return reported level when available."""
        lines = self._send_text_command(f"SHADE STOP {int(device_address)} {int(channel)}")
        return self._extract_shade_level(lines, device_address, channel)

    def shade_set(self, device_address: int, channel: int, level: int) -> int | None:
        """Set shade percentage where 0=down and 100=up."""
        normalized_level = max(0, min(100, int(level)))
        lines = self._send_text_command(f"SHADE SET {int(device_address)} {int(channel)} {normalized_level}")
        parsed = self._extract_shade_level(lines, device_address, channel)
        if parsed is not None:
            return parsed
        return normalized_level

    def _send_text_command(
        self,
        command: str,
        *,
        response_timeout: float = 0.35,
        max_lines: int = 20,
    ) -> list[str]:
        """Send a telnet command and return received text lines."""
        try:
            sock = socket.create_connection((self.host, self.command_port), timeout=max(0.05, self.command_timeout))
        except OSError as err:
            _LOGGER.warning(
                "Roehn telnet connect failed to %s:%s for command '%s': %s",
                self.host,
                self.command_port,
                command,
                err,
            )
            return []
        lines: list[str] = []
        try:
            sock.settimeout(max(0.05, response_timeout))
            sock.sendall(command.encode("ascii", errors="ignore") + b"\r\n")

            end = time.time() + response_timeout
            buffer = b""
            while time.time() < end and len(lines) < max_lines:
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    break
                except OSError as err:
                    _LOGGER.debug("Roehn telnet recv error for '%s': %s", command, err)
                    break
                if not chunk:
                    break
                buffer += chunk

                while b"\n" in buffer:
                    raw_line, buffer = buffer.split(b"\n", 1)
                    line = raw_line.decode("ascii", errors="ignore").strip()
                    if line:
                        lines.append(line)
        finally:
            sock.close()

        _LOGGER.debug("Roehn command '%s' -> %s", command, lines[:5])
        return lines

    @staticmethod
    def _parse_load_line(line: str) -> tuple[int, int, int] | None:
        """Parse response like: R:LOAD <device> <channel> <level>."""
        if not line.startswith("R:LOAD "):
            return None

        parts = line.split()
        if len(parts) < 4:
            return None

        try:
            return int(parts[1]), int(parts[2]), int(parts[3])
        except ValueError:
            return None

    @staticmethod
    def _parse_shade_line(line: str) -> tuple[int, int, int] | None:
        """Parse response like: R:SHADE <device> <channel> <level>."""
        if not line.startswith("R:SHADE "):
            return None

        parts = line.split()
        if len(parts) < 4:
            return None

        try:
            return int(parts[1]), int(parts[2]), int(parts[3])
        except ValueError:
            return None

    def _extract_shade_level(self, lines: list[str], device_address: int, channel: int) -> int | None:
        for line in lines:
            parsed = self._parse_shade_line(line)
            if parsed is None:
                continue
            address, parsed_channel, parsed_level = parsed
            if address == int(device_address) and parsed_channel == int(channel):
                return parsed_level
        return None
