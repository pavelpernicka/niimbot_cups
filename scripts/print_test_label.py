#!/usr/bin/env python3

import argparse
import logging
import signal
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from nimbot_cups.printer import PrinterClient, parse_device_uri, transport_for_uri

DEFAULT_DPI = 203


def make_test_image(text: str, width_mm: int, height_mm: int) -> Image.Image:
    width_px = max(1, round(width_mm / 25.4 * DEFAULT_DPI))
    height_px = max(1, round(height_mm / 25.4 * DEFAULT_DPI))
    image = Image.new("L", (width_px, height_px), 255)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    draw.rectangle((0, 0, width_px - 1, height_px - 1), outline=0, width=1)
    draw.text((8, 8), text, fill=0, font=font)
    draw.text((8, 24), f"{width_mm}x{height_mm} mm", fill=0, font=font)
    return image


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Direct Nimbot print test without CUPS"
    )
    parser.add_argument(
        "--device-uri",
        required=True,
        help="e.g. nimbot://bluetooth?address=03:31:0C:00:06:3B&model=b1",
    )
    parser.add_argument("--image", help="Path to an input image to print")
    parser.add_argument("--text", default="Nimbot direct test", help="Test label text")
    parser.add_argument("--width-mm", type=int, default=30, help="Label width in mm")
    parser.add_argument("--height-mm", type=int, default=15, help="Label height in mm")
    parser.add_argument(
        "--density", type=int, choices=[1, 2, 3, 4, 5], default=3, help="Print density"
    )
    parser.add_argument(
        "--save-preview",
        help="Optional PNG path to save the generated test image before printing",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="Overall timeout in seconds for the direct print test",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    def _alarm_handler(_signum, _frame):
        raise TimeoutError(f"direct print test exceeded {args.timeout}s")

    signal.signal(signal.SIGALRM, _alarm_handler)
    signal.alarm(args.timeout)

    if args.image:
        image = Image.open(args.image)
    else:
        image = make_test_image(args.text, args.width_mm, args.height_mm)

    if args.save_preview:
        preview_path = Path(args.save_preview)
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(preview_path)

    device_uri = parse_device_uri(args.device_uri)
    logging.info("Using device URI: %s", args.device_uri)
    transport = transport_for_uri(device_uri)
    client = PrinterClient(transport)
    try:
        client.print_images(
            [image],
            model=device_uri.model,
            density=args.density,
            label_kind=device_uri.label_kind,
        )
        logging.info("Print job submitted successfully")
    finally:
        signal.alarm(0)
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
