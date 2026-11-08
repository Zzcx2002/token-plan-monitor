"""Offline material preview against controlled light/dark backgrounds."""
from pathlib import Path
import ctypes
import tempfile
import gui_smoke
import tkinter as tk
from PIL import ImageGrab
from gui_preview import FakeMonitor, EmptyKeyring, snapshot, assert_text_fits, ARTIFACTS
from token_plan_monitor.models import MonitorViewState
from token_plan_monitor.storage import Storage
from token_plan_monitor.ui import MonitorUI, COLORS
from token_plan_monitor.ui_graphics import apply_window_effects


def main():
    gui_smoke.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    with tempfile.TemporaryDirectory(dir=ARTIFACTS) as directory:
        monitor = FakeMonitor()
        app = MonitorUI(monitor, Storage(Path(directory), EmptyKeyring()))
        app.root.tk.call("tk", "scaling", 1.0)
        app._configure_style()
        background = tk.Toplevel(app.root)
        background.overrideredirect(True)
        background.geometry('1200x1000+50+50')
        canvas = tk.Canvas(background, highlightthickness=0)
        canvas.pack(fill='both', expand=True)
        app._create_popup()
        app._create_desktop_widget()
        state = MonitorViewState(status='ok', snapshot=snapshot('agent_plan'))
        app._state = monitor.state = state
        app._render_state(state)
        app.open_settings()
        for name, colour in [('light', '#E6EBF2'), ('dark', '#192638')]:
            canvas.configure(background=colour)
            canvas.delete('all')
            for x in range(0, 1200, 60):
                canvas.create_rectangle(x, 0, x+30, 1000, fill='#8296B0', outline='')
            background.lift()
            app.show_popup()
            app.popup.geometry('+80+90')
            app.desktop_widget.window.geometry('+650+110')
            app.desktop_widget.window.lift()
            app.root.update()
            app.settings_window.geometry('+650+340')
            app.settings_window.lift()
            app.root.update()
            print(name, apply_window_effects(app.popup, COLORS['surface']))
            app.root.after(800, app.root.quit)
            app.root.mainloop()
            assert_text_fits(app.settings_window, 'settings')
            ImageGrab.grab(bbox=(50, 50, 1250, 1050)).save(ARTIFACTS / f'material-{name}.png')
        background.destroy()
        app.shutdown()

if __name__ == '__main__':
    main()
