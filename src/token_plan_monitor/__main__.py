from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import logging
import sys
import traceback
import uuid
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .instance import SingleInstance
from .monitor import UsageMonitor
from .storage import Storage
from .paths import data_directory, log_directory, resource_path

SELF_CHECK_REPORT = "self-check-result.txt"
SELF_CHECK_SERVICE = "TokenPlanMonitor.SelfCheck"


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


def _run_windows_self_check() -> int:
    """Deterministic in-exe verification of the frozen bundle.

    The windowed bootloader has no console, so the report is written next to
    the executable instead of stdout. Nothing here starts the tray: the risky
    PyInstaller surfaces (backend imports, bundled resources, portable paths,
    JSON/JSONL storage, and a real Credential Manager round trip) are checked
    directly, and every probe artifact is removed before the build is zipped.
    """
    lines: list[str] = []
    report = Path(sys.executable).resolve().parent / SELF_CHECK_REPORT

    def check(label: str) -> None:
        lines.append(f"ok: {label}")

    try:
        if not getattr(sys, "frozen", False):
            raise RuntimeError("self-check must run inside the frozen executable")
        if sys.platform != "win32":
            raise RuntimeError("self-check is Windows-only")

        import certifi
        import keyring
        import keyring.backends.Windows as windows_keyring
        import PIL.Image  # noqa: F401
        import pystray._win32  # noqa: F401
        import requests  # noqa: F401
        import tkinter  # noqa: F401
        check("imports: pystray._win32, keyring Windows backend, tkinter, "
              "Pillow, requests, certifi")
        active_backend = keyring.get_keyring()
        if not isinstance(active_backend, windows_keyring.WinVaultKeyring):
            raise AssertionError(
                f"Credential Manager is not active, got {type(active_backend)!r}")

        portable_root = Path(sys.executable).resolve().parent
        data_dir = data_directory()
        logs_dir = log_directory()
        if data_dir != portable_root / "data":
            raise AssertionError(f"unexpected data dir: {data_dir}")
        if logs_dir != portable_root / "logs":
            raise AssertionError(f"unexpected log dir: {logs_dir}")
        check("portable data/log directories resolve next to the executable")

        icon = resource_path("assets", "app.ico")
        if not icon.is_file():
            raise AssertionError(f"missing bundled icon: {icon}")
        if not Path(certifi.where()).is_file():
            raise AssertionError(f"missing CA bundle: {certifi.where()}")
        if windows_keyring.WinVaultKeyring.priority <= 0:
            raise AssertionError("Windows Credential Manager backend is not viable")
        check("bundled icon, CA bundle and keyring backend are present")

        configure_logging()
        if not (logs_dir / "app.log").is_file():
            raise AssertionError("log file was not created")
        check("rotating file logging works")

        storage = Storage()
        settings = storage.load_settings()
        storage.save_settings(settings)
        if storage.load_settings() != settings:
            raise AssertionError("settings round trip mismatch")
        check("settings JSON round trip in the portable data directory")

        from .history import HistoryEntry
        from .models import QuotaWindow
        entry = HistoryEntry(
            queried_at=datetime.now(UTC),
            account="0" * 12,
            plan_type="token_plan",
            windows=(QuotaWindow(kind="five_hour", used_percent=1.0),),
        )
        storage.append_history(entry, provider=settings.provider)
        history_path = storage.history_path(settings.provider)
        if not history_path.is_file() or not history_path.read_text(
                encoding="utf-8").strip():
            raise AssertionError(f"history file not written: {history_path}")
        if not storage.load_history(settings.provider):
            raise AssertionError("history round trip returned no rows")
        check(f"history JSONL round trip ({history_path.name})")

        secret = f"self-check-{uuid.uuid4().hex}"
        keyring.set_password(SELF_CHECK_SERVICE, "probe", secret)
        if keyring.get_password(SELF_CHECK_SERVICE, "probe") != secret:
            raise AssertionError("credential readback mismatch")
        keyring.delete_password(SELF_CHECK_SERVICE, "probe")
        if keyring.get_password(SELF_CHECK_SERVICE, "probe") is not None:
            raise AssertionError("credential was not deleted")
        check("Credential Manager write/read/delete round trip")

        lines.append("SELF-CHECK OK")
        report.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return 0
    except Exception:  # noqa: BLE001 - the report is the failure channel
        lines.append("SELF-CHECK FAILED")
        lines.append(traceback.format_exc())
        report.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return 1


def main() -> None:
    if sys.platform == "win32" and "--self-check" in sys.argv:
        raise SystemExit(_run_windows_self_check())
    if sys.platform == "darwin" and ("--preview" in sys.argv or "--smoke-test" in sys.argv):
        from .macos.preview import main as preview
        preview([arg for arg in sys.argv[1:] if arg != "--preview"])
        return
    if sys.platform == "darwin" and "--notify-probe" in sys.argv:
        # Dev entry, same class as --preview: needs a LaunchServices launch
        # (open -W ... --args --notify-probe) for authorization prompts to work.
        from .macos.notify_probe import run as notify_probe
        raise SystemExit(notify_probe())
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
