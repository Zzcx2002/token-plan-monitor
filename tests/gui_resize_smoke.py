"""Offline mouse-resize, minimum-layout and restart checks at three DPI scales."""
import ctypes
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import gui_smoke
from gui_preview import ARTIFACTS, EmptyKeyring, FakeMonitor, scenarios
from gui_theme_preview import assert_layout, capture_screen, pump
from token_plan_monitor.appearance import AppearancePreferences
from token_plan_monitor.storage import Storage
from token_plan_monitor.ui import MonitorUI
from token_plan_monitor.ui_graphics import dpi_scale


def run(mode, scale):
    with tempfile.TemporaryDirectory(dir=ARTIFACTS) as temporary:
        storage = Storage(Path(temporary), EmptyKeyring())
        storage.save_appearance(AppearancePreferences(mode))
        app = MonitorUI(FakeMonitor(), storage)
        app.root.tk.call("tk", "scaling", (96/72)*scale)
        app._configure_style()
        failures = []
        app.root.report_callback_exception = lambda *error: failures.append(error)
        try:
            app._create_popup()
            app._create_desktop_widget()
            app._state = scenarios()[0][1]
            app._render_state(app._state)
            app.show_popup()
            pump(app)
            factor = dpi_scale(app.popup)
            for width, height in ((360, 300), (640, 650)):
                app.popup.geometry(f"{round(width*factor)}x{round(height*factor)}+40+40")
                pump(app)
                assert_layout(app.popup, app.popup_body)
                size = app.popup.winfo_width(), app.popup.winfo_height()
                app._toggle_details()
                app._render_state(app._state)
                app._set_theme("dark" if mode == "light" else "light")
                pump(app)
                assert size == (app.popup.winfo_width(), app.popup.winfo_height())
            app.open_settings()
            pump(app)
            app.settings_window.geometry(f"{round(400*factor)}x{round(350*factor)}+40+40")
            pump(app)
            assert_layout(app.settings_window, app.settings_body)
            app._close_settings(app.settings_window)
            card = app.desktop_widget
            card.on_open = Mock()
            x, y = card.window.winfo_rootx(), card.window.winfo_rooty()
            w, h = card._size
            press = SimpleNamespace(x_root=x+w-2, y_root=y+h-2, widget=card.card)
            card._on_press(press)
            card._on_drag(SimpleNamespace(x_root=x+w+round(120*factor),
                                          y_root=y+h+round(60*factor)))
            pump(app)
            card._on_release(press)
            assert not card.on_open.called
            enlarged = card._size
            card.render(app._state)
            pump(app)
            assert card._size == enlarged
            assert not hasattr(card.window, "_material_shadow")
            for width, height in ((280, 124), (520, 240)):
                card.window.geometry(f"{round(width*factor)}x{round(height*factor)}+700+40")
                pump(app)
                for item in card.card.find_all():
                    if card.card.type(item) == "text":
                        x1, y1, x2, y2 = card.card.bbox(item)
                        assert min(x1, y1) >= 0
                        assert x2 <= card.card.winfo_width() and y2 <= card.card.winfo_height()
            card.geometry.flush()
            app.popup_geometry.flush()
            expected = storage.load_window_size("widget")
            if scale == 1:
                app.popup.withdraw()
                capture_screen(card.card, f"resize-{mode}-widget.png")
            assert not failures, failures
        finally:
            app.shutdown()
        restarted = MonitorUI(FakeMonitor(), storage)
        restarted.root.tk.call("tk", "scaling", (96/72)*scale)
        try:
            restarted._create_desktop_widget()
            pump(restarted)
            card = restarted.desktop_widget
            actual = tuple(n/dpi_scale(card.window) for n in card._size)
            assert all(abs(a-b) < 2 for a, b in zip(actual, expected)), (actual, expected)
        finally:
            restarted.shutdown()
        print(f"PASS resize {mode} {scale:.0%}: minimum, mouse, refresh, theme, persistence")


if __name__ == "__main__":
    ARTIFACTS.mkdir(exist_ok=True)
    gui_smoke.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    for mode in ("light", "dark"):
        for scale in (1.0, 1.5, 2.0):
            run(mode, scale)
