import math
import logging
import os
import re
import shutil
import socket
import struct
import subprocess
import time
from dataclasses import dataclass
from io import BytesIO
from urllib.parse import parse_qs, urlparse

import serial
from PIL import Image, ImageOps, ImageSequence
from serial.tools.list_ports import comports as list_comports

from nimbot_cups.packet import NimbotPacket

MAX_WIDTH_BY_MODEL = {
    "b1": 384,
    "b18": 384,
    "b21": 384,
    "d11": 96,
    "d110": 96,
}

LABEL_TYPE_BY_KIND = {
    "gap": 1,
    "blackmark": 2,
    "continuous": 3,
}

PLACEHOLDER_BLUETOOTH_ADDRESSES = {
    "AA:BB:CC:DD:EE:FF",
    "00:00:00:00:00:00",
}


class NimbotError(Exception):
    """Base exception for the driver."""


class InvalidDeviceURIError(NimbotError):
    """Raised when the queue device URI is invalid."""


class BackendConnectionError(NimbotError):
    """Raised when transport to the printer cannot be opened."""


class RequestCode:
    GET_INFO = 0x40
    HEARTBEAT = 0xDC
    SET_LABEL_TYPE = 0x23
    SET_LABEL_DENSITY = 0x21
    PRINT_STATUS = 0xA3
    START_PRINT = 0x01
    END_PRINT = 0xF3
    START_PAGE_PRINT = 0x03
    END_PAGE_PRINT = 0xE3
    SET_DIMENSION = 0x13


@dataclass
class DeviceURI:
    connection: str
    model: str
    port: str | None = None
    address: str | None = None
    label_kind: str = "gap"


class BaseTransport:
    def read(self, length: int) -> bytes:
        raise NotImplementedError

    def write(self, data: bytes) -> int:
        raise NotImplementedError

    def close(self) -> None:
        return None


class BluetoothTransport(BaseTransport):
    def __init__(self, address: str, channel: int):
        try:
            logging.debug(
                "Attempting Bluetooth RFCOMM connection to %s on channel %s",
                address,
                channel,
            )
            self._sock = socket.socket(
                socket.AF_BLUETOOTH,
                socket.SOCK_STREAM,
                socket.BTPROTO_RFCOMM,
            )
            self._sock.settimeout(8.0)
            self._sock.connect((address, channel))
            self._sock.settimeout(0.5)
            logging.debug(
                "Connected to Bluetooth RFCOMM %s channel %s", address, channel
            )
        except OSError as exc:
            raise BackendConnectionError(
                f"cannot connect to bluetooth printer at {address} on RFCOMM channel {channel}: {exc}. "
                "Check that the printer is powered on, paired, and exposes RFCOMM Serial Port."
            ) from exc

    def read(self, length: int) -> bytes:
        try:
            return self._sock.recv(length)
        except TimeoutError:
            return b""

    def write(self, data: bytes) -> int:
        self._sock.sendall(data)
        return len(data)

    def close(self) -> None:
        self._sock.close()


class SerialTransport(BaseTransport):
    def __init__(self, port: str | None = None):
        selected_port = port or autodetect_serial_port()
        try:
            logging.debug("Opening serial transport on %s", selected_port)
            self._serial = serial.Serial(
                port=selected_port,
                baudrate=115200,
                timeout=0.5,
            )
            logging.debug("Opened serial transport on %s", selected_port)
        except serial.SerialException as exc:
            raise BackendConnectionError(
                f"cannot open serial printer at {selected_port}: {exc}"
            ) from exc

    def read(self, length: int) -> bytes:
        return self._serial.read(length)

    def write(self, data: bytes) -> int:
        return self._serial.write(data)

    def close(self) -> None:
        self._serial.close()


class RfcommCliTransport(BaseTransport):
    def __init__(self, address: str, channel: int, device_index: int = 9):
        if shutil.which("rfcomm") is None:
            raise BackendConnectionError("rfcomm command is not available")

        self._device_index = device_index
        self._device_path = f"/dev/rfcomm{device_index}"
        self._proc = None
        self._serial = None

        subprocess.run(
            ["rfcomm", "release", str(device_index)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        try:
            logging.debug(
                "Starting rfcomm helper for %s on channel %s as %s",
                address,
                channel,
                self._device_path,
            )
            self._proc = subprocess.Popen(
                ["rfcomm", "connect", str(device_index), address, str(channel)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            deadline = time.time() + 8.0
            while time.time() < deadline:
                if os.path.exists(self._device_path):
                    try:
                        self._serial = serial.Serial(
                            port=self._device_path,
                            baudrate=115200,
                            timeout=0.5,
                        )
                        logging.debug("Opened rfcomm helper serial device %s", self._device_path)
                        return
                    except serial.SerialException:
                        time.sleep(0.2)
                        continue
                if self._proc.poll() is not None:
                    break
                time.sleep(0.2)

            raise BackendConnectionError(
                f"rfcomm helper could not open {self._device_path} for {address} on channel {channel}"
            )
        except Exception:
            self.close()
            raise

    def read(self, length: int) -> bytes:
        if self._serial is None:
            return b""
        return self._serial.read(length)

    def write(self, data: bytes) -> int:
        if self._serial is None:
            raise BackendConnectionError("rfcomm serial device is not open")
        return self._serial.write(data)

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except Exception:
                try:
                    self._proc.kill()
                    self._proc.wait(timeout=2)
                except Exception:
                    pass
            self._proc = None
        subprocess.run(
            ["rfcomm", "release", str(self._device_index)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def autodetect_serial_port() -> str:
    matches = []
    for port in list_comports():
        attrs = " ".join(
            filter(
                None,
                [
                    str(port.device),
                    str(port.description),
                    str(port.manufacturer),
                    str(port.product),
                    str(port.hwid),
                ],
            )
        ).lower()
        if "niimbot" in attrs or "nimbot" in attrs:
            matches.append(port.device)
    if len(matches) == 1:
        return matches[0]
    if not matches:
        all_ports = [port.device for port in list_comports()]
        if len(all_ports) == 1:
            return all_ports[0]
        raise BackendConnectionError("no Nimbot serial device detected")
    raise BackendConnectionError(
        f"multiple candidate serial devices found: {', '.join(matches)}"
    )


def validate_bluetooth_address(address: str) -> str:
    normalized = address.upper()
    if normalized in PLACEHOLDER_BLUETOOTH_ADDRESSES:
        raise InvalidDeviceURIError(
            "bluetooth device URI still contains a placeholder MAC address; "
            "replace it with the real printer MAC from 'bluetoothctl devices'"
        )
    if not re.fullmatch(r"([0-9A-F]{2}:){5}[0-9A-F]{2}", normalized):
        raise InvalidDeviceURIError(
            f"invalid bluetooth MAC address in device URI: {address}"
        )
    return normalized


def bluetooth_address_candidates(address: str) -> list[str]:
    normalized = validate_bluetooth_address(address)
    octets = normalized.split(":")
    rotated = ":".join([octets[2], octets[0], octets[1], octets[3], octets[4], octets[5]])
    if rotated == normalized:
        return [normalized]
    return [normalized, rotated]


def bluetooth_channel_candidates(address: str) -> list[int]:
    channels: list[int] = []
    try:
        result = subprocess.run(
            ["sdptool", "browse", address],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        result = None

    if result and result.returncode == 0:
        current_service = None
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("Service Name:"):
                current_service = stripped.split(":", 1)[1].strip().lower()
            elif stripped.startswith("Channel:"):
                try:
                    channel = int(stripped.split(":", 1)[1].strip())
                except ValueError:
                    continue
                if current_service == "serial port" and channel not in channels:
                    channels.append(channel)
        if channels:
            return channels
    return [1, 2, 3, 4, 5, 6, 7, 8]


def parse_device_uri(uri: str) -> DeviceURI:
    parsed = urlparse(uri)
    if parsed.scheme != "nimbot":
        raise InvalidDeviceURIError(f"unsupported device URI scheme: {parsed.scheme}")
    params = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
    model = params.get("model", "b1").lower()
    label_kind = params.get("label_kind", "gap").lower()
    connection = parsed.netloc.lower()
    if connection == "usb":
        return DeviceURI(
            connection="usb",
            model=model,
            port=params.get("port"),
            label_kind=label_kind,
        )
    if connection == "rfcomm":
        port = params.get("port")
        if not port:
            raise InvalidDeviceURIError(
                "rfcomm URI requires port query parameter, e.g. /dev/rfcomm0"
            )
        return DeviceURI(
            connection="rfcomm",
            model=model,
            port=port,
            label_kind=label_kind,
        )
    if connection == "bluetooth":
        address = params.get("address")
        if not address:
            raise InvalidDeviceURIError(
                "bluetooth URI requires address query parameter"
            )
        return DeviceURI(
            connection="bluetooth",
            model=model,
            address=validate_bluetooth_address(address),
            label_kind=label_kind,
        )
    raise InvalidDeviceURIError(f"unsupported connection type: {connection}")


def transport_for_uri(device_uri: DeviceURI) -> BaseTransport:
    if device_uri.connection in {"usb", "rfcomm"}:
        return SerialTransport(port=device_uri.port)
    if device_uri.connection == "bluetooth":
        errors = []
        for address in bluetooth_address_candidates(device_uri.address or ""):
            for channel in bluetooth_channel_candidates(address):
                try:
                    return BluetoothTransport(address, channel)
                except BackendConnectionError as exc:
                    errors.append(str(exc))
                try:
                    return RfcommCliTransport(address, channel)
                except BackendConnectionError as exc:
                    errors.append(str(exc))
        raise BackendConnectionError(
            "all bluetooth connection attempts failed: " + " | ".join(errors)
        )
    raise InvalidDeviceURIError(
        f"unsupported connection type: {device_uri.connection}"
    )


def open_document_images(payload: bytes) -> list[Image.Image]:
    handle = BytesIO(payload)
    image = Image.open(handle)
    return [frame.copy() for frame in ImageSequence.Iterator(image)]


def normalize_for_printer(
    image: Image.Image,
    model: str,
    threshold: int = 160,
) -> Image.Image:
    image = ImageOps.exif_transpose(image).convert("L")
    max_width = MAX_WIDTH_BY_MODEL[model]
    if image.width > max_width:
        height = max(1, round(image.height * (max_width / image.width)))
        image = image.resize((max_width, height), Image.Resampling.LANCZOS)
    monochrome = image.point(lambda value: 255 if value >= threshold else 0, mode="1")
    if monochrome.width % 8:
        padded_width = ((monochrome.width + 7) // 8) * 8
        padded = Image.new("1", (padded_width, monochrome.height), 255)
        padded.paste(monochrome, (0, 0))
        monochrome = padded
    return monochrome


class PrinterClient:
    def __init__(self, transport: BaseTransport):
        self._transport = transport
        self._packetbuf = bytearray()

    def close(self) -> None:
        self._transport.close()

    def print_images(
        self,
        images: list[Image.Image],
        model: str,
        density: int = 3,
        label_kind: str = "gap",
    ) -> None:
        logging.debug("Setting label density to %s", density)
        self.set_label_density(density)
        logging.debug("Setting label type to %s", LABEL_TYPE_BY_KIND.get(label_kind, 1))
        self.set_label_type(LABEL_TYPE_BY_KIND.get(label_kind, 1))
        logging.debug("Starting print job")
        self.start_print(model=model, total_pages=len(images))
        for image in images:
            normalized = normalize_for_printer(image, model=model)
            logging.debug(
                "Printing page width=%s height=%s", normalized.width, normalized.height
            )
            self.start_page_print()
            self.set_dimension(normalized.height, normalized.width, model=model, copies=1)
            for packet in encode_image_packets(normalized):
                self._send(packet)
            self.end_page_print()
        if model == "b1":
            self.wait_until_finished_by_status_poll(len(images))
        else:
            time.sleep(0.3)
        while not self.end_print():
            time.sleep(0.1)

    def _recv(self) -> list[NimbotPacket]:
        packets = []
        self._packetbuf.extend(self._transport.read(1024))
        while len(self._packetbuf) > 4:
            packet_length = self._packetbuf[3] + 7
            if len(self._packetbuf) < packet_length:
                break
            packet = NimbotPacket.from_bytes(self._packetbuf[:packet_length])
            packets.append(packet)
            del self._packetbuf[:packet_length]
        return packets

    def _send(self, packet: NimbotPacket) -> int:
        return self._transport.write(packet.to_bytes())

    def _transceive(self, request_code: int, data: bytes, response_offset: int = 1) -> NimbotPacket:
        response_code = request_code + response_offset
        logging.debug("Sending request 0x%02x expecting 0x%02x", request_code, response_code)
        self._send(NimbotPacket(request_code, data))
        for _ in range(10):
            for packet in self._recv():
                if packet.packet_type == response_code:
                    logging.debug("Received response 0x%02x", response_code)
                    return packet
            time.sleep(0.1)
        raise TimeoutError(f"timeout waiting for response to request 0x{request_code:02x}")

    def set_label_type(self, label_type: int) -> bool:
        response = self._transceive(RequestCode.SET_LABEL_TYPE, bytes((label_type,)), 16)
        return bool(response.data[0])

    def set_label_density(self, density: int) -> bool:
        response = self._transceive(RequestCode.SET_LABEL_DENSITY, bytes((density,)), 16)
        return bool(response.data[0])

    def start_print(self, model: str = "b1", total_pages: int = 1) -> bool:
        if model == "b1":
            payload = struct.pack(">HBBBBB", total_pages, 0, 0, 0, 0, 0)
        else:
            payload = b"\x01"
        response = self._transceive(RequestCode.START_PRINT, payload)
        return bool(response.data[0])

    def end_print(self) -> bool:
        response = self._transceive(RequestCode.END_PRINT, b"\x01")
        return bool(response.data[0])

    def get_print_status(self) -> tuple[int, int, int]:
        response = self._transceive(RequestCode.PRINT_STATUS, b"\x01", 16)
        data = response.data
        if len(data) < 3:
            raise TimeoutError("print status response was too short")
        page, page_print_progress, page_feed_progress = data[:3]
        logging.debug(
            "PrintStatus page=%s pagePrintProgress=%s pageFeedProgress=%s",
            page,
            page_print_progress,
            page_feed_progress,
        )
        return page, page_print_progress, page_feed_progress

    def wait_until_finished_by_status_poll(
        self,
        pages_to_print: int,
        poll_interval: float = 0.3,
        timeout: float = 8.0,
    ) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            page, page_print_progress, page_feed_progress = self.get_print_status()
            if (
                page == pages_to_print
                and page_print_progress == 100
                and page_feed_progress == 100
            ):
                return
            time.sleep(poll_interval)
        raise TimeoutError("timed out waiting for B1 print completion status")

    def start_page_print(self) -> bool:
        response = self._transceive(RequestCode.START_PAGE_PRINT, b"\x01")
        return bool(response.data[0])

    def end_page_print(self) -> bool:
        try:
            response = self._transceive(RequestCode.END_PAGE_PRINT, b"\x01")
            return bool(response.data[0])
        except TimeoutError:
            # B1 appears to sometimes accept page end without replying.
            logging.debug("No response to END_PAGE_PRINT; continuing")
            return True

    def set_dimension(
        self,
        height: int,
        width: int,
        model: str = "b1",
        copies: int = 1,
    ) -> bool:
        if model == "b1":
            payload = struct.pack(">HHH", height, width, copies)
        else:
            payload = struct.pack(">HH", height, width)
        response = self._transceive(RequestCode.SET_DIMENSION, payload)
        return bool(response.data[0])

def encode_image_packets(
    image: Image.Image,
    invert_bits: bool = False,
    reverse_bytes: bool = False,
) -> list[NimbotPacket]:
    raster = image.convert("1")
    bytes_per_row = math.ceil(raster.width / 8)
    packets: list[NimbotPacket] = []
    printhead_pixels = MAX_WIDTH_BY_MODEL["b1"]
    for y in range(raster.height):
        packed = bytearray(bytes_per_row)
        for x in range(bytes_per_row * 8):
            if x >= raster.width:
                continue
            if raster.getpixel((x, y)) == 0:
                packed[x // 8] |= 1 << (7 - (x % 8))

        row_data = bytes(packed)
        if invert_bits:
            row_data = bytes((~byte) & 0xFF for byte in row_data)
        if reverse_bytes:
            row_data = bytes(int(f"{byte:08b}"[::-1], 2) for byte in row_data)

        count_bytes = bytes(_count_pixels_for_bitmap_packet(row_data, printhead_pixels))
        row_index = y.to_bytes(2, "big")
        packets.append(NimbotPacket(0x85, row_index + count_bytes + b"\x01" + row_data))
    return packets

def _count_pixels_for_bitmap_packet(
    data: bytes,
    printhead_pixels: int,
) -> tuple[int, int, int]:
    chunk_size = max(1, math.floor(printhead_pixels / 8 / 3))
    split_mode = len(data) <= chunk_size * 3
    parts = [0, 0, 0]
    total = 0

    for byte_index, value in enumerate(data):
        chunk_index = min(2, byte_index // chunk_size) if split_mode else 0
        for bit_index in range(8):
            if value & (1 << bit_index):
                total += 1
                if split_mode:
                    parts[chunk_index] += 1

    if split_mode:
        return parts[0], parts[1], parts[2]

    total_le = total.to_bytes(2, "little")
    return 0, total_le[0], total_le[1]
