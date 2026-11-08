"""Writable locations; application resources never serve as macOS storage."""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def data_directory(platform: str | None = None, home: Path | None = None) -> Path:
    if (platform or sys.platform) == "darwin":
        return (home or Path.home()) / "Library/Application Support/TokenPlanMonitor"
    return PROJECT_ROOT / "data"


def log_directory(platform: str | None = None, home: Path | None = None) -> Path:
    if (platform or sys.platform) == "darwin":
        return (home or Path.home()) / "Library/Logs/TokenPlanMonitor"
    return PROJECT_ROOT / "logs"
