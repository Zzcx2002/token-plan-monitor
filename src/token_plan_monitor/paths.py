"""Writable locations and bundled read-only resources.

Source runs keep the project-layout semantics (``PROJECT_ROOT/data`` etc.).
A PyInstaller build is portable instead: writable data and logs sit next to the
executable, while read-only resources are unpacked under ``sys._MEIPASS``.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _portable_root() -> Path:
    return Path(sys.executable).resolve().parent


def data_directory(platform: str | None = None, home: Path | None = None) -> Path:
    if (platform or sys.platform) == "darwin":
        return (home or Path.home()) / "Library/Application Support/TokenPlanMonitor"
    if is_frozen():
        return _portable_root() / "data"
    return PROJECT_ROOT / "data"


def log_directory(platform: str | None = None, home: Path | None = None) -> Path:
    if (platform or sys.platform) == "darwin":
        return (home or Path.home()) / "Library/Logs/TokenPlanMonitor"
    if is_frozen():
        return _portable_root() / "logs"
    return PROJECT_ROOT / "logs"


def resource_path(*parts: str) -> Path:
    """Resolve a read-only resource shipped inside the application bundle."""
    meipass = getattr(sys, "_MEIPASS", None)
    if is_frozen() and meipass:
        return Path(meipass).joinpath(*parts)
    return PROJECT_ROOT.joinpath(*parts)
