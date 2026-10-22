"""Whether Speak Selected launches automatically at login.

Reads and writes the real registry value directly rather than tracking a
separate on/off flag in settings.json — Windows also exposes this same Run
key through Task Manager's own Startup Apps page, so a second, independent
"remembered" state in our own settings file could drift from what's
actually true and show the user the wrong thing. The registry value itself
is the single source of truth; this module is just a thin, correct wrapper
around it.
"""

import sys
import winreg

import app_paths

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "SpeakSelected"


def _launch_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    return f'"{sys.executable}" "{app_paths.app_dir() / "speak_selected.py"}"'


def is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
            return True
    except OSError:
        return False


def set_enabled(enabled: bool) -> bool:
    """Returns whether the change actually took — this is a convenience
    toggle, not something that should ever crash the app if it fails."""
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_WRITE) as key:
            if enabled:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, _launch_command())
            else:
                try:
                    winreg.DeleteValue(key, VALUE_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False
