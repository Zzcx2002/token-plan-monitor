"""Simulate WM_DPICHANGED across monitors and verify the whole UI re-scales.

Two subclass paths are exercised:
* the framed popup (WindowChrome WNDPROC) -> app._on_dpi_changed, which moves
  Tk's process-global scaling and rebuilds every window's assets;
* the override-redirect desktop card (install_dpi_handler) -> its own
  on_scale_changed callback, which must re-scale even without a chrome.
"""
import ctypes
from ctypes import wintypes as wt
import tempfile
import tkinter.font as tkfont
from pathlib import Path
from tkinter import ttk

import gui_smoke
from gui_preview import ARTIFACTS, EmptyKeyring, FakeMonitor, snapshot
from gui_theme_preview import assert_layout, capture_screen, pump
from token_plan_monitor.appearance import RADIUS
from token_plan_monitor.desktop_widget import refresh_button_hit_bounds
from token_plan_monitor.models import MonitorViewState
from token_plan_monitor.storage import Storage
from token_plan_monitor.ui import MonitorUI
from token_plan_monitor.ui_graphics import dpi_scale, native_handle

user32 = ctypes.windll.user32
WM_DPICHANGED = 0x02E0


def window_rect(hwnd):
    rect = wt.RECT()
    user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect


def send_dpi_change(hwnd, dpi):
    rect = window_rect(hwnd)
    user32.SendMessageW.argtypes = [
        ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    user32.SendMessageW(
        hwnd, WM_DPICHANGED,
        ctypes.c_void_p(dpi | (dpi << 16)),
        ctypes.byref(rect),
    )


def main():
    ARTIFACTS.mkdir(exist_ok=True)
    gui_smoke.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    with tempfile.TemporaryDirectory(dir=ARTIFACTS) as temporary:
        storage = Storage(Path(temporary), EmptyKeyring())
        monitor = FakeMonitor()
        app = MonitorUI(monitor, storage)
        errors = []
        app.root.report_callback_exception = lambda *error: errors.append(error)
        try:
            app._create_popup()
            app._create_desktop_widget()
            state = MonitorViewState(status="ok", snapshot=snapshot("agent_plan"))
            monitor.state = app._state = state
            app._render_state(state)
            app.show_popup()
            app.desktop_widget.set_visible(True)
            pump(app)
            popup = app.popup
            card = app.desktop_widget
            popup_hwnd = native_handle(popup)
            card_hwnd = native_handle(card.window)

            # --- 96 -> 144 DPI through the framed popup's chrome WNDPROC ---
            send_dpi_change(popup_hwnd, 144)
            pump(app)
            assert not errors, errors
            assert app._dpi == 144
            # Tk normalizes tk scaling through system font metrics on write, so
            # the readback drifts slightly; pixel-rounded assets stay exact.
            assert abs(float(app.root.tk.call("tk", "scaling")) - 144 / 72) < 0.02
            assert abs(dpi_scale(popup) - 1.5) < 0.01
            assert card.card.scale == 1.5
            chrome_button = popup._chrome.buttons["close"]
            assert (chrome_button.winfo_width(),
                    chrome_button.winfo_height()) == (42, 42)
            assert app.quota_panel.radius == round(RADIUS["panel"] * 1.5)
            assert_layout(popup, app.popup_body)
            assert popup._window_effects.material == "solid"
            # Scrollbar width follows the 150% scale; detail text stays 9pt.
            style = ttk.Style(app.root)
            assert int(style.lookup("Vertical.TScrollbar", "width")) == 15
            detail_font = tkfont.Font(font=style.lookup("DetailMuted.TLabel", "font"))
            assert detail_font.actual("size") == 9
            # The 20 DIP refresh hit box becomes 30 physical px at 150%.
            hit = refresh_button_hit_bounds(
                card.card.winfo_width(), card.card.winfo_height(), card.card.scale)
            assert (hit[2] - hit[0], hit[3] - hit[1]) == (30, 30)
            capture_screen(popup, "dpi-150-popup.png")
            capture_screen(card.window, "dpi-150-widget.png")

            # --- 144 -> 96 DPI restores the original pixel geometry ---
            send_dpi_change(popup_hwnd, 96)
            pump(app)
            assert not errors, errors
            assert app._dpi == 96 and abs(dpi_scale(popup) - 1.0) < 0.01
            assert card.card.scale == 1.0
            assert (chrome_button.winfo_width(),
                    chrome_button.winfo_height()) == (28, 28)
            assert app.quota_panel.radius == RADIUS["panel"]
            assert_layout(popup, app.popup_body)
            # Back at 96 DPI the scrollbar narrows and the hit box is 20 px.
            style = ttk.Style(app.root)
            assert int(style.lookup("Vertical.TScrollbar", "width")) == 10
            hit = refresh_button_hit_bounds(
                card.card.winfo_width(), card.card.winfo_height(), card.card.scale)
            assert (hit[2] - hit[0], hit[3] - hit[1]) == (20, 20)

            # --- override-redirect card handles the message on its own ---
            send_dpi_change(card_hwnd, 144)
            pump(app)
            assert not errors, errors
            # The card path takes the DPI from the message, not global scaling.
            assert card.card.scale == 1.5 and app._dpi == 96
            assert card.window._window_effects.material == "solid"
            send_dpi_change(card_hwnd, 96)
            pump(app)
            assert card.card.scale == 1.0
            assert not errors, errors
            print("PASS dpi: WM_DPICHANGED 96->144->96 on chrome and card paths, "
                  "scaling, radii, chrome buttons, layout and material all re-apply")
        finally:
            app.shutdown()


if __name__ == "__main__":
    main()
