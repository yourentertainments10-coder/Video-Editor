"""Where bundled resources and user data live, in a source checkout and in the installed app.

In a source checkout the resources sit in the repository (``ui/dist``, ``assets/fonts``).
In the PyInstaller build they are unpacked under ``sys._MEIPASS``. User data (projects,
caches, logs) never lives next to the program: it goes to ``%LOCALAPPDATA%`` on Windows.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

APP_NAME = "OfflineVideoEditor"
DATA_DIR_ENV = "OFFLINE_EDITOR_DATA"


def is_frozen() -> bool:
    """True when running from the packaged (PyInstaller) build."""
    return bool(getattr(sys, "frozen", False)) and hasattr(sys, "_MEIPASS")


def resource_root() -> Path:
    """Folder that contains ``ui/dist`` and ``assets``."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS")).resolve()
    return Path(__file__).resolve().parents[1]


def ui_dist_dir() -> Path:
    return resource_root() / "ui" / "dist"


def fonts_dir() -> Path:
    return resource_root() / "assets" / "fonts"


def data_dir() -> Path:
    """Per-user data folder: projects, caches and logs.

    ``OFFLINE_EDITOR_DATA`` overrides it (tests, portable use). Otherwise Windows uses
    ``%LOCALAPPDATA%\\OfflineVideoEditor`` and other systems ``~/.local/share/...``.
    """
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
        return (root / APP_NAME).resolve()
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return (root / APP_NAME).resolve()


CREATE_NO_WINDOW = 0x08000000


def hide_child_consoles(popen_class=None, platform: str | None = None) -> bool:
    """Stop every FFmpeg child process from flashing a console window on Windows.

    The installed app has no console of its own, so Windows would open a new black
    window for each ``ffmpeg.exe`` call. This adds ``CREATE_NO_WINDOW`` to every
    ``subprocess.Popen`` (``subprocess.run`` uses it too) unless the caller set flags.
    Returns True when the patch was applied. Safe to call more than once.
    """
    platform = platform or sys.platform
    if platform != "win32":
        return False
    popen_class = popen_class or subprocess.Popen
    if getattr(popen_class, "_editor_no_window", False):
        return True
    original_init = popen_class.__init__

    def __init__(self, *args, **kwargs):
        if not kwargs.get("creationflags"):
            kwargs["creationflags"] = CREATE_NO_WINDOW
        original_init(self, *args, **kwargs)

    popen_class.__init__ = __init__
    popen_class._editor_no_window = True
    return True
