"""Where this app's own files actually live — correct whether running as a
plain Python script or as a PyInstaller-frozen exe.

PyInstaller's __file__ resolves into its internal bundled-runtime folder, not
the exe's own folder, so anything using Path(__file__).parent silently looks
in the wrong place once packaged. sys.executable's folder is the real one in
both cases that matters to us (frozen: the exe's folder; dev: this folder)."""

import os
import sys
from pathlib import Path


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def data_dir() -> Path:
    """Where settings/logs live — deliberately NOT app_dir(). The installed
    program folder should only ever contain what the installer put there;
    anything the app writes at runtime belongs in the OS's per-user data
    location instead. Besides being the correct convention, it's also what
    makes uninstall actually clean: MSI only removes files/folders it
    itself installed, so settings.json or a log file sitting inside the
    Programs folder leaves that folder behind forever after uninstall."""
    path = Path(os.environ["APPDATA"]) / "SpeakSelected"
    path.mkdir(parents=True, exist_ok=True)
    return path
