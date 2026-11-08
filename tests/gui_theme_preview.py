"""Render both themes and check compact/expanded layouts using isolated data."""
from __future__ import annotations

import ctypes
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from PIL import ImageGrab
import gui_smoke
from gui_preview import ARTIFACTS, FakeMonitor, EmptyKeyring, scenarios
from token_plan_monitor.appearance import AppearancePreferences
from token_plan_monitor.storage import Storage
from token_plan_monitor.ui import MonitorUI


def pump(app:MonitorUI) -> None:
    app.root.after(120,app.root.quit)
    app.root.mainloop()


def capture_screen(window:tk.Misc,filename:str) -> None:
    x,y=window.winfo_rootx(),window.winfo_rooty()
    ImageGrab.grab(bbox=(x,y,x+window.winfo_width(),y+window.winfo_height()),all_screens=True).save(ARTIFACTS/filename)


def assert_layout(window:tk.Toplevel,body) -> None:
    # Content may extend vertically inside its scrolling viewport, never horizontally.
    def visit(widget):
        for child in widget.winfo_children():
            if child.winfo_ismapped():
                if isinstance(child,(tk.Label,ttk.Label)) and child.cget("text"):
                    if child.winfo_height()<child.winfo_reqheight():
                        raise AssertionError(f"Clipped label: {child.cget('text')}")
                    if child.winfo_width()<child.winfo_reqwidth():
                        raise AssertionError(f"Narrow label: {child.cget('text')}")
                visit(child)
    visit(body.content)
    assert window.winfo_height()<=window.winfo_screenheight()-60
    assert body.canvas.winfo_width()>0


def render(mode:str,scale:float) -> None:
    with tempfile.TemporaryDirectory(dir=ARTIFACTS) as temporary:
        storage=Storage(Path(temporary),EmptyKeyring())
        storage.save_appearance(AppearancePreferences(mode))
        monitor=FakeMonitor()
        app=MonitorUI(monitor,storage)
        failures=[]
        app.root.report_callback_exception=lambda *error:failures.append(error)
        app.root.tk.call("tk","scaling",(96/72)*scale)
        app._configure_style()
        app._create_popup()
        app._create_desktop_widget()
        try:
            for name,state in scenarios():
                monitor.state=app._state=state
                app._render_state(state)
                app.show_popup()
                for expanded in (False,True):
                    if app._details_expanded!=expanded:
                        app._toggle_details()
                    pump(app)
                    assert_layout(app.popup,app.popup_body)
                    if state.snapshot:
                        for window in state.snapshot.windows:
                            row=app.rows[window.kind]
                            assert row["progress"].value==window.remaining_percent
                            if expanded and state.snapshot.plan_type=="coding_plan":
                                assert row["absolute"].winfo_manager()==""
                    card=app.desktop_widget.card
                    for item in card.find_all():
                        if card.type(item)=="text":
                            x1,y1,x2,y2=card.bbox(item)
                            assert min(x1,y1)>=0 and x2<=card.winfo_width() and y2<=card.winfo_height()
                    suffix=f"{mode}-{round(scale*100)}-{name}-{'expanded' if expanded else 'collapsed'}"
                    app.desktop_widget.window.withdraw()
                    app.popup.geometry("+40+40")
                    app.popup.lift()
                    pump(app)
                    capture_screen(app.popup,f"theme-popup-{suffix}.png")
                    if scale==1 and name=="agent":
                        capture_screen(app.popup,f"theme-{mode}-popup-{'expanded' if expanded else 'collapsed'}.png")
                        app.desktop_widget.window.deiconify()
                        app.desktop_widget.window.geometry("+600+40")
                        app.desktop_widget.window.lift()
                        pump(app)
                        capture_screen(card,f"theme-{mode}-widget.png")
                    app.desktop_widget.window.deiconify()
            app.open_settings()
            pump(app)
            assert_layout(app.settings_window,app.settings_body)
            if scale==1:
                app.popup.withdraw()
                app.desktop_widget.window.withdraw()
                app.settings_window.geometry("+40+40")
                app.settings_window.lift()
                pump(app)
                capture_screen(app.settings_window,f"theme-{mode}-settings.png")
            if app.settings_body.content.winfo_reqheight()>app.settings_body.canvas.winfo_height():
                assert app.settings_body.bar.winfo_ismapped()
                app.settings_body.canvas.yview_moveto(1)
                pump(app)
                assert app.settings_body.canvas.yview()[1]>0.99
                app.settings_body.canvas.yview_moveto(0)
                app._settings_entries["refresh"].focus_set()
                pump(app)
                field=app._settings_entries["refresh"]
                top=field.winfo_rooty()-app.settings_body.canvas.winfo_rooty()
                assert top>=0 and top+field.winfo_height()<=app.settings_body.canvas.winfo_height()
            assert not failures,failures
            print(f"PASS {mode} {scale:.0%}: seven scenarios, compact/expanded, card text, settings, viewport")
        finally:
            app.shutdown()


def main():
    ARTIFACTS.mkdir(exist_ok=True)
    gui_smoke.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    for mode in ("light","dark"):
        for scale in (1.0,1.5,2.0):
            render(mode,scale)


if __name__=="__main__":
    main()
