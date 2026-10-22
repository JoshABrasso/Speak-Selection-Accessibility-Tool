"""Light/dark theming — detects the real Windows system setting and provides
both palettes, instead of assuming dark mode regardless of what the user
actually has Windows set to."""

import winreg
from dataclasses import dataclass

PERSONALIZE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"


@dataclass(frozen=True)
class Theme:
    bg: str              # main window/panel background
    bg_header: str       # the OSD panel's drag-handle header strip
    fg: str               # primary text and icon colour
    fg_secondary: str     # status text, less prominent than fg
    fg_muted: str         # the shortcut-legend text in Settings/Onboarding
    separator: str        # divider lines and the OSD panel's 1px border
    hover: str            # button hover/active background
    field_bg: str         # ttk Combobox/Scale field background
    icon_rgba: tuple      # fill colour for PIL-drawn icons (circled +/-)
    cta_bg: str           # the Onboarding "Got it" button background
    cta_fg: str           # the Onboarding "Got it" button text


DARK = Theme(
    bg="#202020", bg_header="#2b2b2b", fg="#ffffff", fg_secondary="#cccccc",
    fg_muted="#999999", separator="#3f3f3f", hover="#333333", field_bg="#2b2b2b",
    icon_rgba=(255, 255, 255, 255), cta_bg="#ffffff", cta_fg="#000000",
)

LIGHT = Theme(
    bg="#f3f3f3", bg_header="#e6e6e6", fg="#1a1a1a", fg_secondary="#3a3a3a",
    fg_muted="#5f5f5f", separator="#d6d6d6", hover="#e0e0e0", field_bg="#ffffff",
    icon_rgba=(26, 26, 26, 255), cta_bg="#1a1a1a", cta_fg="#ffffff",
)


def is_system_dark_mode() -> bool:
    """Reads the same registry value Windows itself uses to decide whether
    apps should render light or dark — AppsUseLightTheme, 0 = dark, 1 = light.
    Defaults to dark if the key is missing (older Windows) rather than
    guessing wrong in either direction without a reason to."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PERSONALIZE_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return value == 0
    except OSError:
        return True


def get_theme() -> Theme:
    return DARK if is_system_dark_mode() else LIGHT
