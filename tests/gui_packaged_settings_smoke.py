"""Packaged card-menu -> Settings process-survival regression, without credentials."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import gui_settings_native_smoke as native
import gui_shell_smoke as shell
from token_plan_monitor import __version__


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "dist" / f"TokenPlanMonitor-{__version__}-win64"
EXE = APP / "TokenPlanMonitor.exe"


def main() -> None:
    if not EXE.is_file():
        raise RuntimeError(f"Build the staged portable package first: {EXE}")
    shell.user32.SetProcessDpiAwarenessContext(native.ctypes.c_void_p(-4))
    shell.ARTIFACTS.mkdir(parents=True, exist_ok=True)
    data_dir = APP / "data"
    data_dir.mkdir(exist_ok=True)
    # No settings/credentials are created: an unconfigured monitor performs no
    # provider request. Solid dark material makes text-pixel checks deterministic.
    (data_dir / "appearance.json").write_text(
        json.dumps({"theme": "dark", "material": "solid"}), encoding="utf-8"
    )
    log_path = shell.ARTIFACTS / "packaged-settings-shell.log"
    env = os.environ.copy()
    env["TOKEN_PLAN_MONITOR_GUI_TEST"] = "1"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [str(EXE)], cwd=APP, env=env, stdout=log, stderr=subprocess.STDOUT,
        )
    try:
        shell.wait_for(
            lambda: native.owned_window(process.pid, native.WIDGET_TITLE),
            "Packaged desktop card was not found", timeout=15,
        )
        for attempt in (1, 2, 3):
            hwnd, _clicked_at, _visible_at = native.open_from_card(process.pid)
            native.assert_survives(process, 8)
            work = shell.wintypes.RECT()
            assert shell.user32.SystemParametersInfoW(0x30, 0, native.ctypes.byref(work), 0)
            bounds = shell.window_rect(hwnd)
            assert work.left <= bounds[0] and work.top <= bounds[1]
            assert bounds[2] <= work.right and bounds[3] <= work.bottom
            filename = f"packaged-card-settings-{attempt}.png"
            native.gui_smoke.capture(hwnd, filename)
            native.assert_body_rendered(filename)
            shell.user32.PostMessageW(hwnd, shell.WM_CLOSE, 0, 0)
            shell.wait_for(
                lambda: not shell.user32.IsWindowVisible(hwnd),
                "Packaged Settings window did not close", timeout=4,
            )
            native.assert_survives(process, 3)
        app_log = APP / "logs" / "app.log"
        log_text = app_log.read_text(encoding="utf-8", errors="replace")
        assert "Traceback" not in log_text, log_text
        print("PASS packaged settings: real card menu, three cycles, 8s present survival, text pixels")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    check=False, capture_output=True,
                )
                process.wait(timeout=5)
        fatal = APP / "logs" / "fatal.log"
        if fatal.exists():
            (shell.ARTIFACTS / "packaged-settings-fatal.log").write_bytes(fatal.read_bytes())
        app_log = APP / "logs" / "app.log"
        if app_log.exists():
            (shell.ARTIFACTS / "packaged-settings-app.log").write_bytes(app_log.read_bytes())


if __name__ == "__main__":
    main()
