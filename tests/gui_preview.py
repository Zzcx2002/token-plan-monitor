"""Render offline quota states for visual inspection without tray or cloud access."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import tempfile
import tkinter as tk
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tkinter import ttk

from PIL import ImageGrab

from token_plan_monitor.models import MonitorViewState, QuotaWindow, UsageSnapshot
from token_plan_monitor.storage import Storage
from token_plan_monitor.ui import POPUP_WIDTH, MonitorUI

import gui_smoke

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = PROJECT_ROOT / "artifacts"


class EmptyKeyring:
    def get_password(self, service, username):
        return None


class FakeMonitor:
    def __init__(self):
        self.on_state = None
        self.state = MonitorViewState(status="unconfigured")

    def get_state(self):
        return self.state

    def request_refresh(self):
        return None

    def stop(self):
        return None


def snapshot(plan_type: str) -> UsageSnapshot:
    queried_at = datetime(2026, 11, 3, 9, 30, tzinfo=UTC)
    windows = []
    for index, (kind, used) in enumerate(
        (("five_hour", 24.6), ("weekly", 58.2), ("monthly", 91.4))
    ):
        windows.append(
            QuotaWindow(
                kind=kind,
                used_percent=used,
                quota=1000 if plan_type == "agent_plan" else None,
                used=used * 10 if plan_type == "agent_plan" else None,
                reset_at=queried_at + timedelta(hours=3 + index * 24),
            )
        )
    return UsageSnapshot(
        plan_type=plan_type,
        plan_name=(
            "Agent Plan Large" if plan_type == "agent_plan" else "Coding Plan Pro"
        ),
        queried_at=queried_at,
        windows=tuple(windows),
    )


def scenarios():
    agent = MonitorViewState(status="ok", snapshot=snapshot("agent_plan"))
    return (
        ("agent", agent),
        ("coding", MonitorViewState(status="ok", snapshot=snapshot("coding_plan"))),
        ("refreshing", replace(agent, is_refreshing=True)),
        (
            "stale",
            replace(
                agent,
                status="stale",
                error="网络连接超时，当前保留最近一次成功查询的数据。",
            ),
        ),
        ("unconfigured", MonitorViewState(status="unconfigured")),
        ("error", MonitorViewState(status="error", error="无法连接火山引擎 OpenAPI")),
    )


def assert_text_fits(window: tk.Misc, filename: str) -> None:
    root_x = window.winfo_rootx()
    root_y = window.winfo_rooty()
    window_width = window.winfo_width()
    window_height = window.winfo_height()

    def visit(widget: tk.Misc) -> None:
        for child in widget.winfo_children():
            if child.winfo_ismapped():
                if isinstance(child, (tk.Label, ttk.Label)) and child.cget("text"):
                    x = child.winfo_rootx() - root_x
                    y = child.winfo_rooty() - root_y
                    right = x + child.winfo_width()
                    bottom = y + child.winfo_height()
                    if x < 0 or y < 0 or right > window_width or bottom > window_height:
                        raise RuntimeError(
                            f"Text outside {filename}: {child.cget('text')!r} "
                            f"bounds={(x, y, right, bottom)} "
                            f"window={(window_width, window_height)}"
                        )
                    if child.winfo_height() < child.winfo_reqheight():
                        raise RuntimeError(
                            f"Text height clipped in {filename}: {child.cget('text')!r}"
                        )
                visit(child)

    visit(window)


def capture_widget(widget: tk.Toplevel, filename: str) -> None:
    bounds = (
        widget.winfo_rootx(),
        widget.winfo_rooty(),
        widget.winfo_rootx() + widget.winfo_width(),
        widget.winfo_rooty() + widget.winfo_height(),
    )
    ImageGrab.grab(bbox=bounds, all_screens=True).save(ARTIFACTS / filename)
    print(ARTIFACTS / filename)


def render_scale(scale: float) -> None:
    with tempfile.TemporaryDirectory(dir=ARTIFACTS) as temporary:
        monitor = FakeMonitor()
        storage = Storage(Path(temporary), EmptyKeyring())
        app = MonitorUI(monitor, storage)
        app.root.tk.call("tk", "scaling", scale)
        app._configure_style()
        app._create_popup()
        app._create_desktop_widget()
        items = iter(scenarios())

        def capture_current(name: str, state_name: str) -> None:
            app.root.update_idletasks()
            widget = app.desktop_widget.window
            card = app.desktop_widget.card
            if widget.winfo_width() < max(app.desktop_widget.MIN_WIDTH, card.winfo_reqwidth()):
                raise RuntimeError(f"Widget width is clipped in {state_name}")
            if widget.winfo_height() < card.winfo_reqheight():
                raise RuntimeError(
                    f"Widget height is clipped in {state_name}: "
                    f"{widget.winfo_height()} < {card.winfo_reqheight()}"
                )
            assert_text_fits(widget, state_name)
            native_rect = wintypes.RECT()
            if not gui_smoke.user32.GetWindowRect(widget.winfo_id(), ctypes.byref(native_rect)):
                raise RuntimeError(f"Could not read native widget bounds in {state_name}")
            native_size = (
                native_rect.right - native_rect.left,
                native_rect.bottom - native_rect.top,
            )
            if any(
                abs(native - logical) > 1
                for native, logical in zip(
                    native_size, (widget.winfo_width(), widget.winfo_height()), strict=True
                )
            ):
                raise RuntimeError(
                    f"Native rounded region clips {state_name}: "
                    f"{native_size} != {(widget.winfo_width(), widget.winfo_height())}"
                )
            if app.popup.winfo_width() != POPUP_WIDTH:
                raise RuntimeError(f"Unexpected popup width: {app.popup.winfo_width()}")
            if app.popup.winfo_height() < app.popup.winfo_reqheight():
                raise RuntimeError(f"Popup content is clipped in {state_name}")
            assert_text_fits(app.popup, state_name)
            suffix = name if scale == 1.0 else f"{round(scale * 100)}-{name}"
            gui_smoke.capture(app.popup.winfo_id(), f"popup-{suffix}.png")
            print(ARTIFACTS / f"popup-{suffix}.png")
            widget_filename = f"widget-{suffix}.png"
            capture_widget(widget, widget_filename)
            app.root.after(120, render_next)

        def render_next() -> None:
            try:
                name, state = next(items)
            except StopIteration:
                app.shutdown()
                return
            monitor.state = state
            app._state = state
            app._render_state(state)
            app.show_popup()
            state_name = f"{round(scale * 100)}-{name}"
            app.root.after(300, lambda: capture_current(name, state_name))

        app.root.after(100, render_next)
        try:
            app.root.mainloop()
        finally:
            app.shutdown()


def main() -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    gui_smoke.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    for scale in (1.0, 1.5, 2.0):
        render_scale(scale)


if __name__ == "__main__":
    main()
