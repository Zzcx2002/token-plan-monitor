"""Geometry stability for the scrollable settings dialog.

Regression coverage for the feedback loop where showing/hiding the scrollbar
changed the canvas width, which re-wrapped labels and fed back into the
scrollbar decision until the content collapsed. The bar now keeps a fixed grid
column and toggles only its disabled state, so canvas geometry must never move
when the overflow decision flips.

Run directly: .venv/Scripts/python tests/gui_geometry_smoke.py
"""
import ctypes
import tempfile
import tkinter as tk
from pathlib import Path

from gui_preview import ARTIFACTS, FakeMonitor
from token_plan_monitor.models import Settings
from token_plan_monitor.providers import get_provider
from token_plan_monitor.storage import Storage
from token_plan_monitor.ui import MonitorUI
from token_plan_monitor.ui_graphics import ScrollableBody
from token_plan_monitor.window_geometry import WindowGeometry

import gui_smoke


class MemoryKeyring:
    def __init__(self):
        self.values = {}

    def get_password(self, service, username):
        return self.values.get((service, username))

    def set_password(self, service, username, value):
        self.values[(service, username)] = value


def pump(app, ms=100):
    app.root.after(ms, app.root.quit)
    app.root.mainloop()


def build_app(temporary, seed_height=None):
    storage = Storage(Path(temporary), MemoryKeyring())
    storage.save_settings(Settings(provider="volcengine", region="cn-beijing"))
    if seed_height is not None:
        storage.save_window_size("settings", (592.95, seed_height))
    app = MonitorUI(FakeMonitor(), storage)
    app._create_popup()
    app._create_desktop_widget()
    app.open_settings()
    pump(app, 300)
    return app


def fixed_column_invariant(app):
    """Canvas width is identical with the bar disabled and enabled."""
    top = tk.Toplevel(app.root)
    try:
        body = ScrollableBody(top, app.colors)
        body.pack(fill="both", expand=True)
        tk.Label(body.content, text="short content").pack(anchor="w")
        pump(app, 120)
        body.fit(600)
        pump(app, 120)
        assert "disabled" in body.bar.state(), body.bar.state()
        width_short = body.canvas.winfo_width()
        assert width_short > 100

        tall = tk.Frame(body.content, height=1200, width=200, bg="red")
        tall.pack_propagate(False)
        tall.pack(fill="x")
        pump(app, 120)
        body.fit(600)
        pump(app, 120)
        assert "disabled" not in body.bar.state(), body.bar.state()
        assert body.bar.winfo_ismapped(), "bar must stay mapped in both states"
        assert body.canvas.winfo_width() == width_short, (
            body.canvas.winfo_width(), width_short)
    finally:
        top.destroy()
        pump(app, 80)


def late_parent_growth_invariant(app):
    """A label skipped in a narrow panel is revisited when that panel grows."""
    top = tk.Toplevel(app.root)
    top.geometry("640x480")
    try:
        body = ScrollableBody(top, app.colors)
        body.pack(fill="both", expand=True)
        panel = tk.Frame(body.content, width=40, height=80)
        panel.pack_propagate(False)
        panel.pack(anchor="w")
        label = tk.Label(panel, text="late parent growth", wraplength=40)
        label.pack(fill="x")
        pump(app, 200)
        assert panel.winfo_width() < 100
        assert int(label.cget("wraplength")) == 40
        canvas_width = body.canvas.winfo_width()

        panel.configure(width=320)
        pump(app, 200)
        assert body.canvas.winfo_width() == canvas_width
        assert int(label.cget("wraplength")) > 200, label.cget("wraplength")
    finally:
        top.destroy()
        pump(app, 80)


def main():
    gui_smoke.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    with tempfile.TemporaryDirectory(dir=ARTIFACTS) as seeded_dir, \
            tempfile.TemporaryDirectory(dir=ARTIFACTS) as fresh_dir:
        app = build_app(seeded_dir, seed_height=1048.26)
        try:
            dialog = app._settings_dialog
            window, body = app.settings_window, dialog.body

            # The dialog opens fully rendered, never collapsed to a strip.
            assert body.content.winfo_ismapped()
            assert body.content.winfo_width() > 300
            assert body.content.winfo_reqheight() > 500
            assert body.bar.winfo_ismapped()
            assert "disabled" not in body.bar.state()

            # Initial layout starts at the top instead of being scrolled away.
            assert body.canvas.yview()[0] == 0.0, body.canvas.yview()

            fixed_column_invariant(app)
            late_parent_growth_invariant(app)

            # A provider rebuild must not move the canvas width any more.
            width_before = body.canvas.winfo_width()
            dialog.provider_var.set(get_provider("bailian").label)
            dialog._schedule_rebuild()
            pump(app, 300)
            assert body.canvas.winfo_width() == width_before, (
                body.canvas.winfo_width(), width_before)
            assert body.bar.winfo_ismapped()

            # Nested labels can need one more wrap pass after a provider
            # rebuild. Once that finite settling is done, geometry must stay
            # at a single fixed point for the full three-second watch.
            pump(app, 500)
            states = set()
            for _ in range(30):
                pump(app, 100)
                states.add((
                    window.winfo_width(), window.winfo_height(),
                    body.canvas.winfo_width(), body.canvas.winfo_height(),
                    body.content.winfo_width(), body.content.winfo_reqheight(),
                    body.bar.winfo_ismapped(), tuple(body.bar.state()),
                ))
            assert len(states) == 1, sorted(states)
            # Reopening the retained shell must preserve the fixed point instead
            # of restarting fit, rasterization and persisted-height drift.
            settled_height = window.winfo_height()
            dialog.close()
            pump(app, 100)
            assert app.settings_window is None and not window.winfo_viewable()
            app.open_settings()
            pump(app, 300)
            assert app.settings_window is window and app.settings_body is body
            assert app._settings_dialog is dialog
            assert body.canvas.yview()[0] == 0.0
            assert window.winfo_height() == settled_height
            seeded_height = window.winfo_height()
        finally:
            app.shutdown()

        # The persisted oversized height must not reopen an empty tall dialog:
        # seeded and history-free windows must fit to the same height.
        app = build_app(fresh_dir)
        try:
            fresh_height = app.settings_window.winfo_height()
            assert fresh_height == seeded_height, (fresh_height, seeded_height)
            assert app._settings_dialog.body.content.winfo_width() > 300
        finally:
            app.shutdown()

    print("PASS geometry: fixed scrollbar column, late-parent wrap, no rebuild "
          "width jump, single fixed point, top-anchored scroll, height clamp")


if __name__ == "__main__":
    main()
