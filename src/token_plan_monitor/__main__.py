from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import logging
import sys
from logging.handlers import RotatingFileHandler

from .instance import SingleInstance
from .monitor import UsageMonitor
from .storage import Storage
from .paths import log_directory


def configure_logging() -> None:
    log_dir = log_directory()
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
    if sys.platform == "darwin" and ("--preview" in sys.argv or "--smoke-test" in sys.argv):
        from .macos.preview import main as preview
        preview([arg for arg in sys.argv[1:] if arg != "--preview"])
        return
    if sys.platform == "darwin":
        from .macos.ui import MonitorUI, show_already_running
    elif sys.platform == "win32":
        from .ui import MonitorUI
        show_already_running = _show_already_running
        _enable_dpi_awareness()
    else:
        raise SystemExit("Only Windows and macOS are supported")
    instance = SingleInstance()
    if not instance.acquire():
        instance.release()
        show_already_running()
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
