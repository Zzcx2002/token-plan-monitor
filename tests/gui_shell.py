"""Real tray/widget shell with fake quota data for native interaction smoke tests."""

from __future__ import annotations

import argparse
import ctypes
import faulthandler
import json
import os
import time
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from token_plan_monitor.models import MonitorViewState, QuotaWindow, UsageSnapshot
from token_plan_monitor.settings_view import SettingsDialog
from token_plan_monitor.storage import DEFAULT_DATA_DIR, Storage
from token_plan_monitor.ui import MonitorUI
from token_plan_monitor import ui_graphics

import gui_smoke


class EmptyKeyring:
    def get_password(self, service, username):
        return None


def snapshot(used: tuple[float, float, float]) -> UsageSnapshot:
    queried_at = datetime(2026, 11, 3, 9, 30, tzinfo=UTC)
    return UsageSnapshot(
        plan_type="agent_plan",
        plan_name="Agent Plan Large",
        queried_at=queried_at,
        windows=tuple(
            QuotaWindow(
                kind=kind,
                used_percent=used_percent,
                quota=1000,
                used=used_percent * 10,
                reset_at=queried_at + timedelta(hours=3 + index * 24),
            )
            for index, (kind, used_percent) in enumerate(
                zip(("five_hour", "weekly", "monthly"), used, strict=True)
            )
        ),
    )


class FakeMonitor:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.on_state = None
        self.state = MonitorViewState(status="ok", snapshot=snapshot((24.6, 58.2, 91.4)))

    def start(self):
        return None

    def get_state(self):
        return self.state

    def request_refresh(self):
        count_path = self.data_dir / "refresh-count.txt"
        count = int(count_path.read_text(encoding="ascii")) if count_path.exists() else 0
        count_path.write_text(str(count + 1), encoding="ascii")

    def publish(self, state):
        self.state = state
        if self.on_state is not None:
            self.on_state(state)

    def stop(self):
        return None


def install_settings_profile(app: MonitorUI) -> dict:
    """Test-only timings; production settings code carries no logging overhead."""
    profile = {"counters": Counter(), "durations_ms": Counter(), "opens": []}

    def elapsed(start: float) -> float:
        return round((time.perf_counter() - start) * 1000, 3)

    original_init = SettingsDialog.__init__

    def profiled_init(dialog, owner):
        profile["counters"]["constructions"] += 1
        original_init(dialog, owner)

    SettingsDialog.__init__ = profiled_init
    original_open = SettingsDialog.open

    def profiled_open(dialog):
        event = {"started_wall": time.time(), "durations_ms": {}}
        profile["opens"].append(event)
        profile["counters"]["open_calls"] += 1
        start = time.perf_counter()
        try:
            window = original_open(dialog)
        finally:
            event["durations_ms"]["open"] = elapsed(start)
        if not getattr(dialog, "_profile_bound", False):
            def mapped(_event, target=window):
                current = profile["opens"][-1]
                current.setdefault("mapped_wall", time.time())
                current.setdefault("window_identity", id(target))
            window.bind("<Map>", mapped, add=True)
            dialog._profile_bound = True
        return window

    SettingsDialog.open = profiled_open

    def profile_method(name: str) -> None:
        original = getattr(SettingsDialog, name)
        def wrapped(dialog, *args, **kwargs):
            profile["counters"][name] += 1
            start = time.perf_counter()
            try:
                return original(dialog, *args, **kwargs)
            finally:
                duration = elapsed(start)
                profile["durations_ms"][name] += duration
                if profile["opens"]:
                    current = profile["opens"][-1]["durations_ms"]
                    current[name] = round(current.get(name, 0) + duration, 3)
                    if name == "_anchor_initial_view":
                        profile["opens"][-1].setdefault("anchor_wall", time.time())
        setattr(SettingsDialog, name, wrapped)

    for method_name in ("_build_form", "_fit", "_present", "_anchor_initial_view"):
        profile_method(method_name)

    original_redraw = ui_graphics.RoundedPanel._redraw
    def profiled_redraw(panel, *args, **kwargs):
        dialog = app._settings_dialog
        is_settings = (
            dialog is not None and dialog.window is not None
            and panel.winfo_toplevel() == dialog.window
        )
        start = time.perf_counter()
        try:
            return original_redraw(panel, *args, **kwargs)
        finally:
            if is_settings:
                profile["counters"]["panel_redraws"] += 1
                profile["durations_ms"]["panel_redraws"] += elapsed(start)
    ui_graphics.RoundedPanel._redraw = profiled_redraw

    original_apply = ui_graphics.apply_window_effects
    def profiled_apply(window, *args, **kwargs):
        dialog = app._settings_dialog
        is_settings = dialog is not None and window is dialog.window
        start = time.perf_counter()
        try:
            return original_apply(window, *args, **kwargs)
        finally:
            if is_settings:
                key = "material_apply_mapped" if window.winfo_viewable() else "material_apply_hidden"
                profile["counters"][key] += 1
                profile["durations_ms"]["material_apply"] += elapsed(start)
                if profile["opens"]:
                    current = profile["opens"][-1]
                    current[key] = current.get(key, 0) + 1
    ui_graphics.apply_window_effects = profiled_apply
    return profile


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("TOKEN_PLAN_MONITOR_GUI_TEST") != "1":
        raise RuntimeError("The fake GUI shell requires TOKEN_PLAN_MONITOR_GUI_TEST=1")
    data_dir = args.data_dir.resolve()
    if data_dir == DEFAULT_DATA_DIR.resolve():
        raise RuntimeError("The fake GUI shell must not use production data")
    data_dir.mkdir(parents=True, exist_ok=True)
    # Native faults bypass Python logging. Keep a real file even when the test
    # launcher redirects stderr; the handle stays alive for app.run().
    fatal_log = (data_dir / "fatal.log").open("a", encoding="utf-8", buffering=1)
    faulthandler.enable(file=fatal_log, all_threads=True)

    gui_smoke.user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    gui_smoke.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    monitor = FakeMonitor(data_dir)
    app = MonitorUI(monitor, Storage(data_dir, EmptyKeyring()))
    settings_profile = install_settings_profile(app)
    control_path = data_dir / "control.json"
    last_revision = 0

    def dump_settings() -> None:
        window = app.settings_window
        if window is None:
            (data_dir / "settings-diagnostic.json").write_text(
                json.dumps({"open": False}), encoding="utf-8"
            )
            return
        body = app.settings_body
        report = {
            "open": True,
            "canvas": {
                "size": [body.canvas.winfo_width(), body.canvas.winfo_height()],
            },
            "content": {"mapped": bool(body.content.winfo_ismapped())},
            "yview": list(body.canvas.yview()),
            "effects": {
                "material": window._window_effects.material,
                "layered": window._window_effects.layered,
            },
            "performance": {
                "counters": dict(settings_profile["counters"]),
                "durations_ms": {
                    key: round(value, 3)
                    for key, value in settings_profile["durations_ms"].items()
                },
                "opens": settings_profile["opens"],
                "window_identity": id(window),
            },
        }
        (data_dir / "settings-diagnostic.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8"
        )

    def poll_control() -> None:
        nonlocal last_revision
        if app._closing:
            return
        try:
            control = json.loads(control_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            control = None
        if isinstance(control, dict) and control.get("revision") != last_revision:
            last_revision = control["revision"]
            if control.get("state") == "updated":
                monitor.publish(
                    replace(monitor.state, snapshot=snapshot((40.0, 30.0, 20.0)))
                )
            if control.get("catalog_result") and app._settings_dialog is not None:
                # Fixed local data exercises the same queued Tk completion path
                # as a real model response without credentials or network.
                app._catalog_events.put((
                    app._settings_dialog.provider,
                    list(control["catalog_result"]),
                    None,
                ))
            if control.get("dump_settings"):
                dump_settings()
        app.root.after(100, poll_control)

    app.root.after(100, poll_control)
    app.root.after(500, lambda: (data_dir / "ready.json").write_text(
        json.dumps({"pid": os.getpid()}), encoding="utf-8"
    ))
    app.run()


if __name__ == "__main__":
    main()
