"""Real tray/widget shell with fake quota data for native interaction smoke tests."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from token_plan_monitor.models import MonitorViewState, QuotaWindow, UsageSnapshot
from token_plan_monitor.storage import DEFAULT_DATA_DIR, Storage
from token_plan_monitor.ui import MonitorUI

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

    gui_smoke.user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    gui_smoke.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    monitor = FakeMonitor(data_dir)
    app = MonitorUI(monitor, Storage(data_dir, EmptyKeyring()))
    control_path = data_dir / "control.json"
    last_revision = 0

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
        app.root.after(100, poll_control)

    app.root.after(100, poll_control)
    app.root.after(500, lambda: (data_dir / "ready.json").write_text(
        json.dumps({"pid": os.getpid()}), encoding="utf-8"
    ))
    app.run()


if __name__ == "__main__":
    main()
