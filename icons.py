"""The one shared icon design — a head with an open mouth and sound marks,
used everywhere the app shows an icon (exe, taskbar, window title bars,
Start Menu, and the system tray). Previously the tray icon and the main
app icon were two unrelated designs (a speaker glyph vs. this head), which
read as mismatched/not-the-same-app — this is the single source of truth
both pull from now."""

from PIL import ImageDraw


def draw_glyph(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float,
               color: tuple, ring_thickness_ratio: float = 0.41) -> None:
    """The head (a thick hollow ring with a wedge bitten out for the open
    mouth) plus two solid chevron sound marks, at an arbitrary centre/scale.
    Solid shapes, not thin outlines — thin strokes don't survive being
    shrunk to real tray-icon size (confirmed by testing a hollow-outline
    version at 16px: it just read as a broken circle)."""
    draw.pieslice((cx - r, cy - r, cx + r, cy + r), start=24, end=336, fill=color)
    inner_r = r * (1 - ring_thickness_ratio)
    draw.pieslice((cx - inner_r, cy - inner_r, cx + inner_r, cy + inner_r),
                  start=0, end=360, fill=(0, 0, 0, 0))

    def chevron(x: float, half_h: float, thickness: float) -> None:
        draw.polygon([
            (x, cy - half_h), (x + thickness, cy), (x, cy + half_h),
            (x + thickness * 0.5, cy),
        ], fill=color)

    chevron(cx + r * 1.037, r * 0.370, r * 0.259)
    chevron(cx + r * 1.407, r * 0.519, r * 0.296)
