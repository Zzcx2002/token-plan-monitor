"""Drive the real tray and desktop widget with fake data; no credentials or network."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PIL import ImageGrab

from token_plan_monitor.desktop_widget import (
    MONITORINFO,
    _user32,
    refresh_button_hit_bounds,
)

import gui_smoke

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = PROJECT_ROOT / "artifacts"
SHELL = PROJECT_ROOT / "tests" / "gui_shell.py"
WM_CLOSE = 0x0010
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008

user32 = gui_smoke.user32
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = wintypes.LONG
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]


def ps_text(text: str) -> str:
    return "-join ([char[]](" + ",".join(f"0x{ord(char):X}" for char in text) + "))"


def powershell(script: str) -> str:
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if result.returncode:
        raise RuntimeError(f"PowerShell failed: {result.stdout}\n{result.stderr}")
    return result.stdout.strip()


UIA_PREFIX = """
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);
Add-Type -AssemblyName UIAutomationClient;
$root=[System.Windows.Automation.AutomationElement]::RootElement;
"""


def tray_icon() -> dict:
    needle = ps_text("火山方舟")
    chevron = ps_text("显示隐藏的图标")
    output = powershell(
        UIA_PREFIX
        + f"""
$needle={needle};
function Find-MonitorIcon {{
    $items=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition);
    return $items | Where-Object {{ $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button -and $_.Current.Name -and $_.Current.Name.Contains($needle) -and -not $_.Current.IsOffscreen }} | Select-Object -First 1;
}}
$icon=Find-MonitorIcon;
if (-not $icon) {{
    $condition=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Button);
    $buttons=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,$condition);
    $chevron=$buttons | Where-Object {{ $_.Current.Name -eq {chevron} }} | Select-Object -First 1;
    if (-not $chevron) {{ throw 'Tray overflow button not found' }};
    $chevron.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke();
    Start-Sleep -Milliseconds 500;
    $icon=Find-MonitorIcon;
}}
if (-not $icon) {{ throw 'Monitor tray icon not found' }};
$rect=$icon.Current.BoundingRectangle;
[PSCustomObject]@{{Name=$icon.Current.Name;X=$rect.X;Y=$rect.Y;Width=$rect.Width;Height=$rect.Height}} | ConvertTo-Json -Compress;
"""
    )
    return json.loads(output)


user32.SendMessageW.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.SendMessageW.restype = wintypes.HANDLE
user32.GetMenuItemCount.argtypes = [wintypes.HANDLE]
user32.GetMenuItemCount.restype = ctypes.c_int
user32.GetMenuStringW.argtypes = [
    wintypes.HANDLE,
    wintypes.UINT,
    wintypes.LPWSTR,
    ctypes.c_int,
    wintypes.UINT,
]
user32.GetMenuStringW.restype = ctypes.c_int
user32.GetMenuItemRect.argtypes = [
    wintypes.HWND,
    wintypes.HANDLE,
    wintypes.UINT,
    ctypes.POINTER(wintypes.RECT),
]
user32.GetMenuItemRect.restype = wintypes.BOOL


def native_menu_entries() -> list[tuple[int, str]]:
    hwnd = native_menu_handle()
    if not hwnd:
        return []
    hmenu = user32.SendMessageW(hwnd, 0x01E1, 0, 0)  # MN_GETHMENU
    if not hmenu:
        return []
    entries = []
    for index in range(user32.GetMenuItemCount(hmenu)):
        length = user32.GetMenuStringW(hmenu, index, None, 0, 0x0400)
        if length <= 0:
            continue
        text = ctypes.create_unicode_buffer(length + 1)
        user32.GetMenuStringW(hmenu, index, text, len(text), 0x0400)
        entries.append((index, text.value))
    return entries


def uia_menu_items() -> list[dict]:
    hwnd = native_menu_handle()
    if not hwnd:
        return []
    left, top, right, bottom = window_rect(hwnd)
    output = powershell(
        UIA_PREFIX
        + f"""
$condition=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::MenuItem);
$items=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,$condition);
$visible=@($items | Where-Object {{
    $_.Current.Name -and -not $_.Current.IsOffscreen -and
    $_.Current.BoundingRectangle.X -ge {left - 2} -and
    $_.Current.BoundingRectangle.Y -ge {top - 2} -and
    $_.Current.BoundingRectangle.Right -le {right + 2} -and
    $_.Current.BoundingRectangle.Bottom -le {bottom + 2}
}} | ForEach-Object {{
    $rect=$_.Current.BoundingRectangle;
    [PSCustomObject]@{{Name=$_.Current.Name;X=$rect.X;Y=$rect.Y;Width=$rect.Width;Height=$rect.Height}}
}});
ConvertTo-Json -InputObject $visible -Compress;
"""
    )
    if not output:
        return []
    value = json.loads(output)
    return [value] if isinstance(value, dict) else value


def menu_names() -> list[str]:
    native = [text for _, text in native_menu_entries()]
    return native or [item["Name"] for item in uia_menu_items()]


def menu_item_rect(name: str) -> dict:
    hwnd = native_menu_handle()
    hmenu = user32.SendMessageW(hwnd, 0x01E1, 0, 0) if hwnd else None
    for index, text in native_menu_entries():
        if text != name:
            continue
        rect = wintypes.RECT()
        if not user32.GetMenuItemRect(hwnd, hmenu, index, ctypes.byref(rect)):
            if not user32.GetMenuItemRect(None, hmenu, index, ctypes.byref(rect)):
                raise AssertionError(f"Could not locate native menu item: {name}")
        return {
            "X": rect.left,
            "Y": rect.top,
            "Width": rect.right - rect.left,
            "Height": rect.bottom - rect.top,
        }
    for item in uia_menu_items():
        if item["Name"] == name:
            return item
    raise AssertionError(f"Native menu item not found: {name}")


def wait_for(predicate, message: str, timeout: float = 8.0):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            value = predicate()
            if value:
                return value
        except (RuntimeError, AssertionError, json.JSONDecodeError) as exc:
            last_error = exc
        time.sleep(0.15)
    raise AssertionError(f"{message}; last error: {last_error}")


def window_handle(title: str) -> int:
    return user32.FindWindowW(None, title)


def window_rect(title_or_hwnd) -> tuple[int, int, int, int]:
    hwnd = title_or_hwnd if isinstance(title_or_hwnd, int) else window_handle(title_or_hwnd)
    if not hwnd:
        raise AssertionError("Native window was not found")
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise AssertionError("GetWindowRect failed")
    return rect.left, rect.top, rect.right, rect.bottom


def native_menu_handle() -> int:
    menus = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(hwnd, _):
        class_name = ctypes.create_unicode_buffer(32)
        user32.GetClassNameW(hwnd, class_name, len(class_name))
        if class_name.value == "#32768" and user32.IsWindowVisible(hwnd):
            menus.append(hwnd)
        return True

    user32.EnumWindows(callback_type(visit), 0)
    return menus[0] if menus else 0


def click(x: int, y: int, *, right: bool = False) -> None:
    user32.SetCursorPos(x, y)
    time.sleep(0.08)
    down, up = (0x0008, 0x0010) if right else (0x0002, 0x0004)
    user32.mouse_event(down, 0, 0, 0, 0)
    time.sleep(0.05)
    user32.mouse_event(up, 0, 0, 0, 0)


def click_rect(rect) -> None:
    click(round(rect["X"] + rect["Width"] / 2), round(rect["Y"] + rect["Height"] / 2))


def escape_native_ui() -> None:
    for _ in range(2):
        user32.keybd_event(0x1B, 0, 0, 0)
        user32.keybd_event(0x1B, 0, 2, 0)
        time.sleep(0.15)


def open_tray_menu() -> int:
    for _ in range(3):
        escape_native_ui()
        icon = tray_icon()
        click(
            round(icon["X"] + icon["Width"] / 2),
            round(icon["Y"] + icon["Height"] / 2),
            right=True,
        )
        time.sleep(0.4)
        hwnd = native_menu_handle()
        if hwnd and any(
            name in ("打开详细用量", "显示用量") for name in menu_names()
        ):
            return hwnd
    raise AssertionError("The monitor tray menu did not open")


# Ordered menu slots; None marks a separator. The tray table mirrors
# MonitorUI._create_tray_menu and the widget table mirrors
# DesktopWidget._show_menu (four disabled header rows while a snapshot
# exists). Adding a tray row now means extending the table, not re-deriving
# positional indexes by hand.
TRAY_MENU_LAYOUT = (
    "context", "window", "window", "window", None,
    "打开详细用量", "用量历史", "立即刷新", None,
    "桌面卡片", "卡片置顶", "设置", None,
    "退出",
)
WIDGET_MENU_LAYOUT = (
    "context", "window", "window", "window", None,
    "打开详细用量", "立即刷新", "置顶显示", "隐藏桌面卡片", "设置", None,
    "退出",
)


def fallback_menu_point(hwnd: int, name: str, layout: tuple) -> tuple[int, int]:
    """Pixel center of a row when neither HMENU nor UIA exposes menu items."""
    if name not in layout:
        raise AssertionError(f"{name!r} is not part of this menu layout")
    slot = layout.index(name)
    text_index = sum(1 for entry in layout[:slot] if entry is not None)
    separators_before = sum(1 for entry in layout[:slot] if entry is None)
    text_rows = sum(1 for entry in layout if entry is not None)
    separator_count = sum(1 for entry in layout if entry is None)
    left, top, right, bottom = window_rect(hwnd)
    # A native text row is about 2.5 separator heights tall, so the menu's
    # total height in separator units is text_rows * 2.5 + separator_count.
    separator_height = round(
        (bottom - top) / (text_rows * 2.5 + separator_count)
    )
    row_height = (
        bottom - top - separator_count * separator_height
    ) / text_rows
    return (
        (left + right) // 2,
        round(
            top
            + (text_index + 0.5) * row_height
            + separators_before * separator_height
        ),
    )


def click_menu(name: str, *, menu: str = "tray") -> None:
    try:
        rect = menu_item_rect(name)
    except AssertionError:
        # Tk's custom Windows menu exposes neither HMENU nor UIA items in
        # some builds; fall back to the structural row tables above.
        hwnd = native_menu_handle()
        if not hwnd:
            raise
        layout = (
            TRAY_MENU_LAYOUT if menu == "tray" else WIDGET_MENU_LAYOUT
        )
        click(*fallback_menu_point(hwnd, name, layout))
    else:
        click_rect(rect)
    time.sleep(0.4)


def capture_rect(rect: tuple[int, int, int, int], filename: str, padding: int = 0) -> None:
    left, top, right, bottom = rect
    image = ImageGrab.grab(
        (left - padding, top - padding, right + padding, bottom + padding),
        all_screens=True,
    )
    image.save(ARTIFACTS / filename)
    print(ARTIFACTS / filename)


def capture_menu(filename: str) -> None:
    hwnd = native_menu_handle()
    if not hwnd:
        raise AssertionError("No native menu to capture")
    capture_rect(window_rect(hwnd), filename)


def widget_rect() -> tuple[int, int, int, int]:
    return window_rect("火山方舟桌面容量")


def widget_visible() -> bool:
    hwnd = window_handle("火山方舟桌面容量")
    return bool(hwnd and user32.IsWindowVisible(hwnd))


def popup_visible() -> bool:
    hwnd = window_handle("火山方舟订阅容量")
    return bool(hwnd and user32.IsWindowVisible(hwnd))


def monitor_scale_at(x: int, y: int) -> float:
    """Effective DPI scale of the monitor containing a physical-screen point."""
    monitor = _user32.MonitorFromPoint(wintypes.POINT(x, y), 2)
    if not monitor:
        raise AssertionError("No monitor contains the desktop widget")
    shcore = ctypes.windll.shcore
    shcore.GetDpiForMonitor.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.POINTER(wintypes.UINT),
        ctypes.POINTER(wintypes.UINT),
    ]
    shcore.GetDpiForMonitor.restype = ctypes.c_long
    dpi_x, dpi_y = wintypes.UINT(), wintypes.UINT()
    shcore.GetDpiForMonitor(monitor, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y))
    return dpi_x.value / 96.0


def load_preferences(data_dir: Path) -> dict:
    return json.loads((data_dir / "widget.json").read_text(encoding="utf-8"))["widget"]


def launch(data_dir: Path, log_dir: Path) -> subprocess.Popen:
    ready = data_dir / "ready.json"
    ready.unlink(missing_ok=True)
    env = os.environ.copy()
    env["TOKEN_PLAN_MONITOR_GUI_TEST"] = "1"
    env["PYTHON_KEYRING_BACKEND"] = "keyring.backends.fail.Keyring"
    log_path = log_dir / f"shell-{time.time_ns()}.log"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, str(SHELL), "--data-dir", str(data_dir)],
            cwd=PROJECT_ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    process.log_path = log_path
    wait_for(ready.exists, "Fake GUI shell did not become ready")
    wait_for(widget_visible, "Desktop widget did not become visible", timeout=4)
    return process


def stop_via_tray(process: subprocess.Popen) -> None:
    escape_native_ui()
    open_tray_menu()
    click_menu("退出")
    process.wait(timeout=10)
    if process.returncode != 0:
        raise AssertionError(f"GUI shell exited with {process.returncode}")


def assert_inside_work_area(rect: tuple[int, int, int, int]) -> None:
    left, top, right, bottom = rect
    point = wintypes.POINT((left + right) // 2, (top + bottom) // 2)
    monitor = _user32.MonitorFromPoint(point, 2)
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if not monitor or not _user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        raise AssertionError("Could not read monitor work area")
    work = info.rcWork
    assert left >= work.left and top >= work.top
    assert right <= work.right and bottom <= work.bottom


def main() -> None:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    processes = []
    with tempfile.TemporaryDirectory(dir=ARTIFACTS, ignore_cleanup_errors=True) as temporary:
        root = Path(temporary)
        data_dir = root / "data"
        try:
            process = launch(data_dir, root)
            processes.append(process)

            icon = tray_icon()
            assert "75.4%" in icon["Name"] and "41.8%" in icon["Name"]
            assert "每月" not in icon["Name"]
            click_rect(icon)
            wait_for(popup_visible, "Tray default action did not open the full popup")
            user32.PostMessageW(window_handle("火山方舟订阅容量"), WM_CLOSE, 0, 0)
            icon = tray_icon()
            user32.SetCursorPos(0, 0)
            time.sleep(0.5)
            user32.SetCursorPos(
                round(icon["X"] + icon["Width"] / 2),
                round(icon["Y"] + icon["Height"] / 2),
            )
            time.sleep(1.3)
            capture_rect(
                (
                    round(icon["X"]) - 300,
                    round(icon["Y"]) - 150,
                    round(icon["X"] + icon["Width"]) + 350,
                    round(icon["Y"] + icon["Height"]) + 50,
                ),
                "native-tray-hover.png",
            )

            open_tray_menu()
            names = menu_names()
            for expected in (
                "5 小时：剩余 75.4% · 已用 24.6%",
                "每周：剩余 41.8% · 已用 58.2%",
                "每月：剩余 8.6% · 已用 91.4%",
                "用量历史",
            ):
                assert expected in names, names
            capture_menu("native-tray-menu.png")
            escape_native_ui()

            start = widget_rect()
            # An empty-area click no longer opens the popup; the entry points
            # stay the tray icon and the widget menu's 打开详细用量 row.
            click((start[0] + start[2]) // 2, (start[1] + start[3]) // 2)
            time.sleep(0.5)
            assert not popup_visible(), "Empty widget click must not open the popup"

            # The right-click menu still opens the full popup.
            click((start[0] + start[2]) // 2, (start[1] + start[3]) // 2, right=True)
            wait_for(native_menu_handle, "Widget menu did not open")
            time.sleep(0.35)
            click_menu("打开详细用量", menu="widget")
            wait_for(popup_visible, "Widget menu did not open the full popup")
            user32.PostMessageW(window_handle("火山方舟订阅容量"), WM_CLOSE, 0, 0)
            wait_for(lambda: not popup_visible(), "Popup did not close", timeout=4)

            # The bottom-right refresh button: convert its DIP hit box with the
            # monitor's real scale and click the physical center. The fake
            # monitor appends a count file on every request_refresh call.
            left, top, right, bottom = start
            scale = monitor_scale_at((left + right) // 2, (top + bottom) // 2)
            hit_left, hit_top, hit_right, hit_bottom = refresh_button_hit_bounds(
                right - left, bottom - top, scale
            )
            refresh_count = data_dir / "refresh-count.txt"
            click(
                left + (hit_left + hit_right) // 2,
                top + (hit_top + hit_bottom) // 2,
            )
            wait_for(
                lambda: refresh_count.exists()
                and refresh_count.read_text(encoding="ascii") == "1",
                "Refresh button click did not reach the monitor",
            )

            user32.SetCursorPos(0, 0)
            time.sleep(0.4)
            user32.SetCursorPos((start[0] + start[2]) // 2, (start[1] + start[3]) // 2)
            time.sleep(0.8)
            hover = wait_for(
                lambda: (
                    hwnd
                    if (hwnd := window_handle("火山方舟容量摘要"))
                    and user32.IsWindowVisible(hwnd)
                    else 0
                ),
                "Widget hover did not become visible",
            )
            capture_rect(window_rect(hover), "native-widget-hover.png", padding=4)
            user32.SetCursorPos(0, 0)
            time.sleep(0.3)
            assert not user32.IsWindowVisible(hover)

            x, y = (start[0] + start[2]) // 2, (start[1] + start[3]) // 2
            user32.SetCursorPos(x, y)
            user32.mouse_event(0x0002, 0, 0, 0, 0)
            for step in range(1, 9):
                user32.SetCursorPos(x - step * 6, y - step * 3)
                time.sleep(0.025)
            user32.mouse_event(0x0004, 0, 0, 0, 0)
            time.sleep(0.5)
            dragged = widget_rect()
            assert dragged[0] - start[0] == -48 and dragged[1] - start[1] == -24
            assert not popup_visible(), "Dragging accidentally opened the popup"
            preferences = load_preferences(data_dir)
            assert (preferences["x"], preferences["y"]) == dragged[:2]
            assert preferences["visible"] is True and preferences["topmost"] is True

            left, top, right, bottom = widget_rect()
            click((left + right) // 2, (top + bottom) // 2, right=True)
            wait_for(native_menu_handle, "Widget menu did not open")
            time.sleep(0.35)
            capture_menu("native-widget-menu.png")
            click_menu("置顶显示", menu="widget")
            preferences = load_preferences(data_dir)
            assert preferences["topmost"] is False
            style = user32.GetWindowLongW(window_handle("火山方舟桌面容量"), GWL_EXSTYLE)
            assert not style & WS_EX_TOPMOST

            left, top, right, bottom = widget_rect()
            click((left + right) // 2, (top + bottom) // 2, right=True)
            click_menu("隐藏桌面卡片", menu="widget")
            wait_for(lambda: not widget_visible(), "Widget did not hide")
            assert load_preferences(data_dir)["visible"] is False

            open_tray_menu()
            click_menu("桌面卡片")
            wait_for(widget_visible, "Tray did not restore the widget")
            preferences = load_preferences(data_dir)
            assert preferences["visible"] is True and preferences["topmost"] is False

            (data_dir / "control.json").write_text(
                json.dumps({"revision": 1, "state": "updated"}), encoding="utf-8"
            )
            wait_for(
                lambda: "60.0%" in tray_icon()["Name"]
                and "70.0%" in tray_icon()["Name"],
                "Tray tooltip did not refresh",
            )
            open_tray_menu()
            names = menu_names()
            assert "5 小时：剩余 60.0% · 已用 40.0%" in names
            assert "每周：剩余 70.0% · 已用 30.0%" in names
            assert "每月：剩余 80.0% · 已用 20.0%" in names
            capture_menu("native-tray-menu-updated.png")
            escape_native_ui()

            stop_via_tray(process)
            processes.remove(process)

            process = launch(data_dir, root)
            processes.append(process)
            assert widget_rect() == dragged
            style = user32.GetWindowLongW(window_handle("火山方舟桌面容量"), GWL_EXSTYLE)
            assert not style & WS_EX_TOPMOST
            assert load_preferences(data_dir)["topmost"] is False
            stop_via_tray(process)
            processes.remove(process)

            widget_json = json.loads((data_dir / "widget.json").read_text(encoding="utf-8"))
            widget_json["widget"]["x"] = 100_000
            widget_json["widget"]["y"] = -100_000
            (data_dir / "widget.json").write_text(
                json.dumps(widget_json, ensure_ascii=False), encoding="utf-8"
            )
            process = launch(data_dir, root)
            processes.append(process)
            assert_inside_work_area(widget_rect())
            stop_via_tray(process)
            processes.remove(process)
            print("Native tray/widget smoke passed: hover, menus, empty-click no-op, menu open, "
                  "refresh button, drag, persistence, clamping, exit")
        finally:
            escape_native_ui()
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                if process.returncode:
                    print(process.log_path.read_text(encoding="utf-8", errors="replace"))


if __name__ == "__main__":
    main()
