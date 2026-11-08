"""Shared frameless Windows shell, with native sizing and taskbar behaviour."""
from __future__ import annotations

import ctypes
from ctypes import wintypes as wt
import logging
import tkinter as tk
from typing import Callable, Mapping

from .ui_graphics import dpi_scale, native_handle

logger = logging.getLogger(__name__)
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


def resize_hit(x: int, y: int, width: int, height: int, edge: int) -> int:
    """Return native HT* codes; the interior is ordinary client content."""
    left, right, top, bottom = x < edge, x >= width-edge, y < edge, y >= height-edge
    if top:
        return 13 if left else 14 if right else 12
    if bottom:
        return 16 if left else 17 if right else 15
    return 10 if left else 11 if right else 1


class ChromeButton(tk.Canvas):
    def __init__(self, master: tk.Misc, icon: str, command: Callable[[], None],
                 colors: Mapping[str, str]):
        self.icon, self.command, self.colors = icon, command, colors
        self.hover = self.focused = self.pressed = False
        self.scale = dpi_scale(master)
        super().__init__(master, width=round(28*self.scale), height=round(28*self.scale),
                         highlightthickness=0, borderwidth=0, takefocus=True,
                         background=colors["surface"])
        self.bind("<Enter>", lambda e: self._state("hover", True))
        self.bind("<Leave>", lambda e: self._state("hover", False))
        self.bind("<FocusIn>", lambda e: self._state("focused", True))
        self.bind("<FocusOut>", lambda e: self._state("focused", False))
        self.bind("<ButtonPress-1>", lambda e: self._state("pressed", True))
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Return>", lambda e: self.command())
        self.bind("<space>", lambda e: self.command())
        self.draw()

    def _state(self, name: str, value: bool) -> None:
        setattr(self, name, value)
        self.draw()

    def _release(self, event: tk.Event) -> None:
        self.pressed = False
        self.draw()
        if 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height():
            self.command()

    def draw(self) -> None:
        c, s = self.colors, self.scale
        self.configure(background=c["surface"])
        self.delete("all")
        active = self.hover or self.focused or self.pressed
        color = "#FFFFFF" if self.icon == "close" and active else c["text"]
        if active:
            fill = "#D94A51" if self.icon == "close" else c["pressed" if self.pressed else "hover"]
            self.create_oval(2*s, 2*s, 26*s, 26*s, fill=fill, outline="")
        def line(*points: float) -> None:
            self.create_line(*(p*s for p in points), fill=color, width=max(1, round(s)), capstyle="round")
        if self.icon == "close":
            line(10, 10, 18, 18)
            line(18, 10, 10, 18)
        elif self.icon == "minimize":
            line(9, 14, 19, 14)
        else:
            if self.icon == "restore":
                line(12, 9, 19, 9, 19, 16)
            self.create_rectangle(9*s, 11*s if self.icon == "restore" else 9*s,
                                  17*s if self.icon == "restore" else 19*s, 19*s,
                                  outline=color, width=max(1, round(s)))
        if self.focused:
            self.create_line(10*s, 25*s, 18*s, 25*s, fill=c["focus"], width=max(1, round(2*s)))


class WindowChrome:
    def __init__(self, window: tk.Toplevel, title: str, colors: Mapping[str, str],
                 font_family: str, on_close: Callable[[], None]):
        self.window, self.colors, self.on_close = window, colors, on_close
        self.hwnd = 0
        self.old_proc = 0
        self.pending: str | None = None
        self.last_region: tuple[int, int, bool] | None = None
        self._callback = WNDPROC(self._wndproc)  # Retain until the HWND is destroyed.
        self.header = tk.Frame(window, bg=colors["surface"], borderwidth=0)
        self.header.pack(side="top", fill="x", padx=round(12*dpi_scale(window)),
                         pady=(round(8*dpi_scale(window)), 0))
        self.title = tk.Label(self.header, text=title, bg=colors["surface"], fg=colors["text"],
                              font=(font_family, 11, "bold"), borderwidth=0)
        self.title.pack(side="left", padx=(round(4*dpi_scale(window)), 0))
        self.controls = tk.Frame(self.header, bg=colors["surface"], borderwidth=0)
        self.controls.pack(side="right")
        self.buttons = {}
        for name, command in (("minimize", self.minimize), ("maximize", self.toggle_maximize),
                              ("close", on_close)):
            button = ChromeButton(self.controls, name, command, colors)
            button.pack(side="left", padx=round(2*dpi_scale(window)))
            self.buttons[name] = button
        self.actions = tk.Frame(self.header, bg=colors["surface"], borderwidth=0)
        self.actions.pack(side="right", padx=(0, round(8*dpi_scale(window))))
        for widget in (self.header, self.title):
            widget.bind("<ButtonPress-1>", self._drag)
            widget.bind("<Double-Button-1>", lambda e: self.toggle_maximize())
        window._chrome = self
        window.bind("<Map>", self._mapped, add=True)
        window.bind("<Configure>", self._configure, add=True)
        window.bind("<Destroy>", self._destroy, add=True)
        window.bind("<Alt-F4>", lambda e: self.close())
        self.user = ctypes.windll.user32
        self.user.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_ssize_t]
        self.user.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        self.user.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
        self.user.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        self.user.CallWindowProcW.argtypes = [ctypes.c_void_p, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        self.user.CallWindowProcW.restype = LRESULT
        self.user.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        self.user.SendMessageW.restype = LRESULT
        self.user.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                           ctypes.c_int, ctypes.c_int, wt.UINT]
        self.user.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
        self.user.IsZoomed.argtypes = [wt.HWND]
        self.user.IsIconic.argtypes = [wt.HWND]
        self.user.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
        self._install()

    @property
    def normal(self) -> bool:
        return not (self.user.IsZoomed(self.hwnd) or self.user.IsIconic(self.hwnd))

    def _install(self) -> None:
        hwnd = native_handle(self.window)
        if hwnd == self.hwnd and self.old_proc:
            return
        self.hwnd = hwnd
        style = self.user.GetWindowLongPtrW(hwnd, -16)
        self.user.SetWindowLongPtrW(hwnd, -16, style & ~0x00C00000)  # WS_CAPTION
        self.old_proc = self.user.SetWindowLongPtrW(hwnd, -4,
                            ctypes.cast(self._callback, ctypes.c_void_p).value)
        if not self.old_proc:
            raise ctypes.WinError()
        # FRAMECHANGED causes NCCALCSIZE to replace the non-client area now.
        self.user.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x37)

    def _wndproc(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
        try:
            if message in (0x83, 0x85):  # WM_NCCALCSIZE, WM_NCPAINT
                return 0
            if message == 0x86:  # WM_NCACTIVATE: no classic frame painting
                return 1
            if message == 0x84:  # WM_NCHITTEST
                rect = wt.RECT()
                self.user.GetWindowRect(hwnd, ctypes.byref(rect))
                x, y = ctypes.c_short(lparam & 0xffff).value, ctypes.c_short((lparam >> 16) & 0xffff).value
                if self.user.IsZoomed(hwnd):
                    return 1
                return resize_hit(x-rect.left, y-rect.top, rect.right-rect.left,
                                  rect.bottom-rect.top, round(6*dpi_scale(self.window)))
            if message == 0x24:  # WM_GETMINMAXINFO; native maximization uses the work area
                result = self._previous(hwnd, message, wparam, lparam)
                self._max_bounds(hwnd, lparam)
                return result
            if message == 0x46 and self.user.IsZoomed(hwnd):  # WM_WINDOWPOSCHANGING
                result = self._previous(hwnd, message, wparam, lparam)
                # Tk adds its cached native frame margins after GETMINMAXINFO.
                # The frameless client must stop at the work area, not beyond it.
                class Position(ctypes.Structure):
                    _fields_ = [("hwnd", wt.HWND), ("after", wt.HWND),
                                ("x", ctypes.c_int), ("y", ctypes.c_int),
                                ("cx", ctypes.c_int), ("cy", ctypes.c_int), ("flags", wt.UINT)]
                pos = ctypes.cast(lparam, ctypes.POINTER(Position)).contents
                from .ui_graphics import window_work_area
                left, top, right, bottom = window_work_area(self.window)
                pos.x, pos.y, pos.cx, pos.cy = left, top, right-left, bottom-top
                pos.flags &= ~0x3  # Apply both size and position.
                return result
            if message == 0x112 and (wparam & 0xfff0) in (0xf020, 0xf030):
                self._flush_size()  # Save restored size before minimizing/maximizing.
            if message == 0x82:  # WM_NCDESTROY
                old = self.old_proc
                self.user.SetWindowLongPtrW(hwnd, -4, old)
                self.old_proc = 0
                return self.user.CallWindowProcW(old, hwnd, message, wparam, lparam)
        except Exception:
            # Never let a Python exception cross a native WNDPROC boundary.
            logger.exception("Window chrome message failed: %#x", message)
        return self._previous(hwnd, message, wparam, lparam)

    def _previous(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
        return self.user.CallWindowProcW(self.old_proc, hwnd, message, wparam, lparam)

    def _max_bounds(self, hwnd: int, address: int) -> None:
        class MinMax(ctypes.Structure):
            _fields_ = [(name, wt.POINT) for name in ("reserved", "max_size", "max_position", "min_track", "max_track")]
        class MonitorInfo(ctypes.Structure):
            _fields_ = [("size", wt.DWORD), ("monitor", wt.RECT), ("work", wt.RECT), ("flags", wt.DWORD)]
        self.user.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
        self.user.MonitorFromWindow.restype = wt.HANDLE
        self.user.GetMonitorInfoW.argtypes = [wt.HANDLE, ctypes.c_void_p]
        info = MonitorInfo()
        info.size = ctypes.sizeof(info)
        if self.user.GetMonitorInfoW(self.user.MonitorFromWindow(hwnd, 2), ctypes.byref(info)):
            value = ctypes.cast(address, ctypes.POINTER(MinMax)).contents
            value.max_position = wt.POINT(info.work.left-info.monitor.left, info.work.top-info.monitor.top)
            value.max_size = wt.POINT(info.work.right-info.work.left, info.work.bottom-info.work.top)

    def _flush_size(self) -> None:
        controller = getattr(self.window, "_geometry", None)
        if controller:
            controller.flush()

    def close(self) -> str:
        self.on_close()
        return "break"

    def minimize(self) -> None:
        self._flush_size()
        self.window.iconify()

    def toggle_maximize(self) -> None:
        self._flush_size()
        self.window.state("normal" if self.user.IsZoomed(self.hwnd) else "zoomed")
        self._schedule()

    def _drag(self, event: tk.Event) -> None:
        self.user.ReleaseCapture()
        position = ((event.y_root & 0xffff) << 16) | (event.x_root & 0xffff)
        self.user.SendMessageW(self.hwnd, 0xA1, 2, position)  # Native caption move loop

    def _mapped(self, event: tk.Event) -> None:
        if event.widget == self.window:
            self._install()
            self._schedule()

    def _configure(self, event: tk.Event) -> None:
        if event.widget == self.window:
            self._schedule()

    def _schedule(self) -> None:
        if self.pending is None:
            self.pending = self.window.after_idle(self._region)

    def _region(self) -> None:
        self.pending = None
        rect = wt.RECT()
        self.user.GetWindowRect(self.hwnd, ctypes.byref(rect))
        width, height = rect.right-rect.left, rect.bottom-rect.top
        zoomed = bool(self.user.IsZoomed(self.hwnd))
        if zoomed:
            from .ui_graphics import window_work_area
            left, top, right, bottom = window_work_area(self.window)
            if (rect.left, rect.top, rect.right, rect.bottom) != (left, top, right, bottom):
                self.user.SetWindowPos(self.hwnd, None, left, top, right-left, bottom-top, 0x14)
                width, height = right-left, bottom-top
        self.buttons["maximize"].icon = "restore" if zoomed else "maximize"
        self.buttons["maximize"].draw()
        key = width, height, zoomed
        if key == self.last_region or min(width, height) <= 1:
            return
        self.last_region = key
        gdi = ctypes.windll.gdi32
        gdi.CreateRoundRectRgn.argtypes = [ctypes.c_int]*6
        gdi.CreateRoundRectRgn.restype = wt.HANDLE
        self.user.SetWindowRgn.argtypes = [wt.HWND, wt.HANDLE, wt.BOOL]
        radius = round(20*dpi_scale(self.window))
        region = None if zoomed else gdi.CreateRoundRectRgn(0, 0, width+1, height+1, radius*2, radius*2)
        if not self.user.SetWindowRgn(self.hwnd, region, True) and region:
            gdi.DeleteObject.argtypes = [wt.HANDLE]
            gdi.DeleteObject(region)

    def set_theme(self, colors: Mapping[str, str]) -> None:
        self.colors = colors
        for widget in (self.header, self.title, self.actions, self.controls):
            widget.configure(background=colors["surface"])
        self.title.configure(foreground=colors["text"])
        for button in self.buttons.values():
            button.colors = colors
            button.draw()

    def _destroy(self, event: tk.Event) -> None:
        if event.widget != self.window:
            return
        if self.pending:
            self.window.after_cancel(self.pending)
            self.pending = None
        if self.old_proc:
            self.user.SetWindowLongPtrW(self.hwnd, -4, self.old_proc)
            self.old_proc = 0
