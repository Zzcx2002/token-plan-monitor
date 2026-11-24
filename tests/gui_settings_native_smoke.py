"""Native desktop-card menu -> settings rendering regression.

PrintWindow prevents an occluding terminal from producing a false visual pass.
All native handles are filtered by the fake shell's real PID so a concurrently
running production monitor is never clicked or captured.
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

from PIL import Image

import gui_smoke
import gui_shell_smoke as shell
from token_plan_monitor.ui_graphics import resolve_material


SETTINGS_TITLE = "火山方舟用量查询设置"
WIDGET_TITLE = "火山方舟桌面容量"

shell.user32.GetWindowThreadProcessId.argtypes = [
    shell.wintypes.HWND, ctypes.POINTER(shell.wintypes.DWORD)
]
shell.user32.GetWindowThreadProcessId.restype = shell.wintypes.DWORD
shell.user32.GetWindowTextW.argtypes = [
    shell.wintypes.HWND, shell.wintypes.LPWSTR, ctypes.c_int
]
shell.user32.GetWindowTextW.restype = ctypes.c_int
shell.user32.SystemParametersInfoW.argtypes = [
    shell.wintypes.UINT, shell.wintypes.UINT,
    ctypes.POINTER(shell.wintypes.RECT), shell.wintypes.UINT,
]
shell.user32.SystemParametersInfoW.restype = shell.wintypes.BOOL


def owned_window(app_pid: int, title: str, *, menu: bool = False) -> int:
    matches = []
    callback_type = ctypes.WINFUNCTYPE(
        shell.wintypes.BOOL, shell.wintypes.HWND, shell.wintypes.LPARAM
    )

    def visit(hwnd, _):
        pid = shell.wintypes.DWORD()
        shell.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value != app_pid or not shell.user32.IsWindowVisible(hwnd):
            return True
        text = ctypes.create_unicode_buffer(256)
        if menu:
            shell.user32.GetClassNameW(hwnd, text, len(text))
            wanted = "#32768"
        else:
            shell.user32.GetWindowTextW(hwnd, text, len(text))
            wanted = title
        if text.value == wanted:
            matches.append(hwnd)
        return True

    callback = callback_type(visit)
    shell.user32.EnumWindows(callback, 0)
    return matches[0] if matches else 0


def assert_body_rendered(filename: str, *, material: str = "solid") -> None:
    image = Image.open(shell.ARTIFACTS / filename).convert("RGB")
    all_colors = image.getcolors(maxcolors=image.width * image.height)
    assert all_colors
    if material == "solid":
        near_black = sum(count for count, color in all_colors if max(color) <= 8)
        assert near_black / (image.width * image.height) < 0.02, (
            f"Large unpainted/offscreen region: {filename}"
        )
    # Empty RoundedPanel outlines already provide many dark colours. In this
    # fixed dark-theme test, actual labels and controls must add light ink.
    body = image.crop((16, 56, image.width - 16, image.height - 64))
    colors = body.getcolors(maxcolors=body.width * body.height)
    assert colors and len(colors) > 16, f"Uniform/empty settings body: {filename}"
    dominant = max(count for count, _color in colors) / (body.width * body.height)
    assert dominant < 0.98, f"Blank settings body ({dominant:.1%} one color): {filename}"
    light_ink = sum(
        count for count, (red, green, blue) in colors
        if min(red, green, blue) > 140
    )
    assert light_ink > 200, (
        f"Settings panels rendered without text/controls ({light_ink} light pixels): "
        f"{filename}"
    )


def assert_survives(process: subprocess.Popen, seconds: float) -> None:
    """Sample the whole delayed-present/geometry-flush interval, not one instant."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        assert process.poll() is None, (
            f"Settings shell exited with code {process.returncode}"
        )
        time.sleep(0.25)
    assert process.poll() is None


def open_from_card(app_pid: int) -> tuple[int, float, float]:
    widget = shell.wait_for(
        lambda: owned_window(app_pid, WIDGET_TITLE), "Fake shell widget was not found"
    )
    left, top, right, bottom = shell.window_rect(widget)
    shell.user32.SetForegroundWindow.argtypes = [shell.wintypes.HWND]
    shell.user32.SetForegroundWindow.restype = shell.wintypes.BOOL
    menu = 0
    for _ in range(3):
        shell.escape_native_ui()
        shell.user32.SetForegroundWindow(widget)
        time.sleep(0.25)
        shell.click((left + right) // 2, (top + bottom) // 2, right=True)
        try:
            menu = shell.wait_for(
                lambda: owned_window(app_pid, "", menu=True),
                "Fake shell widget menu did not open", timeout=2.5,
            )
            break
        except AssertionError:
            shell.escape_native_ui()
    assert menu, "Fake shell widget menu did not open after three native right-clicks"
    assert menu == shell.native_menu_handle(), "Another process owns the active menu"
    time.sleep(0.35)
    clicked_at = shell.click_menu("设置", menu="widget", settle=0)
    hwnd = shell.wait_for(
        lambda: owned_window(app_pid, SETTINGS_TITLE),
        "Card menu did not open a visible fake-shell settings window",
        poll=0.01,
    )
    return hwnd, clicked_at, time.perf_counter()


def main() -> None:
    shell.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    requested_material = os.environ.get("TOKEN_PLAN_MONITOR_TEST_MATERIAL", "solid")
    assert requested_material in ("solid", "acrylic")
    expected_material = resolve_material(requested_material)
    shell.ARTIFACTS.mkdir(parents=True, exist_ok=True)
    process: subprocess.Popen | None = None
    app_pid: int | None = None
    with tempfile.TemporaryDirectory(
            dir=shell.ARTIFACTS, ignore_cleanup_errors=True) as temporary:
        root = Path(temporary)
        data_dir = root / "data"
        data_dir.mkdir()
        # Solid uses strict near-black checks; acrylic checks text/controls and
        # the native layered state because its colour-key background is black.
        (data_dir / "appearance.json").write_text(
            json.dumps({"theme": "dark", "material": requested_material}), encoding="utf-8"
        )
        try:
            process = shell.launch(data_dir, root)
            app_pid = json.loads(
                (data_dir / "ready.json").read_text(encoding="utf-8")
            )["pid"]
            for attempt in (1, 2, 3):
                hwnd, clicked_at, visible_at = open_from_card(app_pid)
                first_filename = f"native-card-settings-{attempt}-first.png"
                capture_attempts = 0
                first_rendered = False
                first_render_error = None
                content_deadline = time.perf_counter() + 2.0
                while True:
                    capture_attempts += 1
                    gui_smoke.capture(hwnd, first_filename)
                    first_content_at = time.perf_counter()
                    try:
                        assert_body_rendered(first_filename, material=expected_material)
                        first_rendered = True
                        first_render_error = None
                        break
                    except AssertionError as exc:
                        first_render_error = str(exc)
                        if time.perf_counter() >= content_deadline:
                            break
                        time.sleep(0.01)
                external_timing = {
                    "click_to_visible_ms": round((visible_at - clicked_at) * 1000, 3),
                    "click_to_first_content_ms": round((first_content_at - clicked_at) * 1000, 3),
                    "visible_to_content_ms": round((first_content_at - visible_at) * 1000, 3),
                    "capture_attempts": capture_attempts,
                    "first_frame_rendered": first_rendered,
                    "first_frame_error": first_render_error,
                }
                # Stay alive through presentation, nested wrapping and the 350ms
                # geometry flush. Sampling also catches an abort after the first
                # correct frame instead of accepting a transient screenshot.
                assert_survives(process, 6)
                diagnostic = data_dir / "settings-diagnostic.json"
                diagnostic.unlink(missing_ok=True)
                (data_dir / "control.json").write_text(
                    json.dumps({
                        "revision": attempt,
                        "dump_settings": True,
                        "catalog_result": ["fake-model-alpha", "fake-model-beta"],
                    }),
                    encoding="utf-8",
                )
                shell.wait_for(diagnostic.exists, "Settings diagnostic was not written")
                report_text = diagnostic.read_text(encoding="utf-8")
                report = json.loads(report_text)
                report["external_timing"] = external_timing
                report_text = json.dumps(report, ensure_ascii=False)
                performance = report["performance"]
                assert performance["counters"]["constructions"] == 1
                assert performance["counters"]["open_calls"] == attempt
                if attempt > 1:
                    assert performance["opens"][-1].get("material_apply_mapped", 0) == 0
                assert external_timing["first_frame_rendered"], external_timing
                assert report["open"] and report["content"]["mapped"]
                assert report["effects"]["material"] == expected_material
                assert report["effects"]["layered"] == (expected_material == "acrylic")
                assert report["canvas"]["size"][0] > 300
                assert report["yview"][0] == 0.0, report["yview"]
                (shell.ARTIFACTS / f"native-card-settings-{attempt}.json").write_text(
                    report_text, encoding="utf-8"
                )
                filename = f"native-card-settings-{attempt}.png"
                work = shell.wintypes.RECT()
                assert shell.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(work), 0)
                bounds = shell.window_rect(hwnd)
                assert work.left <= bounds[0] and work.top <= bounds[1]
                assert bounds[2] <= work.right and bounds[3] <= work.bottom, (
                    bounds, (work.left, work.top, work.right, work.bottom)
                )
                gui_smoke.capture(hwnd, filename)
                assert_body_rendered(filename, material=expected_material)
                assert process.poll() is None, "Settings open crashed the shell"
                shell.user32.PostMessageW(hwnd, shell.WM_CLOSE, 0, 0)
                shell.wait_for(
                    lambda: not shell.user32.IsWindowVisible(hwnd),
                    "Settings window did not close", timeout=4,
                )
                assert_survives(process, 2)
            print(f"PASS native settings ({expected_material}): three open/close cycles, "
                  "retained shell, first content, delayed survival, top anchor")
        finally:
            if app_pid is not None and owned_window(app_pid, "", menu=True):
                shell.escape_native_ui()
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            if process is not None and process.log_path.exists():
                (shell.ARTIFACTS / "native-card-settings-shell.log").write_bytes(
                    process.log_path.read_bytes()
                )
            fatal_log = data_dir / "fatal.log"
            if fatal_log.exists():
                (shell.ARTIFACTS / "native-card-settings-fatal.log").write_bytes(
                    fatal_log.read_bytes()
                )


if __name__ == "__main__":
    main()
