"""Compositor screenshots of both themes against light and dark backgrounds."""
import ctypes
import tempfile
import tkinter as tk
from pathlib import Path

from PIL import ImageGrab
import gui_smoke
from gui_preview import ARTIFACTS, FakeMonitor, EmptyKeyring, snapshot
from gui_theme_preview import pump, assert_layout
from token_plan_monitor.appearance import AppearancePreferences
from token_plan_monitor.models import MonitorViewState
from token_plan_monitor.storage import Storage
from token_plan_monitor.ui import MonitorUI


def render(mode):
    with tempfile.TemporaryDirectory(dir=ARTIFACTS) as directory:
        storage=Storage(Path(directory),EmptyKeyring())
        storage.save_appearance(AppearancePreferences(mode))
        monitor=FakeMonitor()
        app=MonitorUI(monitor,storage)
        background=tk.Toplevel(app.root)
        background.overrideredirect(True)
        background.geometry("1200x1000+40+40")
        canvas=tk.Canvas(background,highlightthickness=0)
        canvas.pack(fill="both",expand=True)
        try:
            app.root.tk.call("tk","scaling",96/72)
            app._configure_style()
            app._create_popup()
            app._create_desktop_widget()
            state=MonitorViewState(status="ok",snapshot=snapshot("agent_plan"))
            monitor.state=app._state=state
            app._render_state(state)
            app.show_popup()
            app.open_settings()
            pump(app)
            for name,colours in (("light",("#EEF2F7","#B5C5D8")),("dark",("#172230","#647A96"))):
                canvas.delete("all")
                canvas.configure(background=colours[0])
                for x in range(0,1200,200):
                    canvas.create_rectangle(x,0,x+100,1000,fill=colours[1],outline="")
                background.lift()
                for window,position in ((app.popup,"+65+65"),(app.desktop_widget.window,"+600+65"),
                                        (app.settings_window,"+600+265")):
                    window.attributes("-topmost",True)
                    window.geometry(position)
                    window.lift()
                app.settings_window.focus_force()
                app.root.after(500,app.root.quit)
                app.root.mainloop()
                assert_layout(app.settings_window,app.settings_body)
                picture=ImageGrab.grab(bbox=(40,40,1240,1040),all_screens=True)
                picture.save(ARTIFACTS/f"material-{mode}-on-{name}.png")
                if name==mode:
                    picture.save(ARTIFACTS/f"material-{mode}.png")
                print(f"PASS {mode} on {name}: compositor, rounded corners, tint, no outer shadow")
        finally:
            background.destroy()
            app.shutdown()


def main():
    ARTIFACTS.mkdir(exist_ok=True)
    gui_smoke.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    for mode in ("light","dark"):
        render(mode)


if __name__=="__main__":
    main()
