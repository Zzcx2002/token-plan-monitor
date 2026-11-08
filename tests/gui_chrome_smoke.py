"""Exercise the real outer HWND, including frame, sizing and taskbar states."""
import ctypes
from ctypes import wintypes as wt
import tempfile
from pathlib import Path

from PIL import ImageGrab
import gui_smoke
from gui_preview import ARTIFACTS, EmptyKeyring, FakeMonitor, scenarios
from gui_theme_preview import assert_layout, pump
from token_plan_monitor.appearance import AppearancePreferences
from token_plan_monitor.storage import Storage
from token_plan_monitor.ui import MonitorUI
from token_plan_monitor.ui_graphics import dpi_scale, window_work_area


def outer_bounds(chrome):
    rect = wt.RECT()
    chrome.user.GetWindowRect(chrome.hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def check_frame(window):
    chrome = window._chrome
    rect = wt.RECT()
    point = wt.POINT(0, 0)
    chrome.user.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
    chrome.user.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
    chrome.user.GetClientRect(chrome.hwnd, ctypes.byref(rect))
    chrome.user.ClientToScreen(chrome.hwnd, ctypes.byref(point))
    left, top, right, bottom = outer_bounds(chrome)
    assert (point.x, point.y, rect.right, rect.bottom) == (left, top, right-left, bottom-top), (
        "Windows non-client frame remains", (point.x, point.y, rect.right, rect.bottom), (left, top, right, bottom))
    width, height = right-left, bottom-top
    positions = ((1, 1, 13), (width//2, 1, 12), (width-2, 1, 14),
                 (1, height//2, 10), (width-2, height//2, 11),
                 (1, height-2, 16), (width//2, height-2, 15), (width-2, height-2, 17))
    for x, y, expected in positions:
        lparam = ((top+y) & 0xffff) << 16 | ((left+x) & 0xffff)
        assert chrome.user.SendMessageW(chrome.hwnd, 0x84, 0, lparam) == expected
    assert window.winfo_manager() == "wm" and not window.overrideredirect()
    assert chrome.title.bind("<Button-1>")
    assert chrome.header.bind("<Double-Button-1>")
    assert not chrome.buttons["close"].bind("<Double-Button-1>")


def run(mode, scale):
    with tempfile.TemporaryDirectory(dir=ARTIFACTS) as temporary:
        storage = Storage(Path(temporary), EmptyKeyring())
        storage.save_appearance(AppearancePreferences(mode))
        app = MonitorUI(FakeMonitor(), storage)
        app.root.tk.call("tk", "scaling", (96/72)*scale)
        app._configure_style()
        errors = []
        app.root.report_callback_exception = lambda *error: errors.append(error)
        try:
            app._create_popup()
            app._state = scenarios()[0][1]
            app._render_state(app._state)
            app.show_popup()
            pump(app)
            app.open_settings()
            pump(app)
            for name, window, body in (("popup", app.popup, app.popup_body),
                                       ("settings", app.settings_window, app.settings_body)):
                window.geometry("+40+40")
                window.lift()
                pump(app)
                chrome = window._chrome
                check_frame(window)
                assert_layout(window, body)
                if scale == 1:
                    ImageGrab.grab(bbox=outer_bounds(chrome), all_screens=True).save(
                        ARTIFACTS/f"chrome-{mode}-{name}-full.png")
                for width, height in ((360 if name == "popup" else 400, 320), (600, 680)):
                    factor = dpi_scale(window)
                    window.geometry(f"{round(width*factor)}x{round(height*factor)}")
                    pump(app)
                    assert_layout(window, body)
                    check_frame(window)
                controller = window._geometry
                controller.flush()
                restored_size = storage.load_window_size(name)
                normal = window.winfo_width(), window.winfo_height()
                chrome.toggle_maximize()
                pump(app)
                assert chrome.user.IsZoomed(chrome.hwnd)
                assert outer_bounds(chrome) == window_work_area(window), (outer_bounds(chrome),window_work_area(window))
                controller.flush()
                assert storage.load_window_size(name) == restored_size
                chrome.toggle_maximize()
                pump(app)
                assert normal == (window.winfo_width(), window.winfo_height())
                chrome.minimize()
                pump(app)
                assert chrome.user.IsIconic(chrome.hwnd)
                controller.flush()
                assert storage.load_window_size(name) == restored_size
                window.deiconify()
                pump(app)
                check_frame(window)
                assert chrome.normal
            app._set_theme("light" if mode == "dark" else "dark")
            app.settings_window._chrome.close()
            assert app.settings_window is None
            assert app.theme_mode == mode
            app.popup._chrome.close()
            pump(app)
            assert not app.popup.winfo_viewable()
            app.show_popup()
            pump(app)
            check_frame(app.popup)
            assert not errors, errors
        finally:
            app.shutdown()
        print(f"PASS chrome {mode} {scale:.0%}: full HWND, 8 edges, maximize, minimize, close, restore")


if __name__ == "__main__":
    ARTIFACTS.mkdir(exist_ok=True)
    gui_smoke.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    for mode in ("light", "dark"):
        for scale in (1.0, 1.5, 2.0):
            run(mode, scale)
