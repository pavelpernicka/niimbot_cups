import unittest

from PIL import Image

from nimbot_cups.packet import NimbotPacket
from nimbot_cups.backend import parse_job_options
from nimbot_cups.printer import (
    InvalidDeviceURIError,
    PrinterClient,
    bluetooth_address_candidates,
    bluetooth_channel_candidates,
    encode_image_packets,
    parse_device_uri,
)
from nimbot_cups.render import fit_page_to_label, target_pixels_from_options


class FakeTransport:
    def __init__(self):
        self.writes = []
        self.responses = bytearray()

    def read(self, length: int) -> bytes:
        chunk = self.responses[:length]
        del self.responses[:length]
        return bytes(chunk)

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        packet = NimbotPacket.from_bytes(data)
        if packet.packet_type == 0xA3:
            self.responses.extend(NimbotPacket(0xB3, b"\x01\x64\x64").to_bytes())
        else:
            response_code = packet.packet_type + (
                16 if packet.packet_type in {0x21, 0x23} else 1
            )
            self.responses.extend(NimbotPacket(response_code, b"\x01").to_bytes())
        return len(data)

    def close(self) -> None:
        return None


class BackendTests(unittest.TestCase):
    def test_parse_device_uri(self):
        device = parse_device_uri("nimbot://usb?port=%2Fdev%2FttyACM0&model=b1")
        self.assertEqual(device.connection, "usb")
        self.assertEqual(device.port, "/dev/ttyACM0")

    def test_parse_rfcomm_device_uri(self):
        device = parse_device_uri("nimbot://rfcomm?port=%2Fdev%2Frfcomm0&model=b1")
        self.assertEqual(device.connection, "rfcomm")
        self.assertEqual(device.port, "/dev/rfcomm0")

    def test_packet_encoding_roundtrip(self):
        packet = NimbotPacket(0x21, b"\x05")
        self.assertEqual(NimbotPacket.from_bytes(packet.to_bytes()).data, b"\x05")

    def test_rejects_placeholder_bluetooth_mac(self):
        with self.assertRaises(InvalidDeviceURIError):
            parse_device_uri(
                "nimbot://bluetooth?address=AA%3ABB%3ACC%3ADD%3AEE%3AFF&model=b1"
            )

    def test_generates_rotated_bluetooth_fallback_address(self):
        self.assertEqual(
            bluetooth_address_candidates("03:31:0C:00:06:3B"),
            ["03:31:0C:00:06:3B", "0C:03:31:00:06:3B"],
        )

    def test_bluetooth_channel_candidates_fallback_range(self):
        self.assertEqual(
            bluetooth_channel_candidates("03:31:0C:00:06:3B"),
            [1, 2, 3, 4, 5, 6, 7, 8],
        )

    def test_page_size_option_maps_to_label_pixels(self):
        self.assertEqual(
            target_pixels_from_options({"PageSize": "w30h15"}),
            (240, 120),
        )

    def test_custom_page_size_option_maps_to_label_pixels(self):
        self.assertEqual(
            target_pixels_from_options({"PageSize": "Custom.141.73x85.04"}),
            (400, 240),
        )

    def test_fit_page_to_label_returns_target_canvas(self):
        image = Image.new("L", (1200, 800), 255)
        fitted = fit_page_to_label(image, (240, 120))
        self.assertEqual(fitted.size, (240, 120))

    def test_job_options_parse_density_and_label_kind(self):
        density, label_kind = parse_job_options(
            "number-up=1 NimbotDensity=4 NimbotLabelKind=continuous PageSize=w30h15"
        )
        self.assertEqual(density, 4)
        self.assertEqual(label_kind, "continuous")

    def test_print_job_emits_line_packets(self):
        transport = FakeTransport()
        client = PrinterClient(transport)
        image = Image.new("1", (16, 4), 255)
        client.print_images([image], model="b1", density=3)
        line_packets = [payload for payload in transport.writes if payload[2] == 0x85]
        self.assertEqual(len(line_packets), 4)

    def test_b1_uses_long_start_and_page_size_packets(self):
        transport = FakeTransport()
        client = PrinterClient(transport)
        image = Image.new("1", (16, 4), 255)
        client.print_images([image], model="b1", density=3)
        start_packet = next(payload for payload in transport.writes if payload[2] == 0x01)
        size_packet = next(payload for payload in transport.writes if payload[2] == 0x13)
        self.assertEqual(start_packet[3], 7)
        self.assertEqual(size_packet[3], 6)

    def test_b1_polls_print_status_before_print_end(self):
        transport = FakeTransport()
        client = PrinterClient(transport)
        image = Image.new("1", (16, 4), 255)
        client.print_images([image], model="b1", density=3)
        packet_types = [payload[2] for payload in transport.writes]
        self.assertIn(0xA3, packet_types)
        self.assertLess(packet_types.index(0xA3), packet_types.index(0xF3))

    def test_bitmap_row_header_contains_black_pixel_count(self):
        image = Image.new("1", (8, 1), 255)
        image.putpixel((0, 0), 0)
        image.putpixel((1, 0), 0)
        packet = encode_image_packets(image)[0]
        self.assertEqual(packet.packet_type, 0x85)
        self.assertEqual(packet.data[:6].hex(), "000002000001")


if __name__ == "__main__":
    unittest.main()
