"""Exercise isolated Windows UI behaviour without touching the running monitor."""
import ctypes
import tempfile
from pathlib import Path
from tkinter import ttk
from types import SimpleNamespace

from gui_preview import ARTIFACTS, FakeMonitor, snapshot
from token_plan_monitor.models import MonitorViewState
from token_plan_monitor.storage import Storage
from token_plan_monitor.ui import MonitorUI
from token_plan_monitor import ui as ui_module
from token_plan_monitor.ui_graphics import _ensure_shadow, native_handle


class MemoryKeyring:
    def __init__(self):
        self.values = {}

    def get_password(self, service, username):
        return self.values.get((service, username))

    def set_password(self, service, username, value):
        self.values[service, username] = value


class Monitor(FakeMonitor):
    def update_configuration(self, settings, credentials):
        self.saved = (settings, credentials)


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def main():
    with tempfile.TemporaryDirectory(dir=ARTIFACTS) as temporary:
        monitor = Monitor()
        app = MonitorUI(monitor, Storage(Path(temporary), MemoryKeyring()))
        try:
            app._create_popup()
            app._create_desktop_widget()
            state = MonitorViewState(status='ok', snapshot=snapshot('agent_plan'))
            app._state = monitor.state = state
            app._render_state(state)
            card = app.desktop_widget
            app.root.after(80, app.root.quit)
            app.root.mainloop()
            opened = []
            card.on_open = lambda: opened.append(True)
            x, y = card.window.winfo_x(), card.window.winfo_y()
            card._on_press(SimpleNamespace(x_root=x+40, y_root=y+40))
            card._on_release(SimpleNamespace())
            assert len(opened) == 1
            card._on_press(SimpleNamespace(x_root=x+40, y_root=y+40))
            card._on_drag(SimpleNamespace(x_root=x+10, y_root=y+10))
            app.root.after(80, app.root.quit)
            app.root.mainloop()
            card._on_release(SimpleNamespace())
            assert len(opened) == 1, 'Dragging must not open the popup'
            assert app.storage.load_widget_preferences().x == card.window.winfo_x()
            card.toggle_topmost()
            app.root.after(80, app.root.quit)
            app.root.mainloop()
            assert not card.topmost
            get_style = ctypes.windll.user32.GetWindowLongW
            get_style.argtypes = [ctypes.c_void_p, ctypes.c_int]
            assert not get_style(native_handle(card.window), -20) & 8
            card._show_hover()
            app.root.after(80, app.root.quit)
            app.root.mainloop()
            assert card._hover.winfo_viewable()
            card.set_visible(False)
            app.root.after(80, app.root.quit)
            app.root.mainloop()
            assert not card.window.winfo_viewable() and not card._hover.winfo_viewable()
            card.set_visible(True)
            app.root.after(80, app.root.quit)
            app.root.mainloop()
            assert card.window.winfo_viewable()

            _ensure_shadow(card.window)  # Force the fallback even on DWM-capable hosts.
            app.root.after(80, app.root.quit)
            app.root.mainloop()
            shadow = card.window._material_shadow
            assert shadow.window.winfo_viewable()
            style = get_style(native_handle(shadow.window), -20)
            assert style & 0x20 and style & 0x08000000, 'Shadow must not accept mouse/focus'
            card.set_visible(False)
            app.root.after(80, app.root.quit)
            app.root.mainloop()
            assert not shadow.window.winfo_viewable()
            card.set_visible(True)
            app.root.after(80, app.root.quit)
            app.root.mainloop()
            assert shadow.window.winfo_viewable()

            app.show_popup()
            app.root.after(80, app.root.quit)
            app.root.mainloop()
            app.popup.event_generate('<Escape>')
            app.root.after(80, app.root.quit)
            app.root.mainloop()
            assert not app.popup.winfo_viewable()
            app.open_settings()
            app.root.after(80, app.root.quit)
            app.root.mainloop()
            buttons = [w for w in descendants(app.settings_window) if isinstance(w, ttk.Button)]
            save = next(w for w in buttons if w.cget('text') == '保存设置')
            errors = []
            original = ui_module.messagebox.showerror
            ui_module.messagebox.showerror = lambda *args, **kwargs: errors.append(args)
            try:
                save.invoke()
            finally:
                ui_module.messagebox.showerror = original
            assert errors and app.settings_window.winfo_exists()
            entries = [w for w in descendants(app.settings_window) if isinstance(w, ttk.Entry)]
            entries[0].insert(0, 'offline-test-access-key')
            entries[1].insert(0, 'offline-test-secret')
            save.invoke()
            app.root.after(80, app.root.quit)
            app.root.mainloop()
            assert app.settings_window is None
            assert monitor.saved[0].refresh_minutes == 15
            assert app.storage.load_credentials().access_key_id == 'offline-test-access-key'
            app.open_settings()
            app.root.after(80, app.root.quit)
            app.root.mainloop()
            cancel = next(w for w in descendants(app.settings_window)
                          if isinstance(w, ttk.Button) and w.cget('text') == '取消')
            cancel.invoke()
            assert app.settings_window is None
            card.close()
            app.root.after(80, app.root.quit)
            app.root.mainloop()
            assert shadow._closed
            print('PASS: click, drag, persistence, topmost, hover, hide/restore, shadow lifecycle, Escape, validation, save, cancel')
        finally:
            app.shutdown()


if __name__ == '__main__':
    main()
