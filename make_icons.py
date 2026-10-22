"""Generates app.ico — the exe/taskbar/window-title-bar/Start-Menu icon.

Draws the exact same glyph as the system tray icon (icons.py) — the app
previously had two unrelated icon designs (a filled speaker for app.ico, a
head-with-sound-marks for the tray), which read as mismatched, like two
different apps. There is now exactly one icon design, used everywhere.

Run once at dev time whenever the design changes; the output is committed
as a real asset, not regenerated at every app launch.
"""

from pathlib import Path

from PIL import Image, ImageDraw

import icons

ACCENT = (0, 99, 177, 255)  # a blue distinct from Windows' own accent/volume icon
WHITE = (255, 255, 255, 255)


def build_app_icon() -> Image.Image:
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pad = size * 0.04
    draw.rounded_rectangle((pad, pad, size - pad, size - pad), radius=size * 0.22, fill=ACCENT)
    # Same proportions as the tray glyph (r relative to canvas), just
    # re-centred for a square icon instead of the tray's off-centre layout
    # (which leaves room on the right for the sound marks within a wide,
    # short tray cell — an icon canvas has no such constraint).
    r = size * 0.195
    cx = size * 0.40
    cy = size * 0.5
    icons.draw_glyph(draw, cx=cx, cy=cy, r=r, color=WHITE)
    return img


def main() -> None:
    out_dir = Path(__file__).parent
    icon = build_app_icon()
    sizes = [16, 24, 32, 48, 64, 128, 256]
    icon.save(out_dir / "app.ico", sizes=[(s, s) for s in sizes])
    icon.save(out_dir / "app_preview.png")
    print(f"Wrote {out_dir / 'app.ico'}")


if __name__ == "__main__":
    main()
