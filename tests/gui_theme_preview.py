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
from token_plan_monitor.desktop_widget import refresh_progress_limit
from token_plan_monitor.storage import Storage
from token_plan_monitor.ui import MonitorUI


def pump(app:MonitorUI) -> None:
    app.root.after(120,app.root.quit)
    app.root.mainloop()


def is_packed(widget) -> bool:
    try:
        return bool(widget.pack_info())
    except tk.TclError:
        return False


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


# clam's readonly combobox map leaks these light/system colors; a themed
# dropdown must never resolve back to them in either mode.
CLAM_COMBOBOX_LEAKS = {"#dcdad5", "#4a6984", "#999999", "#eeebe7", "#cfcdc8"}


def assert_combobox_theme(app: MonitorUI, mode: str) -> None:
    """Style lookups, the classic popdown Listbox and live theme recolor."""
    style = ttk.Style(app.root)
    colors = app.colors
    card = colors["card"].lower()
    # The ttk style itself: base config plus the state maps that override
    # clam's hard-coded warm-grey readonly field and steel-blue focus field.
    assert style.lookup("TCombobox", "fieldbackground").lower() == card
    # style.map returns flat tuples: state names followed by the value last.
    field_map = {tuple(entry[:-1]): entry[-1].lower()
                 for entry in style.map("TCombobox", "fieldbackground")}
    assert field_map[("readonly",)] == card
    assert field_map[("readonly", "focus")] == card
    assert field_map[("disabled",)] == colors["trough"].lower()
    border_map = {tuple(entry[:-1]): entry[-1].lower()
                  for entry in style.map("TCombobox", "bordercolor")}
    assert border_map[("focus",)] == colors["accent"].lower()
    arrow_map = {tuple(entry[:-1]): entry[-1].lower()
                 for entry in style.map("TCombobox", "arrowcolor")}
    assert arrow_map[("disabled",)] == colors["muted"].lower()
    assert style.lookup("ComboboxPopdownFrame", "background").lower() == card
    themed = set(field_map.values()) | set(border_map.values()) | set(arrow_map.values())
    themed.add(style.lookup("TCombobox", "fieldbackground").lower())
    # Light mode's card token is legitimately white; the white ban is dark-only.
    forbidden = set(CLAM_COMBOBOX_LEAKS)
    if mode == "dark":
        forbidden.add("#ffffff")
    assert themed.isdisjoint(forbidden)

    panel = app._settings_dialog.claude_panel
    combo = panel.model_combo
    combo_path = str(combo)
    listbox_path = f"{combo_path}.popdown.f.l"
    popdown_path = f"{combo_path}.popdown"
    # The real Ark catalogue holds only the custom marker (one selected row);
    # add throwaway rows so the screenshot shows plain card-background rows
    # next to an accent-selected row.
    original_values = combo.cget("values")
    combo.configure(values=tuple(original_values) + (
        "demo-model-alpha", "demo-model-beta"))
    # The popdown is a lazily created classic Toplevel/Listbox outside the
    # normal widget tree; post it for real so the option database takes effect.
    app.root.tk.call("ttk::combobox::Post", combo_path)
    try:
        pump(app)
        assert app.root.tk.call("winfo", "exists", listbox_path)
        listbox_get = lambda option: app.root.tk.call(
            listbox_path, "cget", option).lower()
        assert listbox_get("-background") == card
        assert listbox_get("-foreground") == colors["text"].lower()
        assert listbox_get("-selectbackground") == colors["accent"].lower()
        assert listbox_get("-selectforeground") == colors["button_text"].lower()
        if mode == "dark":
            # The original dark-mode bug was a system-white popdown.
            assert listbox_get("-background") != "#ffffff"

        # A theme switch while the popdown is open must recolor the existing
        # Listbox directly; the option database only affects future popdowns.
        other_mode = "dark" if mode == "light" else "light"
        app._set_theme(other_mode)
        pump(app)
        switched_card = app.colors["card"].lower()
        assert switched_card != card
        assert listbox_get("-background") == switched_card
        assert listbox_get("-selectbackground") == app.colors["accent"].lower()
        app._set_theme(mode)
        pump(app)
        assert listbox_get("-background") == card

        # Screenshot the closed box together with its open popdown.
        app.root.update_idletasks()
        pop_x = int(app.root.tk.call("winfo", "rootx", popdown_path))
        pop_y = int(app.root.tk.call("winfo", "rooty", popdown_path))
        pop_w = int(app.root.tk.call("winfo", "width", popdown_path))
        pop_h = int(app.root.tk.call("winfo", "height", popdown_path))
        x1 = min(combo.winfo_rootx(), pop_x)
        y1 = min(combo.winfo_rooty(), pop_y)
        x2 = max(combo.winfo_rootx() + combo.winfo_width(), pop_x + pop_w)
        y2 = max(combo.winfo_rooty() + combo.winfo_height(), pop_y + pop_h)
        ImageGrab.grab(
            bbox=(x1, y1, x2, y2), all_screens=True
        ).save(ARTIFACTS / f"theme-{mode}-settings-combobox.png")
    finally:
        # Unpost withdraws (but keeps for reuse) the popdown Toplevel.
        app.root.tk.call("ttk::combobox::Unpost", combo_path)
        combo.configure(values=original_values)
        pump(app)
        assert not app.root.tk.call("winfo", "viewable", popdown_path)


def render(mode:str,scale:float) -> None:
    with tempfile.TemporaryDirectory(dir=ARTIFACTS) as temporary:
        storage=Storage(Path(temporary),EmptyKeyring())
        storage.save_appearance(AppearancePreferences(mode))
        gui_smoke.simulate_monitor_dpi(round(96*scale))
        monitor=FakeMonitor()
        app=MonitorUI(monitor,storage)
        failures=[]
        app.root.report_callback_exception=lambda *error:failures.append(error)
        app.root.tk.call("tk","scaling",(96/72)*scale)
        app._configure_style()
        app._create_popup()
        app._create_desktop_widget()
        # Themed scrollbars replace clam's warm-grey defaults at every DPI.
        style = ttk.Style(app.root)
        assert style.lookup("Vertical.TScrollbar", "troughcolor") == app.colors["trough"]
        assert int(style.lookup("Vertical.TScrollbar", "width")) == round(10 * scale)
        assert style.lookup("DetailMuted.TLabel", "foreground") == app.colors["muted"]
        try:
            for name,state in scenarios():
                monitor.state=app._state=state
                app._render_state(state)
                if state.snapshot:
                    # N visible quota rows carry exactly N-1 hairline separators.
                    mapped = sum(is_packed(row["separator"]) for row in app.rows.values())
                    assert mapped == len(state.snapshot.windows) - 1
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
                    # The refresh button stays inside the card, and the monthly
                    # progress bar never crosses its reserved hit zone.
                    refresh_box=card.bbox("refresh")
                    assert refresh_box and min(refresh_box[0],refresh_box[1])>=0
                    assert refresh_box[2]<=card.winfo_width() and refresh_box[3]<=card.winfo_height()
                    monthly_box=card.bbox("progress:monthly")
                    assert monthly_box
                    assert monthly_box[2]<=refresh_progress_limit(card.winfo_width(),card.scale)+1
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
            # Hidden construction, presentation and late-parent wrapping are
            # separate event turns, especially at simulated high DPI.
            pump(app)
            pump(app)
            assert_layout(app.settings_window,app.settings_body)
            if scale==1:
                app.popup.withdraw()
                app.desktop_widget.window.withdraw()
                app.settings_window.geometry("+40+40")
                app.settings_window.lift()
                pump(app)
                gui_smoke.capture(
                    app.settings_window._chrome.hwnd, f"theme-{mode}-settings.png"
                )
                assert_combobox_theme(app, mode)
            if app.settings_body.content.winfo_reqheight()>app.settings_body.canvas.winfo_height():
                assert app.settings_body.bar.winfo_ismapped()
                app.settings_body.canvas.yview_moveto(1)
                pump(app)
                assert app.settings_body.canvas.yview()[1]>0.99
                app.settings_body.canvas.yview_moveto(0)
                field=app._settings_entries["refresh"]
                # Let wrapped labels and the scroll region settle first: a
                # reveal measured against mid-layout geometry would no-op and
                # then be proved wrong by the reflow.
                pump(app)
                field.focus_set()
                # A driven harness does not deliver FocusIn reliably; fire the
                # same event the real focus change would, so the scrollable
                # body's reveal path scrolls the field back into the viewport.
                field.event_generate("<FocusIn>")
                pump(app)
                top=field.winfo_rooty()-app.settings_body.canvas.winfo_rooty()
                assert top>=0 and top+field.winfo_height()<=app.settings_body.canvas.winfo_height(), (
                    top, field.winfo_height(), app.settings_body.canvas.winfo_height(),
                    app.settings_body.canvas.yview(), app.settings_body._ready,
                    app.settings_body._armed,
                )
            retained_window = app.settings_window
            retained_dialog = app._settings_dialog
            retained_dialog.close()
            pump(app)
            assert app.settings_window is None and not retained_window.winfo_viewable()
            app.open_settings()
            pump(app)
            pump(app)
            assert app.settings_window is retained_window
            assert app._settings_dialog is retained_dialog
            assert app.settings_body.canvas.yview()[0] == 0.0
            assert_layout(app.settings_window, app.settings_body)
            assert not failures,failures
            coverage = "settings, viewport"
            if scale == 1:
                coverage = "settings, viewport, combobox style/popdown/recolor"
            print(f"PASS {mode} {scale:.0%}: seven scenarios, compact/expanded, card text, {coverage}")
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
