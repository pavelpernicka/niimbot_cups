import os
import sys
from urllib.parse import quote

from serial.tools.list_ports import comports as list_comports

from nimbot_cups.printer import (
    BackendConnectionError,
    InvalidDeviceURIError,
    PrinterClient,
    open_document_images,
    parse_device_uri,
    transport_for_uri,
)

CUPS_BACKEND_OK = 0
CUPS_BACKEND_FAILED = 1
CUPS_BACKEND_HOLD = 3


def parse_job_options(raw_options: str) -> tuple[int, str]:
    density = 3
    label_kind = "gap"
    for token in raw_options.split():
        if token.startswith("NimbotDensity="):
            density = int(token.split("=", 1)[1])
        elif token.startswith("NimbotLabelKind="):
            label_kind = token.split("=", 1)[1].strip().lower()
    return density, label_kind


def bluetooth_candidates() -> list[tuple[str, str]]:
    candidates = []
    try:
        import subprocess

        result = subprocess.run(
            ["bluetoothctl", "devices"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return candidates
    for line in result.stdout.splitlines():
        parts = line.strip().split(" ", 2)
        if len(parts) != 3:
            continue
        _, address, name = parts
        lower_name = name.lower()
        if "niimbot" in lower_name or "nimbot" in lower_name:
            candidates.append((address.upper(), name))
    return candidates


def serial_candidates() -> list[tuple[str, str]]:
    candidates = []
    for port in list_comports():
        attrs = " ".join(
            filter(None, [str(port.description), str(port.manufacturer), str(port.product)])
        ).lower()
        if "niimbot" in attrs or "nimbot" in attrs:
            candidates.append((port.device, port.description or "USB serial"))
    return candidates


def list_devices() -> int:
    for port, description in serial_candidates():
        uri = f"nimbot://usb?port={quote(port, safe='')}&model=b1"
        print(
            f'direct {uri} "Nimbot B1 (USB)" "{description} on {port}" '
            '"MFG:Nimbot;MDL:B1;CMD:TIFF;"'
        )
    for address, name in bluetooth_candidates():
        uri = f"nimbot://bluetooth?address={quote(address, safe='')}&model=b1"
        print(
            f'direct {uri} "Nimbot B1 (Bluetooth)" "{name} at {address}" '
            '"MFG:Nimbot;MDL:B1;CMD:TIFF;"'
        )
    return 0


def load_job_payload(argv: list[str]) -> bytes:
    if len(argv) > 6:
        with open(argv[6], "rb") as handle:
            return handle.read()
    return sys.stdin.buffer.read()


def main(argv: list[str]) -> int:
    if len(argv) == 1:
        return list_devices()

    client = None
    try:
        device_uri = parse_device_uri(os.environ["DEVICE_URI"])
        copies = int(argv[4]) if len(argv) > 4 else 1
        density, label_kind = parse_job_options(argv[5] if len(argv) > 5 else "")

        payload = load_job_payload(argv)
        images = open_document_images(payload)
        transport = transport_for_uri(device_uri)
        client = PrinterClient(transport)
        for _ in range(max(copies, 1)):
            client.print_images(
                images=images,
                model=device_uri.model,
                density=density,
                label_kind=label_kind or device_uri.label_kind,
            )
        return CUPS_BACKEND_OK
    except InvalidDeviceURIError as exc:  # pragma: no cover
        print(f'ERROR: Nimbot queue configuration error: {exc}', file=sys.stderr)
        return CUPS_BACKEND_FAILED
    except BackendConnectionError as exc:  # pragma: no cover
        print(f'ERROR: Nimbot printer connection failed: {exc}', file=sys.stderr)
        return CUPS_BACKEND_FAILED
    except Exception as exc:  # pragma: no cover
        print(f"ERROR: nimbot backend failed: {exc}", file=sys.stderr)
        return CUPS_BACKEND_HOLD
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
