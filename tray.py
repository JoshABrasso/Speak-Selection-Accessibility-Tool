"""System tray icon — the visible, always-there sign that Speak Selected is
running, plus a mouse-driven way into Settings/Help/Exit alongside the
keyboard shortcuts. Installing the app and getting nothing you can see or
click isn't a real application experience."""

import threading

import pystray
from PIL import Image, ImageDraw

import icons


def _build_icon_image() -> Image.Image:
    """The shared head-with-open-mouth-and-sound-marks glyph (see icons.py),
    white on transparent — the taskbar area handles its own light/dark
    background, so tray icons conventionally stay a single flat colour
    rather than following the app's own window theme."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    icons.draw_glyph(draw, cx=28, cy=32, r=27, color=(255, 255, 255, 255))
    return img


class TrayIcon:
    def __init__(self, on_open_settings, on_show_help, on_exit):
        self._on_exit = on_exit
        self._icon = pystray.Icon(
            "SpeakSelected",
            icon=_build_icon_image(),
            title="Speak Selected — active (Ctrl+Alt+Z to speak selected text)",
            menu=pystray.Menu(
                pystray.MenuItem("Speak Selected — active", None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Settings...", lambda: on_open_settings()),
                pystray.MenuItem("Help / Shortcuts...", lambda: on_show_help()),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Exit", self._handle_exit),
            ),
        )
        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()

    def _handle_exit(self) -> None:
        self._on_exit()
        self._icon.stop()
