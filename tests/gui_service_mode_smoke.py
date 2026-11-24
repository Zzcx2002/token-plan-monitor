"""Regression for the packaged settings-window abort (BEX64 / 0xc0000409).

Faithful replay of the field crash condition:

  * a Python command is on the stack, so Tcl's per-thread ``tcl_tstate`` is
    NULL;
  * the notifier is forced to TCL_SERVICE_ALL -- the mode used by Tk's
    native modal loops, including size/move;
  * an after-idle Python callback is queued;
  * production native boundaries (subclass install, WNDPROC forwarding,
    material apply, FRAMECHANGED) synchronously pump window messages.

Without the NONE guards in ui_graphics/window_chrome, Tk's WndProc then runs
Tcl_ServiceAll() -> the queued Python command -> ENTER_PYTHON ->
PyEval_RestoreThread(NULL) -> Py_FatalError, and the process aborts before
reaching the PASS line. With the guards, every boundary survives, the
ambient ALL mode is restored after each call, queued idle work is not lost,
and the idle callback fires later in the normal event loop.
"""
import ctypes
import sys
import tempfile
from pathlib import Path

if sys.platform != "win32":
    print("SKIP: Windows Tcl notifier service-mode regression")
    sys.exit(0)

import tkinter as tk

from gui_preview import EmptyKeyring, FakeMonitor
from token_plan_monitor.appearance import LIGHT
from token_plan_monitor.storage import Storage
from token_plan_monitor.ui import MonitorUI
from token_plan_monitor.ui_graphics import (
    TCL_SERVICE_NONE,
    _SuggestedRect,
    _tcl_service_mode_functions,
    apply_window_effects,
    ensure_tool_window_style,
    install_dpi_handler,
    native_handle,
    tcl_events_deferred,
)
from token_plan_monitor.window_chrome import WindowChrome

TCL_SERVICE_ALL = 1
WM_NULL = 0x0000
WM_DPICHANGED = 0x02E0
SWP_FRAMECHANGED_FLAGS = 0x37


def main() -> None:
    setter, getter = _tcl_service_mode_functions()
    assert setter is not None and getter is not None, "tcl86 service-mode exports missing"
    original_mode = getter()

    temporary = tempfile.TemporaryDirectory(prefix="service-mode-settings-")
    app = MonitorUI(FakeMonitor(), Storage(Path(temporary.name), EmptyKeyring()))
    root = app.root
    root.withdraw()
    errors = []
    root.report_callback_exception = lambda *error: errors.append(error)
    try:
        top = tk.Toplevel(root)
        top.geometry("420x320+80+80")
        top.title("service-mode-regression")
        top.update_idletasks()
        # Production framed window: subclass install already ran under guard.
        chrome = WindowChrome(top, "regression", LIGHT.colors, "Segoe UI", top.withdraw)

        card = tk.Toplevel(root)
        card.overrideredirect(True)
        card.geometry("240x120+40+40")
        card.update_idletasks()
        dpi_events = []
        install_dpi_handler(card, dpi_events.append)
        card_hwnd = native_handle(card)

        # Reproduce the exact field condition: Python phase + ambient ALL.
        assert setter(TCL_SERVICE_ALL) == original_mode
        try:
            assert getter() == TCL_SERVICE_ALL

            # Material apply runs under ALL; it also uses update_idletasks,
            # whose TclServiceIdle path is a safe explicit-Tkinter reentrancy,
            # before the Python sentinel below gets queued.
            apply_window_effects(top, LIGHT.colors["surface"], material="solid")
            assert getter() == TCL_SERVICE_ALL, "guard must restore the ambient mode"

            # Queue the Python sentinel and keep every later call free of
            # update_idletasks: only the guarded WndProc Tcl_ServiceAll path
            # could now service it, which must stay a no-op under NONE.
            marker = {"idle": False}
            root.after_idle(lambda: marker.__setitem__("idle", True))

            # override-redirect taskbar style re-assertion (SetWindowPos 0x27).
            ensure_tool_window_style(card)
            assert getter() == TCL_SERVICE_ALL

            # Idle region recompute (SetWindowRgn/SetWindowPos path).
            chrome.last_region = None
            chrome._region()
            assert getter() == TCL_SERVICE_ALL

            # Generic message through the subclassed WNDPROC into Tk's proc.
            assert chrome._wndproc(chrome.hwnd, WM_NULL, 0, 0) == 0
            assert getter() == TCL_SERVICE_ALL

            # WM_DPICHANGED takes the SetWindowPos + after_idle branch.
            suggested = _SuggestedRect(60, 60, 460, 360)
            lparam = ctypes.cast(ctypes.byref(suggested), ctypes.c_void_p).value
            chrome._wndproc(chrome.hwnd, WM_DPICHANGED, 96 | (96 << 16), lparam)
            assert getter() == TCL_SERVICE_ALL

            # The card's lightweight DPI WNDPROC, same forwarding contract.
            assert card._dpi_handler is not None
            card._dpi_handler["proc"](card_hwnd, WM_NULL, 0, 0)
            assert getter() == TCL_SERVICE_ALL

            # The bare field-repro call: raw FRAMECHANGED inside the CM guard.
            user32 = ctypes.windll.user32
            user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                            ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                            ctypes.c_int, ctypes.c_uint]
            with tcl_events_deferred():
                user32.SetWindowPos(chrome.hwnd, None, 0, 0, 0, 0,
                                    SWP_FRAMECHANGED_FLAGS)
            assert getter() == TCL_SERVICE_ALL

            # Real Settings construction, fit and presentation must provide the
            # same protection. Drive fit/present synchronously so the original
            # Python sentinel is still pending at every native boundary.
            marker["settings_timer"] = False
            marker["settings_timer_modes"] = []
            root.after(0, lambda: (
                marker.__setitem__("settings_timer", True),
                marker["settings_timer_modes"].append(getter()),
            ))
            app.open_settings()
            dialog = app._settings_dialog
            assert dialog is not None and dialog.window is not None
            settings_window = dialog.window
            for method_name in ("deiconify", "lift", "focus_force"):
                method = getattr(settings_window, method_name)
                def checked(*args, _method=method, **kwargs):
                    assert getter() == TCL_SERVICE_NONE, "Settings native call left guard"
                    return _method(*args, **kwargs)
                setattr(settings_window, method_name, checked)
            dialog._fit()
            # update_idletasks explicitly services due Tcl work even under NONE;
            # the low-level WndProc assertions above prove nested native pumping
            # stays blocked. Here, verify every Settings native call is guarded.
            assert getter() == TCL_SERVICE_ALL
            dialog._present()
            assert getter() == TCL_SERVICE_ALL
            assert dialog.window.winfo_ismapped()

            # The explicit update_idletasks in Settings may have serviced the
            # idle marker safely; ambient ALL must still be restored afterward.
            assert getter() == TCL_SERVICE_ALL
        finally:
            setter(original_mode)

        # Back in the normal NONE-mode loop the deferred idle work, final anchor
        # and the 350ms geometry-persistence callback must all complete.
        root.after(1200, root.quit)
        root.mainloop()
        assert marker["idle"] is True, "deferred after-idle callback was lost"
        assert marker["settings_timer"] is True, "deferred timer callback was lost"
        assert marker["settings_timer_modes"] and all(
            mode == TCL_SERVICE_NONE for mode in marker["settings_timer_modes"]
        )
        assert dialog._presented and dialog.body.canvas.yview()[0] == 0.0
        dialog.close()
        root.after(400, root.quit)
        root.mainloop()
        assert app.settings_window is None
        assert dialog.window.winfo_exists() and not dialog.window.winfo_viewable()

        # Retained presentation must preserve the exact same native guards.
        assert setter(TCL_SERVICE_ALL) == original_mode
        try:
            app.open_settings()
            assert getter() == TCL_SERVICE_ALL
            dialog._present()
            assert getter() == TCL_SERVICE_ALL
            assert dialog.window.winfo_ismapped()
        finally:
            setter(original_mode)
        root.after(400, root.quit)
        root.mainloop()
        assert dialog._presented and dialog.body.canvas.yview()[0] == 0.0
        dialog.close()
        assert app.settings_window is None
        assert not errors, errors
    finally:
        app.shutdown()
        temporary.cleanup()
    print("PASS service-mode: Settings fit/present/flush survived ALL; idle and mode preserved")


if __name__ == "__main__":
    main()
