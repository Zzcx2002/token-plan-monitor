from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import logging
from logging.handlers import RotatingFileHandler

from .instance import SingleInstance
from .monitor import UsageMonitor
from .storage import PROJECT_ROOT, Storage
from .ui import MonitorUI


def configure_logging() -> None:
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_dir / "app.log",
        maxBytes=512_000,
        backupCount=2,
        encoding="utf-8",
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[handler],
    )


def _enable_dpi_awareness() -> None:
    user32 = ctypes.windll.user32
    user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
    if not user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
        user32.SetProcessDPIAware()


def _show_already_running() -> None:
    user32 = ctypes.windll.user32
    user32.MessageBoxW.argtypes = [
        wintypes.HWND,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.UINT,
    ]
    user32.MessageBoxW(
        None,
        "程序已在运行，请从系统托盘打开用量窗口。",
        "火山方舟用量监控",
        0x00040040,
    )


def main() -> None:
    _enable_dpi_awareness()
    instance = SingleInstance()
    if not instance.acquire():
        instance.release()
        _show_already_running()
        return
    try:
        configure_logging()
        storage = Storage()
        monitor = UsageMonitor(storage)
        app = MonitorUI(monitor, storage)
        app.run()
    finally:
        instance.release()


if __name__ == "__main__":
    main()
