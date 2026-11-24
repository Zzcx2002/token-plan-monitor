from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import faulthandler
import logging
import os
import sys
import traceback
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .instance import SingleInstance
from .monitor import UsageMonitor
from .storage import Storage
from .paths import data_directory, log_directory, resource_path

SELF_CHECK_REPORT = "self-check-result.txt"
SELF_CHECK_SERVICE = "TokenPlanMonitor.SelfCheck"
_FATAL_LOG_HANDLE = None


def _configure_isolated_fatal_logging() -> None:
    """Retain native-fault stacks only for the opt-in isolated GUI harness."""
    global _FATAL_LOG_HANDLE
    if os.environ.get("TOKEN_PLAN_MONITOR_GUI_TEST") != "1":
        return
    log_dir = log_directory()
    log_dir.mkdir(parents=True, exist_ok=True)
    _FATAL_LOG_HANDLE = (log_dir / "fatal.log").open(
        "a", encoding="utf-8", buffering=1
    )
    faulthandler.enable(file=_FATAL_LOG_HANDLE, all_threads=True)


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
    if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
        return
    # Frozen builds already declare PerMonitorV2 in the embedded manifest; in
    # that case the call above fails because awareness can only be set once.
    try:
        user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        user32.AreDpiAwarenessContextsEqual.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        user32.AreDpiAwarenessContextsEqual.restype = wintypes.BOOL
        current = user32.GetThreadDpiAwarenessContext()
        if current and user32.AreDpiAwarenessContextsEqual(current, ctypes.c_void_p(-4)):
            return
    except AttributeError:
        pass
    user32.SetProcessDPIAware()


def _assert_single_system_ucrt() -> None:
    """Reject a second, bundled UCRT inside the frozen process.

    A toolchain directory on the build machine's PATH can make PyInstaller
    collect physical ``ucrtbase.dll`` / ``api-ms-win-crt-*`` copies. The app
    then loads that stale CRT next to the system one, and native window
    creation eventually calls ``abort()`` (0xc0000409) with no Python
    traceback. Enumerate every loaded module: GetModuleHandle cannot tell two
    same-named DLLs apart, but EnumProcessModulesEx lists both files.
    """
    psapi = ctypes.windll.psapi
    kernel32 = ctypes.windll.kernel32
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.EnumProcessModulesEx.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(wintypes.HMODULE), wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), wintypes.DWORD,
    ]
    psapi.EnumProcessModulesEx.restype = wintypes.BOOL
    psapi.GetModuleFileNameExW.argtypes = [
        wintypes.HANDLE, wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD,
    ]
    psapi.GetModuleFileNameExW.restype = wintypes.DWORD

    process = kernel32.GetCurrentProcess()
    needed = wintypes.DWORD()
    psapi.EnumProcessModulesEx(
        process, None, 0, ctypes.byref(needed), 0x03  # LIST_MODULES_ALL
    )
    count = needed.value // ctypes.sizeof(wintypes.HMODULE)
    modules = (wintypes.HMODULE * count)()
    if not psapi.EnumProcessModulesEx(
        process, modules, needed.value, ctypes.byref(needed), 0x03
    ):
        raise ctypes.WinError()

    ucrt_paths: list[str] = []
    for module in modules:
        name_buffer = ctypes.create_unicode_buffer(32768)
        if psapi.GetModuleFileNameExW(process, module, name_buffer, 32768):
            if Path(name_buffer.value).name.lower() == "ucrtbase.dll":
                ucrt_paths.append(name_buffer.value)

    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows")).resolve()
    if len(ucrt_paths) != 1 or not Path(ucrt_paths[0]).resolve().is_relative_to(
        system_root
    ):
        raise AssertionError(
            f"expected a single system UCRT, got: {ucrt_paths!r}"
        )


def _check_tcl_service_guard(tkinter) -> list[str]:
    """Prove the frozen process can suppress nested Tcl dispatch, not just import it."""
    from .ui_graphics import (
        TCL_SERVICE_NONE,
        native_handle,
        require_tcl_service_guard,
        tcl_events_deferred,
        tcl_service_guard_status,
    )

    setter, getter = require_tcl_service_guard()
    available, source = tcl_service_guard_status()
    if not available or setter is None or getter is None:
        raise AssertionError(f"Tcl service guard unavailable: {source}")
    lines = [f"TCL_SERVICE_GUARD_OK path={source}"]
    root = tkinter.Tk()
    root.withdraw()
    original = getter()
    try:
        top = tkinter.Toplevel(root)
        top.withdraw()
        top.geometry("320x240+40+40")
        top.update_idletasks()
        hwnd = native_handle(top)

        if setter(TCL_SERVICE_NONE) != original or getter() != TCL_SERVICE_NONE:
            raise AssertionError("could not select TCL_SERVICE_NONE")
        if setter(1) != TCL_SERVICE_NONE or getter() != 1:
            raise AssertionError("could not select TCL_SERVICE_ALL")
        setter(original)
        lines.append("TCL_SERVICE_ROUNDTRIP_OK")

        marker = {"idle": False}
        root.after_idle(lambda: marker.__setitem__("idle", True))
        setter(1)
        try:
            user32 = ctypes.windll.user32
            user32.SetWindowPos.argtypes = [
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, ctypes.c_uint,
            ]
            user32.SetWindowPos.restype = wintypes.BOOL
            with tcl_events_deferred():
                if getter() != TCL_SERVICE_NONE:
                    raise AssertionError("native boundary was not deferred")
                user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x37)
            if getter() != 1:
                raise AssertionError("ambient TCL_SERVICE_ALL was not restored")
            if marker["idle"]:
                raise AssertionError("native WndProc reentered a queued Python idle callback")
        finally:
            setter(original)
        root.update_idletasks()
        if not marker["idle"]:
            raise AssertionError("deferred idle callback was lost")
        lines.append("TCL_SERVICE_DEFER_OK")

        # Exercise the actual Settings presentation method in the frozen
        # process, offscreen and without storage, credentials or a model fetch.
        from .settings_view import SettingsDialog
        dialog = object.__new__(SettingsDialog)
        dialog.window = top
        dialog.body = SimpleNamespace(canvas=tkinter.Canvas(top))
        dialog.body.canvas.pack(fill="both", expand=True)
        dialog._closing = False
        dialog._presented = False
        dialog._present_after = None
        dialog._anchor_after = None
        top.geometry("320x240+10000+10000")
        setter(1)
        try:
            dialog._present()
            if getter() != 1 or not dialog._presented:
                raise AssertionError("Settings presentation did not preserve ALL mode")
        finally:
            setter(original)
        if dialog._anchor_after is not None:
            top.after_cancel(dialog._anchor_after)
        top.withdraw()
        lines.append("TCL_SETTINGS_PRESENT_OK")
        return lines
    finally:
        setter(original)
        root.destroy()


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
        user32 = ctypes.windll.user32
        user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        user32.AreDpiAwarenessContextsEqual.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        user32.AreDpiAwarenessContextsEqual.restype = wintypes.BOOL
        awareness = user32.GetThreadDpiAwarenessContext()
        if not user32.AreDpiAwarenessContextsEqual(awareness, ctypes.c_void_p(-4)):
            raise AssertionError("PerMonitorV2 DPI awareness inactive; manifest missing?")
        check("PerMonitorV2 DPI awareness active from embedded manifest")
        _assert_single_system_ucrt()
        check("single system UCRT loaded (no bundled ucrtbase/api-ms)")
        for native_check in _check_tcl_service_guard(tkinter):
            check(native_check)
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
        _configure_isolated_fatal_logging()
        storage = Storage()
        monitor = UsageMonitor(storage)
        app = MonitorUI(monitor, storage)
        app.run()
    finally:
        instance.release()


if __name__ == "__main__":
    main()
