"""Exercise isolated Windows UI behaviour without touching the running monitor."""
import ctypes
import tempfile
from pathlib import Path
from dataclasses import replace
from tkinter import ttk
from types import SimpleNamespace
from unittest.mock import patch

from gui_preview import ARTIFACTS, FakeMonitor, snapshot
from token_plan_monitor.models import MonitorViewState
from token_plan_monitor.storage import Storage
from token_plan_monitor.ui import MonitorUI
from token_plan_monitor.ui_graphics import native_handle


class MemoryKeyring:
    def __init__(self):
        self.values = {}
        self.writes = 0

    def get_password(self, service, username):
        return self.values.get((service, username))

    def set_password(self, service, username, value):
        self.values[service, username] = value
        self.writes += 1


class Monitor(FakeMonitor):
    def __init__(self):
        super().__init__()
        self.configuration_updates=0
        self.refreshes=0

    def request_refresh(self):
        self.refreshes+=1

    def update_configuration(self, settings, credentials):
        self.saved = (settings, credentials)
        self.configuration_updates += 1


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
            app.storage.save_snapshot(state.snapshot)
            cache_before=app.storage.snapshot_path.read_bytes()
            initial_card=card.window
            initial_popup=app.popup
            initial_position=(card.window.winfo_x(),card.window.winfo_y())
            app._toggle_details()
            app._render_state(replace(state,is_refreshing=True))
            app._render_state(state)
            assert app._details_expanded
            app._set_theme("dark",persist=True)
            app.root.after(80,app.root.quit)
            app.root.mainloop()
            assert app.theme.name=="dark" and app._details_expanded
            assert app.popup is initial_popup and card.window is initial_card
            assert (card.window.winfo_x(),card.window.winfo_y())==initial_position
            assert app._state.snapshot is state.snapshot
            assert app.storage.snapshot_path.read_bytes()==cache_before
            assert monitor.refreshes==0 and monitor.configuration_updates==0
            app.open_settings()
            app.root.after(80,app.root.quit)
            app.root.mainloop()
            light=next(w for w in descendants(app.settings_window)
                       if isinstance(w,ttk.Radiobutton) and w.cget("text")=="浅色")
            light.invoke()
            assert app.theme.name=="light"
            assert app.storage.load_appearance().theme=="dark"
            cancel=next(w for w in descendants(app.settings_window)
                        if isinstance(w,ttk.Button) and w.cget("text")=="取消")
            cancel.invoke()
            assert app.theme_mode=="dark" and app.theme.name=="dark"
            assert app._details_expanded
            with patch("token_plan_monitor.appearance.system_theme",return_value="light"):
                app._set_theme("system",persist=True)
                assert app.theme.name=="light"
                with patch("token_plan_monitor.appearance.system_theme",return_value="dark"):
                    app.root.after_cancel(app._theme_poll_after)
                    app._poll_theme()
                    assert app.theme.name=="dark" and app.theme_mode=="system"
            assert monitor.refreshes==0 and monitor.configuration_updates==0
            app._set_theme("light",persist=True)
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

            assert not hasattr(card.window,"_material_shadow")
            assert not card.window._window_effects.shadow

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
            save.invoke()
            app.root.after(80,app.root.quit)
            app.root.mainloop()
            assert app._settings_entries["access_key"].instate(["invalid"])
            assert app._settings_errors["access_key"].cget("text")
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
            updates=monitor.configuration_updates
            writes=app.storage._keyring.writes
            app.open_settings()
            app.root.after(80,app.root.quit)
            app.root.mainloop()
            dark=next(w for w in descendants(app.settings_window)
                      if isinstance(w,ttk.Radiobutton) and w.cget("text")=="深色")
            dark.invoke()
            save=next(w for w in descendants(app.settings_window)
                      if isinstance(w,ttk.Button) and w.cget("text")=="保存设置")
            save.invoke()
            assert app.settings_window is None
            assert app.storage.load_appearance().theme=="dark"
            assert monitor.configuration_updates==updates and monitor.refreshes==0
            assert app.storage._keyring.writes==writes
            assert app._details_expanded
            card.close()
            app.root.after(80, app.root.quit)
            app.root.mainloop()
            print('PASS: theme switching, system changes, persistence, rollback, no quota/keyring writes, details retention, click, drag, topmost, hover, no outer shadow, Escape, validation, save, cancel')
        finally:
            app.shutdown()
        restarted=MonitorUI(Monitor(),Storage(Path(temporary),MemoryKeyring()))
        try:
            assert restarted.theme_mode=="dark" and restarted.theme.name=="dark"
        finally:
            restarted.shutdown()


if __name__ == '__main__':
    main()
