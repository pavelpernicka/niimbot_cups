import re
import shlex
import subprocess
import sys
import tempfile
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops, ImageOps, ImageSequence

DEFAULT_DPI = 203


def mm_to_px(mm: float, dpi: int = DEFAULT_DPI) -> int:
    return max(1, round(mm / 25.4 * dpi))


def parse_options(raw_options: str) -> dict[str, str]:
    options: dict[str, str] = {}
    for token in shlex.split(raw_options):
        if "=" in token:
            key, value = token.split("=", 1)
            options[key] = value
        else:
            options[token] = "true"
    return options


def load_input_bytes(path: str | None) -> bytes:
    if path:
        return Path(path).read_bytes()
    return sys.stdin.buffer.read()


def is_pdf(payload: bytes) -> bool:
    return payload.lstrip().startswith(b"%PDF")


def render_pdf_to_images(payload: bytes, dpi: int) -> list[Image.Image]:
    with tempfile.TemporaryDirectory(prefix="nimbot-cups-") as tempdir:
        source_path = Path(tempdir) / "job.pdf"
        source_path.write_bytes(payload)
        prefix = Path(tempdir) / "page"
        subprocess.run(
            [
                "pdftoppm",
                "-gray",
                "-r",
                str(dpi),
                "-png",
                str(source_path),
                str(prefix),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        pages = []
        for page in sorted(Path(tempdir).glob("page-*.png")):
            pages.append(Image.open(page).copy())
        if not pages:
            raise RuntimeError("PDF renderer produced no pages")
        return pages


def apply_orientation(image: Image.Image, options: dict[str, str]) -> Image.Image:
    orientation = options.get("orientation-requested")
    if orientation == "4":
        return image.rotate(-90, expand=True)
    if orientation == "5":
        return image.rotate(90, expand=True)
    if orientation == "6":
        return image.rotate(180, expand=True)
    if options.get("landscape", "").lower() in {"true", "yes"}:
        return image.rotate(-90, expand=True)
    return image


def target_pixels_from_options(options: dict[str, str]) -> tuple[int, int] | None:
    page_size = options.get("PageSize")
    if not page_size:
        return None
    match = re.fullmatch(r"w(\d+)h(\d+)", page_size)
    if not match:
        custom = re.fullmatch(r"Custom\.(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)", page_size)
        if not custom:
            return None
        width_pt = float(custom.group(1))
        height_pt = float(custom.group(2))
        width_px = max(1, round(width_pt / 72.0 * DEFAULT_DPI))
        height_px = max(1, round(height_pt / 72.0 * DEFAULT_DPI))
        return width_px, height_px
    width_mm = int(match.group(1))
    height_mm = int(match.group(2))
    return mm_to_px(width_mm), mm_to_px(height_mm)


def fit_page_to_label(image: Image.Image, target_size: tuple[int, int] | None) -> Image.Image:
    if not target_size:
        return image

    grayscale = image.convert("L")
    # Crop surrounding white margins so small text from Letter/A4 source documents
    # gets scaled to the actual label instead of becoming microscopic.
    inverted = ImageChops.invert(grayscale)
    bbox = inverted.getbbox()
    if bbox:
        grayscale = grayscale.crop(bbox)

    # Scale cropped content to fill the label canvas as much as possible while
    # preserving aspect ratio. This is especially important for GUI/text jobs
    # that first become a Letter/A4 PDF with large white margins.
    contained = ImageOps.contain(grayscale, target_size, Image.Resampling.NEAREST)
    canvas = Image.new("L", target_size, 255)
    offset = (
        (target_size[0] - contained.width) // 2,
        (target_size[1] - contained.height) // 2,
    )
    canvas.paste(contained, offset)
    return canvas


def prepare_pages(payload: bytes, options: dict[str, str]) -> list[Image.Image]:
    if is_pdf(payload):
        pages = render_pdf_to_images(payload, dpi=DEFAULT_DPI)
    else:
        image = Image.open(BytesIO(payload))
        pages = [frame.copy() for frame in ImageSequence.Iterator(image)]
    target_size = target_pixels_from_options(options)
    prepared = []
    for page in pages:
        page = ImageOps.exif_transpose(page)
        page = apply_orientation(page, options)
        page = fit_page_to_label(page, target_size)
        prepared.append(page)
    return prepared


def save_multi_page_tiff(images: list[Image.Image]) -> bytes:
    if not images:
        raise ValueError("no images to save")
    normalized = [image.convert("1") for image in images]
    buffer = BytesIO()
    normalized[0].save(
        buffer,
        format="TIFF",
        compression="group4",
        save_all=True,
        append_images=normalized[1:],
        dpi=(DEFAULT_DPI, DEFAULT_DPI),
    )
    return buffer.getvalue()


def main(argv: list[str]) -> int:
    job_id = argv[1] if len(argv) > 1 else "0"
    options = parse_options(argv[5] if len(argv) > 5 else "")
    input_path = argv[6] if len(argv) > 6 else None
    try:
        payload = load_input_bytes(input_path)
        pages = prepare_pages(payload, options)
        sys.stdout.buffer.write(save_multi_page_tiff(pages))
        return 0
    except Exception as exc:  # pragma: no cover
        print(f"ERROR: nimbot-render failed for job {job_id}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
