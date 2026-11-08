"""Optional Windows GUI smoke check; run while the desktop app is open."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import time
from pathlib import Path

from PIL import Image

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts"
WM_CLOSE = 0x0010
PW_RENDERFULLCONTENT = 2


class BitmapInfoHeader(ctypes.Structure):
    _fields_ = (
        ("size", ctypes.wintypes.DWORD),
        ("width", ctypes.wintypes.LONG),
        ("height", ctypes.wintypes.LONG),
        ("planes", ctypes.wintypes.WORD),
        ("bit_count", ctypes.wintypes.WORD),
        ("compression", ctypes.wintypes.DWORD),
        ("size_image", ctypes.wintypes.DWORD),
        ("x_ppm", ctypes.wintypes.LONG),
        ("y_ppm", ctypes.wintypes.LONG),
        ("colors_used", ctypes.wintypes.DWORD),
        ("colors_important", ctypes.wintypes.DWORD),
    )


class BitmapInfo(ctypes.Structure):
    _fields_ = (
        ("header", BitmapInfoHeader),
        ("colors", ctypes.wintypes.DWORD * 3),
    )


user32.GetWindowDC.argtypes = [ctypes.wintypes.HWND]
user32.GetWindowDC.restype = ctypes.c_void_p
user32.PrintWindow.argtypes = [
    ctypes.wintypes.HWND,
    ctypes.c_void_p,
    ctypes.wintypes.UINT,
]
user32.PrintWindow.restype = ctypes.wintypes.BOOL
user32.ReleaseDC.argtypes = [ctypes.wintypes.HWND, ctypes.c_void_p]
user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.SetWindowPos.argtypes = [
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.wintypes.UINT,
]
gdi32.GetDIBits.argtypes = [
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.wintypes.UINT,
    ctypes.wintypes.UINT,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.wintypes.UINT,
]
gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
gdi32.CreateCompatibleBitmap.argtypes = [
    ctypes.c_void_p,
    ctypes.c_int,
    ctypes.c_int,
]
gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.SelectObject.restype = ctypes.c_void_p
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.DeleteDC.argtypes = [ctypes.c_void_p]


def find_window(title, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        hwnd = user32.FindWindowW(None, title)
        if hwnd:
            return hwnd
        time.sleep(0.1)
    raise RuntimeError(f"Window not found: {title}")


def capture(hwnd, filename):
    """Print the window directly so an occluding full-screen app cannot spoil it."""
    rect = ctypes.wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    window_dc = user32.GetWindowDC(hwnd)
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    previous = gdi32.SelectObject(memory_dc, bitmap)
    try:
        if not user32.PrintWindow(hwnd, memory_dc, PW_RENDERFULLCONTENT):
            raise RuntimeError(f"PrintWindow failed for {filename}")
        info = BitmapInfo()
        info.header.size = ctypes.sizeof(BitmapInfoHeader)
        info.header.width = width
        info.header.height = -height
        info.header.planes = 1
        info.header.bit_count = 32
        pixels = ctypes.create_string_buffer(width * height * 4)
        rows = gdi32.GetDIBits(
            memory_dc, bitmap, 0, height, pixels, ctypes.byref(info), 0
        )
        if rows != height:
            raise RuntimeError(f"GetDIBits failed for {filename}")
        image = Image.frombuffer(
            "RGB", (width, height), pixels, "raw", "BGRX", 0, 1
        )
        image.save(ARTIFACTS / filename)
    finally:
        gdi32.SelectObject(memory_dc, previous)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(hwnd, window_dc)


def click_window_offset(hwnd, x, y):
    """Click a point measured in a captured image, including the title bar."""
    rect = ctypes.wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    user32.SetCursorPos(rect.left + x, rect.top + y)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    user32.mouse_event(0x0004, 0, 0, 0, 0)


def find_color_bounds(filename, color):
    """Locate a solid ttk button from the actual rendered layout."""
    image = Image.open(ARTIFACTS / filename).convert("RGB")
    points = [
        (x, y)
        for y in range(image.height)
        for x in range(image.width)
        if image.getpixel((x, y)) == color
    ]
    if not points:
        raise RuntimeError(f"Could not locate color {color} in {filename}")
    xs, ys = zip(*points)
    return min(xs), min(ys), max(xs), max(ys)


def click_window_center(hwnd):
    rect = ctypes.wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise RuntimeError("Could not read button bounds")
    user32.SetCursorPos((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    user32.mouse_event(0x0004, 0, 0, 0, 0)


if __name__ == "__main__":
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    settings = find_window("火山方舟用量查询设置")
    user32.SetWindowPos(settings, ctypes.c_void_p(-1), 0, 0, 0, 0, 0x0053)
    time.sleep(0.3)
    capture(settings, "settings.png")

    # Tk widgets share one native window; locate Save by its rendered accent fill.
    left, top, right, bottom = find_color_bounds("settings.png", (0, 122, 255))
    click_window_offset(settings, (left + right) // 2, (top + bottom) // 2)
    validation = find_window("设置无效")
    user32.SetWindowPos(validation, ctypes.c_void_p(-1), 0, 0, 0, 0, 0x0053)
    time.sleep(0.3)
    capture(validation, "validation.png")

    user32.FindWindowExW.argtypes = [
        ctypes.wintypes.HWND,
        ctypes.wintypes.HWND,
        ctypes.wintypes.LPCWSTR,
        ctypes.wintypes.LPCWSTR,
    ]
    user32.FindWindowExW.restype = ctypes.wintypes.HWND
    ok_button = user32.FindWindowExW(validation, None, "Button", None)
    if not ok_button:
        raise RuntimeError("Validation OK button not found")
    click_window_center(ok_button)
    time.sleep(0.5)
    if user32.FindWindowW(None, "设置无效"):
        raise RuntimeError("Validation dialog did not close")
    user32.PostMessageW(settings, WM_CLOSE, 0, 0)
    print("GUI smoke check passed; screenshots saved under", ARTIFACTS)
