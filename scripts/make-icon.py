"""Generate the Blurt app icon and write blurt/assets/Blurt.icns.

Build-time only (needs Pillow + macOS `iconutil`); not an app dependency. Draws a
single master, renders the size set `iconutil` expects, and packs the .icns into the
package so it ships in the wheel and the installer can drop it into any Blurt.app.

The look matches the product: a dark slate tile, a soft off-white lowercase "b", and
the slate-blue caret from the UI accent, nodding to "just type."
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_PKG = Path(__file__).resolve().parent.parent / "blurt"
_DEFAULT_ICNS = _PKG / "assets" / "Blurt.icns"
# A web copy of the same mark, used by the front-end splash so the brand moment matches
# the dock/app icon exactly.
_STATIC_PNG = _PKG / "static" / "blurt-icon.png"

# Palette pulled from the UI: dark slate surface, off-white ink, slate-blue accent.
BG_TOP = (38, 44, 52)  # #262c34
BG_BOTTOM = (24, 28, 33)  # #181c21
INK = (233, 236, 239)  # near-white
ACCENT = (122, 146, 173)  # slate blue-grey caret

S = 1024  # master canvas; macOS downscales the rest


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in ("/System/Library/Fonts/SFNSRounded.ttf", "/System/Library/Fonts/SFNS.ttf"):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", size)


def _rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return mask


def _vertical_gradient(size: int, top: tuple, bottom: tuple) -> Image.Image:
    grad = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / (size - 1)
        grad.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(top, bottom, strict=True)))
    return grad.resize((size, size))


def make_master() -> Image.Image:
    base = _vertical_gradient(S, BG_TOP, BG_BOTTOM).convert("RGBA")
    icon = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    icon.paste(base, (0, 0), _rounded_mask(S, radius=int(S * 0.22)))  # macOS squircle-ish

    draw = ImageDraw.Draw(icon)
    font = _font(int(S * 0.62))
    glyph = "b"
    box = draw.textbbox((0, 0), glyph, font=font)
    gw, gh = box[2] - box[0], box[3] - box[1]
    gx = (S - gw) / 2 - box[0] - int(S * 0.06)  # nudge left to leave room for the caret
    gy = (S - gh) / 2 - box[1]
    draw.text((gx, gy), glyph, font=font, fill=INK)

    # Blinking-caret motif to the right of the b: the "just type" cue.
    cw, ch = int(S * 0.045), int(gh * 0.92)
    cx = gx + gw + int(S * 0.04)
    cy = gy + box[1] + (gh - ch) // 2
    draw.rounded_rectangle((cx, cy, cx + cw, cy + ch), radius=cw // 2, fill=ACCENT)
    return icon


def main() -> None:
    icns = Path(sys.argv[1]) if len(sys.argv) > 1 else _DEFAULT_ICNS
    icns.parent.mkdir(parents=True, exist_ok=True)
    master = make_master()
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "Blurt.iconset"
        iconset.mkdir()
        # The exact set `iconutil` expects: each base size at @1x and @2x (double pixels).
        for base in (16, 32, 128, 256, 512):
            master.resize((base, base), Image.LANCZOS).save(iconset / f"icon_{base}x{base}.png")
            master.resize((base * 2, base * 2), Image.LANCZOS).save(
                iconset / f"icon_{base}x{base}@2x.png"
            )
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns)], check=True)
    print(f"wrote {icns}")

    _STATIC_PNG.parent.mkdir(parents=True, exist_ok=True)
    master.resize((512, 512), Image.LANCZOS).save(_STATIC_PNG)
    print(f"wrote {_STATIC_PNG}")


if __name__ == "__main__":
    main()
