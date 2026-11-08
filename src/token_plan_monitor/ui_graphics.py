from __future__ import annotations

import ctypes
import tkinter as tk
import winreg
from dataclasses import dataclass
from typing import Any
from tkinter import ttk

from PIL import Image, ImageDraw, ImageFilter, ImageTk


def rounded_photo(
    width: int,
    height: int,
    *,
    radius: int,
    fill: str,
    outline: str | None = None,
    background: str | None = None,
) -> ImageTk.PhotoImage:
    """Render a smooth rounded surface; the exterior stays transparent."""
    width = max(1, width)
    height = max(1, height)
    scale = 3
    image = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    box = (0, 0, width * scale - 1, height * scale - 1)
    draw.rounded_rectangle(
        box,
        radius=max(0, min(radius, width // 2, height // 2)) * scale,
        fill=fill,
        outline=outline,
        width=scale if outline else 1,
    )
    image = image.resize((width, height), Image.Resampling.LANCZOS)
    if background is not None:
        backing = Image.new("RGBA", image.size, background)
        backing.alpha_composite(image)
        image = backing
    return ImageTk.PhotoImage(image)


class RoundedPanel(tk.Frame):
    """A measured Tk frame with a rounded background and a rectangular content safe area."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        radius: int = 14,
        fill: str,
        outline: str,
        background: str,
        padding: int | tuple[int, int] = (16, 14),
    ):
        super().__init__(master, background=background, borderwidth=0, highlightthickness=0)
        self.radius = radius
        self.fill = fill
        self.outline = outline
        self._image: ImageTk.PhotoImage | None = None
        self._image_size = (0, 0)
        self._background = tk.Label(
            self, background=background, borderwidth=0, highlightthickness=0
        )
        self._background.place(x=0, y=0, relwidth=1, relheight=1)
        self.content = tk.Frame(self, background=fill, borderwidth=0, highlightthickness=0)
        inset = max(3, radius // 2)
        self.content.pack(
            fill="both",
            expand=True,
            padx=inset,
            pady=inset,
            ipadx=padding[0] if isinstance(padding, tuple) else padding,
            ipady=padding[1] if isinstance(padding, tuple) else padding,
        )
        self.bind("<Configure>", self._redraw)

    def _redraw(self, event: tk.Event | None = None) -> None:
        size = (self.winfo_width(), self.winfo_height())
        if size == self._image_size or min(size) <= 1:
            return
        self._image_size = size
        self._image = rounded_photo(
            *size,
            radius=self.radius,
            fill=self.fill,
            outline=self.outline,
        )
        self._background.configure(image=self._image)


class RoundedProgress(tk.Canvas):
    def __init__(self, master: tk.Misc, *, background: str, trough: str, height: int = 7):
        super().__init__(
            master,
            height=height,
            width=1,
            background=background,
            borderwidth=0,
            highlightthickness=0,
        )
        self.trough = trough
        self.value = 0.0
        self.color = "#007AFF"
        self._image: ImageTk.PhotoImage | None = None
        self.bind("<Configure>", lambda event: self._redraw())

    def set(self, value: float, color: str) -> None:
        self.value = min(100.0, max(0.0, value))
        self.color = color
        self._redraw()

    def _redraw(self) -> None:
        width = self.winfo_width()
        height = self.winfo_height()
        if width <= 1 or height <= 1:
            return
        scale = 3
        image = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            (0, 0, width * scale - 1, height * scale - 1),
            radius=height * scale // 2,
            fill=self.trough,
        )
        fill_width = round(width * self.value / 100)
        if fill_width > 0:
            draw.rounded_rectangle(
                (0, 0, max(height, fill_width) * scale - 1, height * scale - 1),
                radius=height * scale // 2,
                fill=self.color,
            )
        image = image.resize((width, height), Image.Resampling.LANCZOS)
        self._image = ImageTk.PhotoImage(image)
        self.delete("all")
        self.create_image(0, 0, image=self._image, anchor="nw")


def set_rounded_region(window: tk.Toplevel, radius: int) -> None:
    """Clip a borderless window to a native rounded region without color-key halos."""
    try:
        window.update_idletasks()
        width = window.winfo_width()
        height = window.winfo_height()
        if width <= 1 or height <= 1:
            return
        gdi32 = ctypes.windll.gdi32
        user32 = ctypes.windll.user32
        gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
        gdi32.CreateRoundRectRgn.restype = ctypes.c_void_p
        user32.SetWindowRgn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
        user32.SetWindowRgn.restype = ctypes.c_int
        region = gdi32.CreateRoundRectRgn(
            0, 0, width + 1, height + 1, radius * 2, radius * 2
        )
        if region:
            result = user32.SetWindowRgn(
                ctypes.c_void_p(native_handle(window)), region, True
            )
            if not result:
                gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
                gdi32.DeleteObject(region)
    except (AttributeError, OSError, tk.TclError):
        return


def enable_rounded_corners(window: tk.Toplevel | tk.Tk) -> bool:
    """Request native Windows 11 rounded corners; older versions simply ignore it."""
    try:
        window.update_idletasks()
        dwmapi = ctypes.windll.dwmapi
        function: Any = dwmapi.DwmSetWindowAttribute
        function.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        function.restype = ctypes.c_long
        preference = ctypes.c_uint32(2)  # DWMWCP_ROUND
        hwnd = ctypes.c_void_p(native_handle(window))
        return function(hwnd, 33, ctypes.byref(preference), ctypes.sizeof(preference)) == 0
    except (AttributeError, OSError, tk.TclError):
        return False


@dataclass(frozen=True)
class WindowEffects:
    rounded: bool = False
    glass: bool = False
    shadow: bool = False


def native_handle(window: tk.Misc) -> int:
    """Tk exposes the client HWND; DWM needs its outer top-level wrapper."""
    user32 = ctypes.windll.user32
    user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    user32.GetAncestor.restype = ctypes.c_void_p
    return user32.GetAncestor(window.winfo_id(), 2) or window.winfo_id()


def transparency_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return bool(winreg.QueryValueEx(key, "EnableTransparency")[0])
    except OSError:
        return False


def apply_window_effects(window: tk.Toplevel, surface: str) -> WindowEffects:
    """Keep foreground opaque, exposing acrylic only through the surface colour.

    Failed/disabled composition leaves the ordinary solid Tk background intact.
    No global alpha is used. A mouse-transparent shadow layer is a fallback
    only for borderless windows when native frame extension is unavailable.
    """
    rounded = enable_rounded_corners(window)
    glass = shadow = False
    try:
        window.attributes("-transparentcolor", "")
    except tk.TclError:
        return WindowEffects(rounded)
    try:
        hwnd = ctypes.c_void_p(native_handle(window))
        dwm = ctypes.windll.dwmapi
        # Extend a narrow native frame for the compositor's soft drop shadow.
        class Margins(ctypes.Structure):
            _fields_ = [(name, ctypes.c_int) for name in ("left", "right", "top", "bottom")]
        extend = dwm.DwmExtendFrameIntoClientArea
        extend.argtypes = [ctypes.c_void_p, ctypes.POINTER(Margins)]
        extend.restype = ctypes.c_long
        shadow = extend(hwnd, ctypes.byref(Margins(1, 1, 1, 1))) == 0
        # Borderless Tk popups need the native drop-shadow class style as well.
        user32 = ctypes.windll.user32
        get_style = user32.GetClassLongPtrW
        set_style = user32.SetClassLongPtrW
        get_style.argtypes = [ctypes.c_void_p, ctypes.c_int]
        get_style.restype = ctypes.c_size_t
        set_style.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t]
        set_style.restype = ctypes.c_size_t
        set_style(hwnd, -26, get_style(hwnd, -26) | 0x00020000)
        if not shadow and window.overrideredirect():
            _ensure_shadow(window)
        if not transparency_enabled():
            return WindowEffects(rounded, False, shadow)

        class Accent(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint32) for name in ("state", "flags", "colour", "animation")]
        class CompositionData(ctypes.Structure):
            _fields_ = [("attribute", ctypes.c_int), ("data", ctypes.c_void_p), ("size", ctypes.c_size_t)]
        # Acrylic tint in AABBGGRR; keep enough translucency to reveal the desktop.
        accent = Accent(4, 0, 0xD9F8F6F4, 0)
        data = CompositionData(19, ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p), ctypes.sizeof(accent))
        compose = ctypes.windll.user32.SetWindowCompositionAttribute
        compose.argtypes = [ctypes.c_void_p, ctypes.POINTER(CompositionData)]
        compose.restype = ctypes.c_int
        if compose(hwnd, ctypes.byref(data)):
            try:
                window.attributes("-transparentcolor", surface)
                glass = True
            except tk.TclError:
                accent.state = 0
                compose(hwnd, ctypes.byref(data))
    except (AttributeError, OSError, tk.TclError):
        pass
    if not shadow:
        try:
            if window.overrideredirect():
                _ensure_shadow(window)
        except (AttributeError, OSError, tk.TclError):
            pass
    return WindowEffects(rounded, glass, shadow)


def configure_rounded_buttons(style: ttk.Style) -> None:
    """Image-backed ttk buttons retain native keyboard/focus/disabled behaviour."""
    if "MacPrimary.background" in style.element_names():
        return
    images = []
    for name, colours in (
        ("Primary", ("#007AFF", "#0062CC", "#006BE0", "#B5CFF2", "#007AFF")),
        ("Secondary", ("#FFFFFF", "#E7EDF5", "#F0F5FC", "#F2F3F6", "#FFFFFF")),
    ):
        states = []
        for index, colour in enumerate(colours):
            outline = "#004FA6" if index == 4 else ("#E0E4EC" if name == "Secondary" else colour)
            states.append(rounded_photo(80, 36, radius=9, fill=colour, outline=outline,
                                        background="#F2F3F6"))
        images.extend(states)
        element = f"Mac{name}.background"
        style.element_create(element, "image", states[0],
                             ("disabled", states[3]), ("pressed", states[1]),
                             ("active", states[2]), ("focus", states[4]),
                             border=11, sticky="nsew")
        style.layout(f"{name}.TButton", [(element, {"sticky": "nsew", "children": [
            ("Button.padding", {"sticky": "nsew", "children": [
                ("Button.label", {"sticky": "nsew"})]})]})])
    # Tcl image elements do not retain Python PhotoImage references.
    style.master._mac_button_images = images


class _ShadowLayer:
    """A per-pixel-alpha, mouse-transparent shadow used only if DWM fails."""

    def __init__(self, owner: tk.Toplevel):
        self.owner = owner
        self.window = tk.Toplevel(owner.master)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-toolwindow", True)
        self.window.attributes("-disabled", True)
        self._pending: str | None = None
        self._closed = False
        owner.bind("<Configure>", self._schedule, add=True)
        owner.bind("<Map>", self._schedule, add=True)
        owner.bind("<Unmap>", self._hide, add=True)
        owner.bind("<Destroy>", self._destroy, add=True)
        self._schedule()

    def _schedule(self, event: tk.Event | None = None) -> None:
        if event is not None and event.widget != self.owner:
            return
        if not self._closed and self._pending is None:
            self._pending = self.owner.after(20, self._sync)

    def _hide(self, event: tk.Event) -> None:
        if event.widget == self.owner:
            self.window.withdraw()

    def _destroy(self, event: tk.Event) -> None:
        if event.widget != self.owner or self._closed:
            return
        self._closed = True
        if self._pending is not None:
            self.owner.after_cancel(self._pending)
        self.window.destroy()

    def _sync(self) -> None:
        self._pending = None
        if self._closed or not self.owner.winfo_viewable():
            self.window.withdraw()
            return
        try:
            if self.window.state() == "withdrawn":
                self.window.geometry(f"{self.owner.winfo_width()+36}x{self.owner.winfo_height()+36}"
                                     f"+{self.owner.winfo_rootx()-18}+{self.owner.winfo_rooty()-12}")
                self.window.deiconify()
                # Let Tk finish creating and styling its native wrapper first.
                self._pending = self.owner.after(20, self._sync)
                return
            self._paint()
        except (AttributeError, OSError, tk.TclError):
            self.window.withdraw()

    def _paint(self) -> None:
        from ctypes import wintypes as wt
        width, height = self.owner.winfo_width() + 36, self.owner.winfo_height() + 36
        x, y = self.owner.winfo_rootx() - 18, self.owner.winfo_rooty() - 12
        self.window.geometry(f"{width}x{height}+{x}+{y}")
        self.window.attributes("-topmost", self.owner.attributes("-topmost"))
        self.window.update_idletasks()
        hwnd = ctypes.c_void_p(native_handle(self.window))
        user, gdi = ctypes.windll.user32, ctypes.windll.gdi32
        get_style, set_style = user.GetWindowLongPtrW, user.SetWindowLongPtrW
        get_style.argtypes, get_style.restype = [ctypes.c_void_p, ctypes.c_int], ctypes.c_size_t
        set_style.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t]
        set_style.restype = ctypes.c_size_t
        # LAYERED | TRANSPARENT | NOACTIVATE | TOOLWINDOW
        set_style(hwnd, -20, get_style(hwnd, -20) | 0x080800A0)
        image = Image.new("RGBA", (width, height))
        ImageDraw.Draw(image).rounded_rectangle((18, 18, width-18, height-18),
                                                radius=20, fill=(0, 0, 0, 65))
        image = image.filter(ImageFilter.GaussianBlur(9))
        # RGB is zero, so BGRA bytes are already premultiplied.
        pixels = image.tobytes("raw", "BGRA")
        class Header(ctypes.Structure):
            _fields_ = [("size", wt.DWORD), ("width", wt.LONG), ("height", wt.LONG),
                        ("planes", wt.WORD), ("bits", wt.WORD),
                        ("compression", wt.DWORD), ("image_size", wt.DWORD),
                        ("xppm", wt.LONG), ("yppm", wt.LONG),
                        ("used", wt.DWORD), ("important", wt.DWORD)]
        class BitmapInfo(ctypes.Structure):
            _fields_ = [("header", Header), ("colours", wt.DWORD * 3)]
        class Blend(ctypes.Structure):
            _fields_ = [(name, wt.BYTE) for name in ("operation", "flags", "alpha", "format")]
        info = BitmapInfo(Header(ctypes.sizeof(Header), width, -height, 1, 32))
        gdi.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
        gdi.CreateCompatibleDC.restype = ctypes.c_void_p
        gdi.CreateDIBSection.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wt.UINT,
                                       ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, wt.DWORD]
        gdi.CreateDIBSection.restype = ctypes.c_void_p
        gdi.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        gdi.SelectObject.restype = ctypes.c_void_p
        gdi.DeleteObject.argtypes = [ctypes.c_void_p]
        gdi.DeleteDC.argtypes = [ctypes.c_void_p]
        dc = gdi.CreateCompatibleDC(None)
        bits = ctypes.c_void_p()
        bitmap = gdi.CreateDIBSection(dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
        if not dc or not bitmap or not bits.value:
            if bitmap:
                gdi.DeleteObject(bitmap)
            if dc:
                gdi.DeleteDC(dc)
            self.window.withdraw()
            return
        old = gdi.SelectObject(dc, bitmap)
        try:
            ctypes.memmove(bits, pixels, len(pixels))
            update = user.UpdateLayeredWindow
            update.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(wt.POINT),
                               ctypes.POINTER(wt.SIZE), ctypes.c_void_p, ctypes.POINTER(wt.POINT),
                               wt.DWORD, ctypes.POINTER(Blend), wt.DWORD]
            update.restype = wt.BOOL
            if not update(hwnd, None, ctypes.byref(wt.POINT(x, y)),
                          ctypes.byref(wt.SIZE(width, height)), dc, ctypes.byref(wt.POINT()),
                          0, ctypes.byref(Blend(0, 0, 255, 1)), 2):
                self.window.withdraw()
                return
            user.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                          ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                          ctypes.c_int, wt.UINT]
            user.SetWindowPos(hwnd, ctypes.c_void_p(native_handle(self.owner)), 0, 0, 0, 0, 0x13)
        finally:
            gdi.SelectObject(dc, old)
            gdi.DeleteObject(bitmap)
            gdi.DeleteDC(dc)


def _ensure_shadow(window: tk.Toplevel) -> None:
    if not hasattr(window, "_material_shadow"):
        window._material_shadow = _ShadowLayer(window)
    else:
        window._material_shadow._schedule()


def install_window_effects(window: tk.Toplevel, surface: str, radius: int | None = None) -> None:
    """Reapply after Tk has finished mapping/rebuilding the outer HWND."""
    pending: str | None = None

    def apply() -> None:
        nonlocal pending
        pending = None
        if window.winfo_viewable():
            window._window_effects = apply_window_effects(window, surface)
            if radius is not None:
                set_rounded_region(window, radius)

    def mapped(event: tk.Event) -> None:
        nonlocal pending
        if event.widget == window and pending is None:
            pending = window.after(30, apply)

    def destroyed(event: tk.Event) -> None:
        nonlocal pending
        if event.widget == window and pending is not None:
            window.after_cancel(pending)
            pending = None

    window.bind("<Map>", mapped, add=True)
    window.bind("<Destroy>", destroyed, add=True)
