#!/usr/bin/env python3
"""Generate the macOS .icns bundle with tighter padding around the glyph.

This script crops transparent padding from the main icon artwork and rebuilds
`macos/AppBundle/AppIcon.icns` using the required iconset sizes. Re-run whenever
`assets/icloudbridge.png` changes.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "assets" / "icloudbridge.png"
ICONSET_DIR = ROOT / "macos" / "AppBundle" / "AppIcon.iconset"
OUTPUT = ROOT / "macos" / "AppBundle" / "AppIcon.icns"
TARGET_SIZE = 1024


def _debug(msg: str) -> None:
    print(f"[generate_app_icon] {msg}")


def trim_and_normalize(source: Path) -> Image.Image:
    """Remove transparent padding and return a 1024×1024 RGBA image."""
    image = Image.open(source).convert("RGBA")
    alpha = image.split()[-1]
    bbox = alpha.getbbox()
    if bbox is None:
        _debug("Source appears fully transparent; using original image")
        return image.resize((TARGET_SIZE, TARGET_SIZE), Image.LANCZOS)

    pad = int(max(image.width, image.height) * 0.04)
    left = max(bbox[0] - pad, 0)
    top = max(bbox[1] - pad, 0)
    right = min(bbox[2] + pad, image.width)
    bottom = min(bbox[3] + pad, image.height)
    cropped = image.crop((left, top, right, bottom))
    resized = cropped.resize((TARGET_SIZE, TARGET_SIZE), Image.LANCZOS)
    return resized


def write_iconset(master: Image.Image) -> None:
    if ICONSET_DIR.exists():
        shutil.rmtree(ICONSET_DIR)
    ICONSET_DIR.mkdir(parents=True)

    sizes = [16, 32, 64, 128, 256, 512]
    for size in sizes:
        for scale in (1, 2):
            target = size * scale
            filename = ICONSET_DIR / f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
            master.resize((target, target), Image.LANCZOS).save(filename)

    # Apple expects the 1024×1024 "@2x" asset for 512×512 explicitly
    if not (ICONSET_DIR / "icon_512x512@2x.png").exists():
        master.save(ICONSET_DIR / "icon_512x512@2x.png")


def build_icns() -> None:
    subprocess.run([
        "iconutil",
        "-c",
        "icns",
        str(ICONSET_DIR),
        "-o",
        str(OUTPUT),
    ], check=True)


def main() -> None:
    if not SRC.exists():
        raise SystemExit(f"Missing source icon: {SRC}")

    _debug("Trimming transparent padding from source artwork")
    master = trim_and_normalize(SRC)
    _debug("Writing intermediate iconset")
    write_iconset(master)
    _debug("Building .icns bundle")
    build_icns()
    shutil.rmtree(ICONSET_DIR, ignore_errors=True)
    _debug(f"Updated {OUTPUT}")


if __name__ == "__main__":
    main()
