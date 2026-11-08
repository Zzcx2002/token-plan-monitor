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
    queried_at = datetime.now(UTC).replace(microsecond=0)
    windows = []
    for index, (kind, used) in enumerate(
        (("five_hour", 24.6), ("weekly", 58.2), ("monthly", 38.6))
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
        ("low", replace(agent,snapshot=replace(agent.snapshot,windows=tuple(
            replace(window,used_percent=94.2,used=942) if window.kind=="five_hour" else window
            for window in agent.snapshot.windows)))),
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


def main() -> None:
    # Keep the documented entry point while the matrix lives in one implementation.
    from gui_theme_preview import main as render_themes
    render_themes()


if __name__ == "__main__":
    main()
