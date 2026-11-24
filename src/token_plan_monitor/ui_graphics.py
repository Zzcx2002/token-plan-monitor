from __future__ import annotations

import contextlib
import ctypes
import sys
import tkinter as tk
from collections import OrderedDict
from pathlib import Path
from ctypes import wintypes as wt
try:
    import winreg
except ModuleNotFoundError:
    winreg = None
from dataclasses import dataclass
from functools import lru_cache
from tkinter import ttk
from typing import Any, Callable, Iterator, Mapping

# Keep the native callback type in this low-level module so window_chrome and
# the override-redirect DPI watcher can share it without circular imports.
LRESULT = ctypes.c_ssize_t
WNDPROC = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
    LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM
)

#: Tcl notifier service modes (generic/tclNotify.c). NONE makes
#: Tcl_ServiceAll() a no-op; Tk modal loops (including native size/move)
#: temporarily use ALL while nested dispatch is expected.
TCL_SERVICE_NONE = 0


class TclServiceGuardUnavailable(RuntimeError):
    """The native reentrancy guard could not be bound on Windows."""


# Cache successful bindings only. An early lookup before Tk has loaded Tcl must
# not permanently disable every later guard. The source records the actual DLL
# used so the frozen self-check can prove this is not a silent no-op.
_TCL_SERVICE_FUNCTIONS: tuple[Any, Any] | None = None
_TCL_SERVICE_SOURCE = "not resolved"


def _loaded_tcl_path(kernel32: Any, dll_name: str) -> Path | None:
    """Return the full path of Tcl already loaded by _tkinter, if available."""
    try:
        kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wt.HMODULE
        kernel32.GetModuleFileNameW.argtypes = [
            wt.HMODULE, wt.LPWSTR, wt.DWORD,
        ]
        kernel32.GetModuleFileNameW.restype = wt.DWORD
        handle = kernel32.GetModuleHandleW(dll_name)
        if not handle:
            return None
        buffer = ctypes.create_unicode_buffer(32768)
        length = kernel32.GetModuleFileNameW(handle, buffer, len(buffer))
        return Path(buffer.value).resolve() if length else None
    except (AttributeError, OSError):
        return None


def _tcl_dll_candidates() -> list[str]:
    """Prefer Tk's loaded Tcl, then package-relative copies, never PATH first."""
    candidates: list[str] = []
    kernel32 = getattr(getattr(ctypes, "windll", None), "kernel32", None)
    for name in ("tcl86t.dll", "tcl86.dll"):
        if kernel32 is not None:
            loaded = _loaded_tcl_path(kernel32, name)
            if loaded is not None:
                candidates.append(str(loaded))
        try:
            import _tkinter
            candidates.append(str(Path(_tkinter.__file__).resolve().parent / name))
        except (ImportError, AttributeError):
            pass
        bundle = getattr(sys, "_MEIPASS", None)
        if bundle:
            candidates.append(str(Path(bundle) / name))
        if getattr(sys, "frozen", False):
            candidates.append(str(Path(sys.executable).resolve().parent / "_internal" / name))
        # Last-resort compatibility for alternative Python distributions. All
        # explicit package paths above take precedence over DLL search order.
        candidates.append(name)
    return list(dict.fromkeys(candidates))


def _tcl_service_mode_functions() -> tuple[Any, Any] | tuple[None, None]:
    """Bind Tcl service-mode exports from the same Tcl used by Tk."""
    global _TCL_SERVICE_FUNCTIONS, _TCL_SERVICE_SOURCE
    if _TCL_SERVICE_FUNCTIONS is not None:
        return _TCL_SERVICE_FUNCTIONS
    if sys.platform != "win32":
        _TCL_SERVICE_SOURCE = "unsupported platform"
        return None, None
    windll_factory = getattr(ctypes, "WinDLL", None)
    if windll_factory is None:
        _TCL_SERVICE_SOURCE = "ctypes.WinDLL unavailable"
        return None, None
    errors: list[str] = []
    for candidate in _tcl_dll_candidates():
        # Do not skip a loaded path merely because a mock/special filesystem
        # does not expose it; ctypes is the authority on whether it can load.
        try:
            tcl = windll_factory(candidate)
            setter = tcl.Tcl_SetServiceMode
            getter = tcl.Tcl_GetServiceMode
        except (OSError, AttributeError) as exc:
            errors.append(f"{candidate}: {exc}")
            continue
        setter.argtypes = [ctypes.c_int]
        setter.restype = ctypes.c_int
        getter.argtypes = []
        getter.restype = ctypes.c_int
        _TCL_SERVICE_FUNCTIONS = (setter, getter)
        _TCL_SERVICE_SOURCE = candidate
        return setter, getter
    _TCL_SERVICE_SOURCE = "; ".join(errors) or "no Tcl DLL candidates"
    return None, None


def tcl_service_guard_status() -> tuple[bool, str]:
    """Return availability and the resolved DLL path or diagnostic reason."""
    setter, getter = _tcl_service_mode_functions()
    return setter is not None and getter is not None, _TCL_SERVICE_SOURCE


def require_tcl_service_guard() -> tuple[Any, Any] | tuple[None, None]:
    """Fail closed on Windows instead of running native calls unprotected."""
    functions = _tcl_service_mode_functions()
    if sys.platform == "win32" and functions[0] is None:
        raise TclServiceGuardUnavailable(
            f"Tcl service-mode exports unavailable: {_TCL_SERVICE_SOURCE}"
        )
    return functions


def defer_tcl_events() -> int | None:
    """Force TCL_SERVICE_NONE around a message-pumping native call.

    Returns the previous mode for :func:`restore_tcl_events`; ``None`` is only
    used off Windows. Missing Windows exports raise instead of silently turning
    every native-boundary guard into a no-op.

    While a Python command runs, Tcl's per-thread ``tcl_tstate`` is NULL. If a
    ctypes call into user32 synchronously dispatches window messages while the
    notifier is in TCL_SERVICE_ALL (used by Tk's native modal loops),
    Tk's WndProc pumps a queued after-idle Python
    command whose ENTER_PYTHON then calls ``PyEval_RestoreThread(NULL)`` -- a
    Py_FatalError that aborts the process (0xc0000409) with no Python
    traceback. NONE turns the nested Tcl_ServiceAll() into a no-op; queued
    events run later in the normal event loop, where tcl_tstate is valid.
    """
    setter, _ = require_tcl_service_guard()
    if setter is None:
        return None
    return setter(TCL_SERVICE_NONE)


def restore_tcl_events(previous: int | None) -> None:
    """Restore the mode saved by :func:`defer_tcl_events`."""
    if previous is None:
        return
    setter, _ = _tcl_service_mode_functions()
    setter(previous)


@contextlib.contextmanager
def tcl_events_deferred() -> Iterator[None]:
    """Context-manager form of the defer/restore guard for ordinary call sites."""
    previous = defer_tcl_events()
    try:
        yield
    finally:
        restore_tcl_events(previous)

from PIL import Image, ImageDraw, ImageFilter, ImageTk
from .appearance import ACRYLIC_TINT_ALPHA, LIGHT, RADIUS, Theme


def dpi_scale(widget: tk.Misc) -> float:
    return max(0.75, float(widget.tk.call("tk", "scaling")) / (96 / 72))


class _OSVERSIONINFOEXW(ctypes.Structure):
    _fields_ = [
        ("dwOSVersionInfoSize", ctypes.c_ulong),
        ("dwMajorVersion", ctypes.c_ulong),
        ("dwMinorVersion", ctypes.c_ulong),
        ("dwBuildNumber", ctypes.c_ulong),
        ("dwPlatformId", ctypes.c_ulong),
        ("szCSDVersion", ctypes.c_wchar * 128),
        ("wServicePackMajor", ctypes.c_ushort),
        ("wServicePackMinor", ctypes.c_ushort),
        ("wSuiteMask", ctypes.c_ushort),
        ("wProductType", ctypes.c_byte),
        ("wReserved", ctypes.c_byte),
    ]


@lru_cache(maxsize=1)
def windows_build() -> int:
    """Real OS build number. RtlGetVersion ignores the manifest compat shim."""
    try:
        ntdll = ctypes.windll.ntdll
        info = _OSVERSIONINFOEXW()
        info.dwOSVersionInfoSize = ctypes.sizeof(info)
        if ntdll.RtlGetVersion(ctypes.byref(info)) == 0:
            return int(info.dwBuildNumber)
    except (AttributeError, OSError):
        pass
    return 0


def is_windows_11() -> bool:
    return windows_build() >= 22000


def native_dpi(window: tk.Misc) -> int:
    try:
        user32 = ctypes.windll.user32
        user32.GetDpiForWindow.argtypes = [ctypes.c_void_p]
        user32.GetDpiForWindow.restype = ctypes.c_uint
        dpi = int(user32.GetDpiForWindow(native_handle(window)))
        if dpi > 0:
            return dpi
        user32.GetDpiForSystem.restype = ctypes.c_uint
        return int(user32.GetDpiForSystem()) or 96
    except (AttributeError, OSError, tk.TclError):
        return 96


def calibrate_tk_scaling(window: tk.Misc) -> float:
    """Pin Tk's process-global scaling to the monitor DPI before fonts are built."""
    dpi = native_dpi(window)
    current = float(window.tk.call("tk", "scaling")) * 72.0
    if round(current) != round(float(dpi)):
        window.tk.call("tk", "scaling", dpi / 72.0)
    return dpi / 96.0


def invalidate_scaled_caches() -> None:
    """Drop raster caches keyed on physical pixels after a DPI change."""
    _gradient_strip.cache_clear()
    rounded_image.cache_clear()


def window_work_area(window: tk.Misc) -> tuple[int,int,int,int]:
    from ctypes import wintypes as wt
    class MonitorInfo(ctypes.Structure):
        _fields_=[("size",wt.DWORD),("monitor",wt.RECT),("work",wt.RECT),("flags",wt.DWORD)]
    try:
        user=ctypes.windll.user32
        user.MonitorFromWindow.argtypes=[ctypes.c_void_p,wt.DWORD]
        user.MonitorFromWindow.restype=ctypes.c_void_p
        user.GetMonitorInfoW.argtypes=[ctypes.c_void_p,ctypes.c_void_p]
        monitor=user.MonitorFromWindow(native_handle(window),2)
        info=MonitorInfo()
        info.size=ctypes.sizeof(info)
        if user.GetMonitorInfoW(monitor,ctypes.byref(info)):
            rect=info.work
            return rect.left,rect.top,rect.right,rect.bottom
    except (AttributeError,OSError,tk.TclError):
        pass
    return 0,0,window.winfo_screenwidth(),window.winfo_screenheight()


def mix_color(first: str, second: str, amount: float) -> str:
    a, b = tuple(bytes.fromhex(first[1:])), tuple(bytes.fromhex(second[1:]))
    return "#" + "".join(f"{round(x+(y-x)*amount):02x}" for x, y in zip(a, b))


@lru_cache(maxsize=96)
def _gradient_strip(length: int, start: str, end: str, horizontal: bool) -> Image.Image:
    strip = Image.new("RGBA", (length, 1) if horizontal else (1, length))
    strip.putdata([tuple(bytes.fromhex(mix_color(start, end, i/max(1, length-1))[1:])) + (255,)
                   for i in range(length)])
    return strip


def gradient_image(width: int, height: int, start: str, end: str, horizontal: bool = False) -> Image.Image:
    return _gradient_strip(width if horizontal else height,start,end,horizontal).resize((width,height))


@lru_cache(maxsize=64)
def rounded_image(width: int, height: int, radius: int, fill: str,
                  end_fill: str | None = None, outline: str | None = None,
                  shadow: bool = False) -> Image.Image:
    scale = 3
    size = (max(1, width)*scale, max(1, height)*scale)
    inset = 7*scale if shadow else 0
    bottom = size[1]-1-(9*scale if shadow else 0)
    box = (inset, inset, size[0]-1-inset, bottom)
    mask = Image.new("L", size)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=radius*scale, fill=255)
    image = gradient_image(*size, fill, end_fill or fill).copy()
    image.putalpha(mask)
    if outline:
        ImageDraw.Draw(image).rounded_rectangle(box, radius=radius*scale,
                                               outline=outline, width=scale)
    if shadow:
        under = Image.new("RGBA", size)
        ImageDraw.Draw(under).rounded_rectangle((box[0],box[1]+2*scale,box[2],box[3]+2*scale),
                                               radius=radius*scale, fill=(0,0,0,16))
        under = under.filter(ImageFilter.GaussianBlur(3*scale))
        under.alpha_composite(image)
        image = under
    return image.resize((max(1,width), max(1,height)), Image.Resampling.LANCZOS)


def rounded_photo(width: int, height: int, *, radius: int, fill: str,
                  outline: str | None = None, background: str | None = None,
                  end_fill: str | None = None, shadow: bool = False) -> ImageTk.PhotoImage:
    image = rounded_image(width,height,radius,fill,end_fill,outline,shadow)
    if background is not None:
        backing = Image.new("RGBA", image.size, background)
        backing.alpha_composite(image)
        image = backing
    return ImageTk.PhotoImage(image)


def _rounded_panel_photo(panel: tk.Misc, width: int, height: int, *, radius: int,
                         fill: str, outline: str, background: str) -> ImageTk.PhotoImage:
    """Reuse Tk images when a provider/theme rebuild lands on the same geometry."""
    key = (width, height, radius, fill, outline, background, True)
    top = panel.winfo_toplevel()
    cache = getattr(top, "_rounded_panel_photos", None)
    if cache is None:
        cache = OrderedDict()
        top._rounded_panel_photos = cache
    photo = cache.get(key)
    if photo is None:
        photo = rounded_photo(
            width, height, radius=radius, fill=fill, outline=outline,
            shadow=True, background=background,
        )
        cache[key] = photo
        if len(cache) > 48:
            cache.popitem(last=False)
    else:
        cache.move_to_end(key)
    return photo


class RoundedPanel(tk.Frame):
    def __init__(self, master: tk.Misc, *, radius: int = RADIUS["panel"], fill: str,
                 outline: str | None, background: str, padding: int | tuple[int,int] = (16,14)):
        super().__init__(master, background=background, borderwidth=0, highlightthickness=0)
        self._radius_dip, self._padding = radius, padding
        self.radius = round(radius*dpi_scale(master))
        self.fill, self.outline = fill, outline
        self._image = None
        self._image_size = (0,0)
        self._background = tk.Label(self, background=background, borderwidth=0, highlightthickness=0)
        self._background.place(x=0,y=0,relwidth=1,relheight=1)
        self.content = tk.Frame(self,background=fill,borderwidth=0,highlightthickness=0)
        inset = max(round(14*dpi_scale(master)),self.radius//2)
        self._inset = inset
        self.content.pack(fill="both",expand=True,padx=inset,pady=inset,
                          ipadx=round((padding[0] if isinstance(padding,tuple) else padding)*dpi_scale(master)),
                          ipady=round((padding[1] if isinstance(padding,tuple) else padding)*dpi_scale(master)))
        self.bind("<Configure>", self._redraw)

    def refresh_scale(self) -> None:
        scale = dpi_scale(self)
        self.radius = round(self._radius_dip*scale)
        inset = max(round(14*scale), self.radius//2)
        self._inset = inset
        pad_x = self._padding[0] if isinstance(self._padding, tuple) else self._padding
        pad_y = self._padding[1] if isinstance(self._padding, tuple) else self._padding
        self.content.pack_configure(padx=inset, pady=inset,
                                    ipadx=round(pad_x*scale), ipady=round(pad_y*scale))
        self._image_size = (0,0)
        self._redraw()

    def _redraw(self, event: tk.Event | None = None) -> None:
        size = self.winfo_width(), self.winfo_height()
        if size == self._image_size or min(size)<=1:
            return
        self._image_size = size
        self._image = _rounded_panel_photo(
            self, *size, radius=self.radius, fill=self.fill, outline=self.outline,
            background=str(self.cget("background")),
        )
        self._background.configure(image=self._image)

    def set_theme(self, colors: Mapping[str,str]) -> None:
        self.fill, self.outline = colors["card"], colors["border"]
        self.configure(background=colors["surface"])
        self._background.configure(background=colors["surface"])
        self.content.configure(background=self.fill)
        self._image_size = (0,0)
        self._redraw()


class RoundedProgress(tk.Canvas):
    def __init__(self, master: tk.Misc, *, background: str, trough: str, height: int = 6):
        super().__init__(master,height=height,width=1,background=background,borderwidth=0,highlightthickness=0)
        self.trough,self.value,self.color,self.start_color = trough,0.0,"#007AFF","#55B2FF"
        self._image = None
        self._key = None
        self.bind("<Configure>",lambda event:self._redraw())

    def set(self, value: float, color: str, start: str | None = None) -> None:
        self.value = min(100.0,max(0.0,value))
        self.color,self.start_color = color,start or mix_color(color,"#FFFFFF",0.3)
        self._redraw()

    def _redraw(self) -> None:
        width,height = self.winfo_width(),self.winfo_height()
        key = width,height,self.value,self.color,self.start_color,self.trough
        if min(width,height)<=1 or key==self._key:
            return
        self._key = key
        scale=3
        image=rounded_image(width,height,height//2,self.trough).resize((width*scale,height*scale))
        filled=round(width*scale*self.value/100)
        if filled>0:
            mask=Image.new("L",image.size)
            ImageDraw.Draw(mask).rounded_rectangle((0,0,max(height*scale,filled)-1,height*scale-1),
                                                   radius=height*scale//2,fill=255)
            # Crop after rounding so a tiny positive quota never overstates its width.
            ImageDraw.Draw(mask).rectangle((filled,0,width*scale,height*scale),fill=0)
            overlay=Image.new("RGBA",image.size)
            overlay.paste(gradient_image(filled,image.height,self.start_color,self.color,True),(0,0))
            image.paste(overlay,(0,0),mask)
        image=image.resize((width,height),Image.Resampling.LANCZOS)
        self._image=ImageTk.PhotoImage(image)
        self.delete("all")
        self.create_image(0,0,image=self._image,anchor="nw")


def configure_rounded_buttons(style: ttk.Style, theme: Theme = LIGHT) -> None:
    colors=theme.colors
    scale=dpi_scale(style.master)
    px=lambda value:max(1,round(value*scale))
    tag=f"{theme.name}{round(scale*100)}"
    cache=getattr(style.master,"_mac_button_images",{})
    for name in ("Primary","Secondary"):
        element=f"Mac{tag}{name}.background"
        if element not in style.element_names():
            images=[]
            for state in ("normal","disabled","pressed","active","focus"):
                if name=="Primary":
                    top=colors[{"normal":"button_top","focus":"button_top","active":"button_hover",
                                "pressed":"button_pressed","disabled":"button_disabled"}[state]]
                    bottom=colors["button_bottom"] if state in ("normal","focus","active") else top
                else:
                    top=bottom=colors[{"normal":"card","focus":"card","active":"hover",
                                       "pressed":"pressed","disabled":"surface"}[state]]
                images.append(rounded_photo(px(88),px(38),radius=px(RADIUS["button"]),fill=top,end_fill=bottom,
                    outline=colors["focus"] if state=="focus" else None,
                    background=colors["surface"],shadow=False))
            style.element_create(element,"image",images[0],("disabled",images[1]),
                                 ("pressed",images[2]),("active",images[3]),("focus",images[4]),
                                 border=px(12),padding=0,sticky="nsew")
            cache[element]=images
        style.layout(f"{name}.TButton",[(element,{"sticky":"nsew","children":[
            ("Button.padding",{"sticky":"nsew","children":[("Button.label",{"sticky":"nsew"})]})]})])
    # No raster backing: its baked corners form a halo over acrylic surfaces.
    style.layout("Toolbar.TButton",[("Button.padding",{"sticky":"nsew","children":[
        ("Button.label",{"sticky":"nsew"})]})])
    style.map("Toolbar.TButton", background=[("pressed",colors["pressed"]),
              ("active",colors["hover"]), ("focus",colors["info_surface"])])
    entry=f"Mac{tag}.entry"
    if entry not in style.element_names():
        images=[rounded_photo(px(80),px(36),radius=px(RADIUS["input"]),fill=colors["hover"],outline=None if key=="border" else colors[key],
                              background=colors["card"]) for key in ("border","focus","error")]
        style.element_create(entry,"image",images[0],("invalid",images[2]),("focus",images[1]),border=px(10),padding=0,sticky="nsew")
        cache[entry]=images
    style.layout("TEntry",[(entry,{"sticky":"nsew","children":[
        ("Entry.padding",{"sticky":"nsew","children":[("Entry.textarea",{"sticky":"nsew"})]})]})])
    segment=f"Mac{tag}.segment"
    if segment not in style.element_names():
        images=[rounded_photo(px(90),px(34),radius=px(RADIUS["input"]),fill=colors[fill],outline=colors[border] if border=="focus" else None,
                              background=colors["card"])
                for fill,border in (("card","border"),("info_surface","accent"),("hover","border"),("card","focus"))]
        style.element_create(segment,"image",images[0],("focus",images[3]),
                             ("selected",images[1]),("active",images[2]),border=px(10),padding=0,sticky="nsew")
        cache[segment]=images
    style.layout("Theme.TRadiobutton",[(segment,{"sticky":"nsew","children":[
        ("Radiobutton.padding",{"sticky":"nsew","children":[("Radiobutton.label",{"sticky":"nsew"})]})]})])
    style.master._mac_button_images=cache


# Scrollbar geometry in DIPs; clam's built-in warm-grey trough/thumb colours
# clash with both themes (most visibly the black-grey mix in dark mode), so the
# whole widget is restyled from the shared appearance tokens.
SCROLLBAR_WIDTH = 10
SCROLLBAR_ARROW = 10


def scrollbar_palette(theme: Theme) -> dict[str, str]:
    """Pure colour derivation for the themed ttk scrollbars."""
    colors = theme.colors
    thumb = mix_color(colors["muted"], colors["trough"], 0.35)
    return {
        "trough": colors["trough"],
        "thumb": thumb,
        "arrow": thumb,
        "active": colors["accent"],
        "pressed": colors["accent_active"],
        "disabled": colors["border"],
    }


def configure_scrollbars(style: ttk.Style, theme: Theme = LIGHT) -> None:
    """Restyle clam's vertical/horizontal scrollbars with theme tokens.

    Pure style configuration (no bitmap elements): Tk keeps sizing the thumb
    proportionally to the visible fraction, and re-running this on theme/DPI
    switches re-colours every existing scrollbar automatically.
    """
    palette = scrollbar_palette(theme)
    px = lambda value: max(1, round(value * dpi_scale(style.master)))
    state_colors = [
        ("disabled", palette["disabled"]),
        ("pressed", palette["pressed"]),
        ("active", palette["active"]),
    ]
    for orientation in ("Vertical", "Horizontal"):
        name = f"{orientation}.TScrollbar"
        style.configure(
            name,
            width=px(SCROLLBAR_WIDTH),
            arrowsize=px(SCROLLBAR_ARROW),
            troughcolor=palette["trough"],
            background=palette["thumb"],
            lightcolor=palette["thumb"],
            darkcolor=palette["thumb"],
            bordercolor=palette["trough"],
            arrowcolor=palette["arrow"],
            relief="flat",
            borderwidth=0,
            gripcount=0,
        )
        style.map(
            name,
            background=state_colors,
            lightcolor=state_colors,
            darkcolor=state_colors,
            arrowcolor=state_colors,
        )


# clam's readonly combobox map hard-codes light warm-greys (#dcdad5 field,
# #4a6984 focused field) and its popdown is a classic Listbox that resolves
# Windows system colors (white background), so dark mode needs an explicit
# token-driven restyle of both the ttk widget and the option database.
def combobox_palette(theme: Theme) -> dict[str, str]:
    """Pure colour derivation for themed ttk comboboxes and their popdown."""
    colors = theme.colors
    return {
        "field": colors["card"],
        "background": colors["card"],
        "foreground": colors["text"],
        "border": colors["border"],
        "arrow": colors["text"],
        "muted": colors["muted"],
        "hover": colors["hover"],
        "pressed": colors["pressed"],
        "disabled_field": colors["trough"],
        "select_background": colors["accent"],
        "select_foreground": colors["button_text"],
    }


def configure_combobox(style: ttk.Style, theme: Theme = LIGHT) -> None:
    """Restyle clam's TCombobox and its classic popdown Listbox per theme."""
    palette = combobox_palette(theme)
    style.configure(
        "TCombobox",
        fieldbackground=palette["field"],
        background=palette["background"],
        foreground=palette["foreground"],
        bordercolor=palette["border"],
        lightcolor=palette["border"],
        darkcolor=palette["border"],
        arrowcolor=palette["arrow"],
        insertcolor=palette["foreground"],
        selectbackground=palette["select_background"],
        selectforeground=palette["select_foreground"],
        relief="flat",
    )
    style.map(
        "TCombobox",
        fieldbackground=[
            ("disabled", palette["disabled_field"]),
            # The explicit readonly+focus pair must outrank plain readonly:
            # clam otherwise forces its steel-blue #4a6984 focus field.
            (("readonly", "focus"), palette["field"]),
            ("readonly", palette["field"]),
        ],
        foreground=[
            ("disabled", palette["muted"]),
            (("readonly", "focus"), palette["foreground"]),
            ("readonly", palette["foreground"]),
        ],
        background=[
            ("disabled", palette["field"]),
            ("pressed", palette["pressed"]),
            ("active", palette["hover"]),
        ],
        bordercolor=[("focus", palette["select_background"])],
        lightcolor=[("focus", palette["select_background"])],
        darkcolor=[("focus", palette["select_background"])],
        arrowcolor=[("disabled", palette["muted"])],
    )
    style.configure(
        "ComboboxPopdownFrame",
        background=palette["field"],
        bordercolor=palette["border"],
        lightcolor=palette["border"],
        darkcolor=palette["border"],
        relief="solid",
        borderwidth=1,
    )
    # The popdown holds a classic Listbox, not a ttk widget; the option
    # database is the only seam that themes a popdown created after this call.
    master = style.master
    for option, value in (
        ("*TCombobox*Listbox.background", palette["field"]),
        ("*TCombobox*Listbox.foreground", palette["foreground"]),
        ("*TCombobox*Listbox.selectBackground", palette["select_background"]),
        ("*TCombobox*Listbox.selectForeground", palette["select_foreground"]),
    ):
        master.option_add(option, value)


def recolor_combobox_popdown(combo: ttk.Combobox, palette: Mapping[str, str]) -> None:
    """Re-theme a popdown Listbox that already exists.

    Option-database entries only affect popdowns created later, and the
    popdown Toplevel is not reachable through ``winfo_children``; address its
    classic Listbox by the fixed Tk path ttk::combobox assigns it.
    """
    listbox_path = f"{combo}.popdown.f.l"
    try:
        if not combo.tk.call("winfo", "exists", listbox_path):
            return
        combo.tk.call(
            listbox_path, "configure",
            "-background", palette["field"],
            "-foreground", palette["foreground"],
            "-selectbackground", palette["select_background"],
            "-selectforeground", palette["select_foreground"],
        )
    except tk.TclError:
        pass


class Tooltip:
    def __init__(self, widget: tk.Misc, text: str, colors: Callable[[],Mapping[str,str]]):
        self.widget,self.text,self.colors=widget,text,colors
        self.window=None
        self.pending=None
        widget.bind("<Enter>",self._enter,add=True)
        widget.bind("<Leave>",self.hide,add=True)
        widget.bind("<ButtonPress>",self.hide,add=True)
        widget.bind("<Destroy>",self.hide,add=True)

    def _enter(self, event: tk.Event) -> None:
        self.hide()
        self.pending=self.widget.after(450,self._show)

    def _show(self) -> None:
        self.pending=None
        c=self.colors()
        self.window=tk.Toplevel(self.widget)
        self.window.overrideredirect(True)
        self.window.attributes("-topmost",True)
        tk.Label(self.window,text=self.text,bg=c["card"],fg=c["text"],padx=10,pady=6).pack()
        self.window.geometry(f"+{self.widget.winfo_rootx()}+{self.widget.winfo_rooty()+self.widget.winfo_height()+6}")
        # Small transient surfaces stay solid: ClearType matters more than blur.
        install_window_effects(self.window,c["surface"],RADIUS["tooltip"],material="solid")

    def hide(self, event: tk.Event | None = None) -> None:
        if self.pending:
            self.widget.after_cancel(self.pending)
            self.pending=None
        if self.window:
            self.window.destroy()
            self.window=None


class ScrollableBody(tk.Frame):
    """Only the body scrolls; window actions are packed separately.

    The scrollbar always occupies a fixed grid column. Toggling it with
    pack/pack_forget used to change the canvas width, which re-wrapped every
    wraplength label and fed the new request height back into the show/hide
    decision -- a geometry loop that could compress the content to nothing.
    A disabled ttk state keeps the trough on screen without moving geometry.
    """
    def __init__(self,master:tk.Misc,colors:Mapping[str,str]):
        super().__init__(master,bg=colors["surface"])
        self.canvas=tk.Canvas(self,bg=colors["surface"],highlightthickness=0,height=1)
        self.bar=ttk.Scrollbar(self,orient="vertical",command=self.canvas.yview,
                               style="Vertical.TScrollbar")
        self.grid_columnconfigure(0,weight=1)
        self.grid_rowconfigure(0,weight=1)
        self.canvas.grid(row=0,column=0,sticky="nsew")
        self.bar.grid(row=0,column=1,sticky="ns")
        self.bar.state(["disabled"])
        self.content=ttk.Frame(self.canvas,padding=round(16*dpi_scale(master)))
        self.item=self.canvas.create_window(0,0,window=self.content,anchor="nw")
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.canvas.bind("<Configure>",self._resize)
        self.content.bind("<Configure>",self._content_changed)
        self._wrap_pending=None
        # Focus reveal is for later keyboard navigation; the initial focus
        # must not scroll a half-laid-out canvas to its bottom.
        self._ready=False
        self._armed=False
        self._fitted_height=0
        self._bound=set()
        self.content.bind("<Configure>",self._wrap_labels,add=True)
        self.bind("<Destroy>",self._cancel_wrap,add=True)

    def _cancel_wrap(self,event:tk.Event) -> None:
        if event.widget==self and self._wrap_pending is not None:
            self.after_cancel(self._wrap_pending)
            self._wrap_pending=None

    def _wrap_labels(self,event:tk.Event) -> None:
        if self._wrap_pending is None:
            self._wrap_pending=self.after_idle(self._apply_wrap)

    def _apply_wrap(self) -> None:
        self._wrap_pending=None
        # _wrap_labels already coalesces Configure bursts to one idle walk.
        # Do not dedup solely on canvas width: a panel master can be <100px on
        # the first walk and grow later while the canvas width stays fixed, and
        # provider rebuilds introduce new labels at that same canvas width.
        def visit(parent:tk.Misc) -> None:
            for child in parent.winfo_children():
                if isinstance(child,(tk.Label,ttk.Label)) and float(child.cget("wraplength") or 0)>0:
                    if child.master.winfo_width()<100:
                        continue
                    inset=64 if child.master==self.content else 24
                    available=max(40,child.master.winfo_width()-round(inset*dpi_scale(self)))
                    # Only "ew" labels track their grid cell; cap their wrap to
                    # the real allocation so a label sharing a row with another
                    # column (the catalogue status beside the refresh button)
                    # cannot wrap wider than its cell. A sticky="w" label is
                    # only as wide as its own reqwidth, so capping it would lock
                    # the wrap at its shrunken transient width and never recover.
                    grid_info=child.grid_info()
                    allocated=child.winfo_width()
                    sticky=str(grid_info.get("sticky",""))
                    if allocated>1 and "e" in sticky and "w" in sticky:
                        available=min(available,allocated)
                    if int(float(child.cget("wraplength")))!=available:
                        child.configure(wraplength=available)
                visit(child)
        visit(self.content)

    def _resize(self,event:tk.Event) -> None:
        self.canvas.itemconfigure(self.item,width=event.width)
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self._update_scrollbar()
        if self._ready and event.height>=self._fitted_height:
            self._armed=True

    def _update_scrollbar(self) -> None:
        overflow=self.content.winfo_reqheight()>self.canvas.winfo_height()
        self.bar.state(["!disabled"] if overflow else ["disabled"])
        if not overflow:
            self.canvas.yview_moveto(0)

    def _content_changed(self,event:tk.Event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self._update_scrollbar()
        def visit(widget:tk.Misc) -> None:
            if str(widget) not in self._bound:
                widget.bind("<MouseWheel>",self._wheel,add=True)
                widget.bind("<FocusIn>",self._reveal_focus,add=True)
                widget.bind("<Configure>",self._wrap_labels,add=True)
                self._bound.add(str(widget))
            for child in widget.winfo_children():
                visit(child)
        visit(self)

    def _reveal_focus(self,event:tk.Event) -> None:
        if not self._armed:
            return
        # Tk also sends FocusIn to ancestors when a descendant gains focus.
        # Revealing one of those tall containers can jump to the content bottom
        # after the actual entry was already visible (notably at high DPI).
        if event.widget != self.focus_get():
            return
        if self.content.winfo_height()<=self.canvas.winfo_height():
            return
        # Stale geometry mid-layout can yield negative or >1 fractions, which
        # Tk would clamp to the very bottom of the content; only scroll for
        # genuinely mapped, measured widgets.
        if not event.widget.winfo_ismapped() or min(
                event.widget.winfo_width(),event.widget.winfo_height())<=1:
            return
        y=event.widget.winfo_rooty()-self.canvas.winfo_rooty()
        bottom=y+event.widget.winfo_height()
        visible=self.canvas.winfo_height()
        start=self.canvas.canvasy(0)
        content_height=self.content.winfo_height()
        if y<0:
            fraction=max(0,(start+y-12)/content_height)
        elif bottom>visible:
            fraction=(start+bottom-visible+12)/content_height
        else:
            return
        if 0<=fraction<=1:
            self.canvas.yview_moveto(fraction)

    def _wheel(self,event:tk.Event) -> str | None:
        if self.content.winfo_reqheight()>self.canvas.winfo_height():
            self.canvas.yview_scroll(-(int(event.delta/120) or (1 if event.delta>0 else -1)),"units")
            return "break"
        return None

    def refresh_scale(self) -> None:
        self.content.configure(padding=round(16*dpi_scale(self)))
        self._wrap_pending = None
        self._apply_wrap()

    def fit(self,max_height:int) -> int:
        self.update_idletasks()
        desired=self.content.winfo_reqheight()
        height=min(desired,max_height)
        self._update_scrollbar()
        self.canvas.configure(height=max(1,height))
        self._fitted_height=max(1,height)
        # Initial layout must start at the top; focus-reveal scrolling measured
        # against the pre-layout canvas otherwise leaves the dialog opened
        # halfway down its own content.
        self.canvas.yview_moveto(0)
        self._ready=True
        if self.canvas.winfo_height()>=self._fitted_height:
            self._armed=True
        return height


def set_rounded_region(window: tk.Toplevel, radius_dip: int) -> None:
    """Windows 10 fallback clip. ``radius_dip`` is logical pixels.

    On Windows 11 the DWM owns anti-aliased corners (attr 33), so a GDI region
    -- which is a 1-bit mask with no antialiasing -- must never override them.
    """
    if is_windows_11():
        return
    try:
        width = window.winfo_width()
        height = window.winfo_height()
        if width <= 1 or height <= 1:
            return
        radius = max(1, round(radius_dip * dpi_scale(window)))
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
            token = defer_tcl_events()
            try:
                result = user32.SetWindowRgn(
                    ctypes.c_void_p(native_handle(window)), region, True
                )
            finally:
                restore_tcl_events(token)
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


#: Tk marks override-redirect surfaces with this bit so the shell keeps them
#: out of the taskbar; see ensure_tool_window_style for why it must be re-asserted.
WS_EX_TOOLWINDOW = 0x00000080
#: SWP_NOMOVE|NOSIZE|NOZORDER|NOACTIVATE|FRAMECHANGED: re-evaluate ex styles
#: without moving, resizing, reordering, or stealing activation.
_TOOLWINDOW_SWP_FLAGS = 0x0037


class _Accent(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in ("state", "flags", "colour", "animation")]


class _CompositionData(ctypes.Structure):
    _fields_ = [("attribute", ctypes.c_int), ("data", ctypes.c_void_p), ("size", ctypes.c_size_t)]


class _Margins(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int) for name in ("left", "right", "top", "bottom")]


@dataclass(frozen=True)
class WindowEffects:
    rounded: bool = False
    glass: bool = False
    shadow: bool = False
    material: str = "solid"
    layered: bool = False


def native_handle(window: tk.Misc) -> int:
    """Tk exposes the client HWND; DWM needs its outer top-level wrapper."""
    user32 = ctypes.windll.user32
    user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    user32.GetAncestor.restype = ctypes.c_void_p
    return user32.GetAncestor(window.winfo_id(), 2) or window.winfo_id()


def transparency_enabled() -> bool:
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return bool(winreg.QueryValueEx(key, "EnableTransparency")[0])
    except OSError:
        return False


def resolve_material(requested: str) -> str:
    """Acrylic needs Windows 11 plus the system transparency switch; else solid."""
    if requested == "acrylic" and is_windows_11() and transparency_enabled():
        return "acrylic"
    return "solid"


def _dwm_attribute(dwm: Any, hwnd: ctypes.c_void_p, attribute: int, value: Any) -> None:
    try:
        dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value))
    except (AttributeError, OSError):
        pass


def _extend_frame(dwm: Any, hwnd: ctypes.c_void_p) -> None:
    try:
        dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(_Margins(0, 0, 0, 0)))
    except (AttributeError, OSError):
        pass


def _reset_composition(user32: Any, hwnd: ctypes.c_void_p) -> None:
    """Remove any leftover acrylic accent when switching back to solid."""
    try:
        compose = user32.SetWindowCompositionAttribute
        compose.argtypes = [ctypes.c_void_p, ctypes.POINTER(_CompositionData)]
        compose.restype = ctypes.c_int
        accent = _Accent(0, 0, 0, 0)
        data = _CompositionData(19, ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p),
                                 ctypes.sizeof(accent))
        compose(hwnd, ctypes.byref(data))
    except (AttributeError, OSError):
        pass


def _clear_layered(user32: Any, hwnd: ctypes.c_void_p) -> None:
    """Drop WS_EX_LAYERED so GDI restores ClearType text rendering."""
    try:
        user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        style = user32.GetWindowLongPtrW(hwnd, -20)  # GWL_EXSTYLE
        if style & 0x00080000:  # WS_EX_LAYERED
            user32.SetWindowLongPtrW(hwnd, -20, style & ~0x00080000)
            # SWP_NOMOVE|NOSIZE|NOZORDER|NOACTIVATE|FRAMECHANGED
            user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x27)
    except (AttributeError, OSError):
        pass


def ensure_tool_window_style(window: tk.Toplevel) -> None:
    """Keep an override-redirect surface off the Windows taskbar.

    Tk adds WS_EX_TOOLWINDOW when it maps an ``overrideredirect`` window, but
    its ``wm attributes -transparentcolor`` handler (exercised by every
    solid/acrylic material apply below) rewrites GWL_EXSTYLE from a cached
    value that predates that Map-time bit, silently dropping it. The card and
    hover popups then become ordinary unowned top-level windows: they show a
    taskbar button whose close command destroys the card out from under the
    app. Re-assert the bit after each material apply; the Map-bound re-apply
    also covers Tk swapping the wrapper HWND on first map.
    """
    try:
        user32 = ctypes.windll.user32
        user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        hwnd = ctypes.c_void_p(native_handle(window))
        # SetWindowLongPtrW/SetWindowPos can synchronously dispatch messages
        # (including WM_GETOBJECT into UIAutomation); never let that pump Tcl
        # events while a Python command has tcl_tstate NULL.
        token = defer_tcl_events()
        try:
            style = user32.GetWindowLongPtrW(hwnd, -20)  # GWL_EXSTYLE
            if not style & WS_EX_TOOLWINDOW:
                user32.SetWindowLongPtrW(hwnd, -20, style | WS_EX_TOOLWINDOW)
                user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, _TOOLWINDOW_SWP_FLAGS)
        finally:
            restore_tcl_events(token)
    except (AttributeError, OSError, tk.TclError):
        pass


def _redraw_frame(user32: Any, hwnd: ctypes.c_void_p) -> None:
    try:
        user32.RedrawWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_uint, ctypes.c_void_p]
        # RDW_INVALIDATE | RDW_ERASE | RDW_FRAME | RDW_ALLCHILDREN
        user32.RedrawWindow(hwnd, None, None, 0x485, None)
    except (AttributeError, OSError):
        pass


def _clear_window_region(user32: Any, hwnd: ctypes.c_void_p) -> None:
    """Remove stale GDI regions so the DWM corner preference is authoritative."""
    try:
        user32.SetWindowRgn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
        user32.SetWindowRgn.restype = ctypes.c_int
        user32.SetWindowRgn(hwnd, None, True)
    except (AttributeError, OSError):
        pass


def _make_solid(window: tk.Toplevel, hwnd: ctypes.c_void_p,
                user32: Any, dwm: Any) -> None:
    try:
        window.attributes("-transparentcolor", "")
    except tk.TclError:
        pass
    _clear_layered(user32, hwnd)
    _reset_composition(user32, hwnd)
    _extend_frame(dwm, hwnd)
    _redraw_frame(user32, hwnd)


def _apply_acrylic(window: tk.Toplevel, hwnd: ctypes.c_void_p,
                   user32: Any, dwm: Any, surface: str) -> bool:
    try:
        # Probe colour-key support before touching composition.
        window.attributes("-transparentcolor", "")
    except tk.TclError:
        return False
    try:
        red, green, blue = bytes.fromhex(surface[1:])
        # Tint in AABBGGRR; the high alpha keeps it close to the theme surface.
        tint = (ACRYLIC_TINT_ALPHA << 24) | (blue << 16) | (green << 8) | red
        accent = _Accent(4, 0, tint, 0)  # ACCENT_ENABLE_ACRYLICBLURBEHIND
        data = _CompositionData(19, ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p),
                                ctypes.sizeof(accent))
        compose = user32.SetWindowCompositionAttribute
        compose.argtypes = [ctypes.c_void_p, ctypes.POINTER(_CompositionData)]
        compose.restype = ctypes.c_int
        if not compose(hwnd, ctypes.byref(data)):
            return False
        try:
            window.attributes("-transparentcolor", surface)
        except tk.TclError:
            accent.state = 0
            compose(hwnd, ctypes.byref(data))
            return False
        _extend_frame(dwm, hwnd)
        return True
    except (AttributeError, OSError, tk.TclError):
        return False


def apply_window_effects(window: tk.Toplevel, surface: str,
                         *, material: str | None = None) -> WindowEffects:
    """Apply the window material.

    Solid windows stay opaque so GDI keeps ClearType text; acrylic is an opt-in
    colour-keyed layered window on which Windows forces grayscale text. Every
    native failure fails closed to the ordinary solid Tk background.
    """
    window._material_surface = surface
    requested = material if material is not None else getattr(window, "_material", "solid")
    try:
        hwnd = ctypes.c_void_p(native_handle(window))
        user32 = ctypes.windll.user32
        dwm = ctypes.windll.dwmapi
    except (AttributeError, OSError, tk.TclError):
        window._window_effects = WindowEffects()
        return window._window_effects
    # Every native call below (composition, DWM, style/FRAMECHANGED) may pump
    # window messages synchronously; defer Tcl event servicing for the whole
    # apply so a stuck/active TCL_SERVICE_ALL cannot re-enter a Python command.
    token = defer_tcl_events()
    try:
        dark = ctypes.c_uint32(int(sum(bytes.fromhex(surface[1:])) < 384))
        _dwm_attribute(dwm, hwnd, 20, dark)  # DWMWA_USE_IMMERSIVE_DARK_MODE
        rounded = False
        if is_windows_11():
            rounded = enable_rounded_corners(window)
            _clear_window_region(user32, hwnd)
        if resolve_material(requested) == "acrylic" and _apply_acrylic(window, hwnd, user32, dwm, surface):
            window._material = "acrylic"
            effects = WindowEffects(rounded, glass=True, shadow=False,
                                    material="acrylic", layered=True)
        else:
            _make_solid(window, hwnd, user32, dwm)
            window._material = "solid"
            effects = WindowEffects(rounded, glass=False, shadow=True,
                                    material="solid", layered=False)
        # The colour-key path above strips Tk's Map-time WS_EX_TOOLWINDOW, which
        # would put the frameless card/hover surfaces on the taskbar. Framed
        # windows keep their ordinary taskbar button on purpose. Tk hands back a
        # real boolean here (not the "0"/"1" string older docs imply), so coerce
        # through Tcl's own boolean rules rather than comparing string forms.
        if window.tk.getboolean(window.overrideredirect() or 0):
            ensure_tool_window_style(window)
        # Record on every call (not just install time) so material/theme/DPI
        # switches leave an authoritative state on the window. The signature
        # lets the Map hook avoid erasing and recompositing an unchanged frame.
        window._window_effects = effects
        window._window_effects_key = (hwnd.value, surface, effects.material)
        return effects
    finally:
        restore_tcl_events(token)


def install_window_effects(window: tk.Toplevel, surface: str,
                           radius: int | None = None, *, material: str = "solid") -> None:
    """Apply material while the window is still hidden, then re-check on Map.

    Applying immediately (the HWND exists after Toplevel construction) avoids
    the first-frame solid flash that a deferred timer produced.
    """
    window._material = material
    pending: str | None = None
    mapped_key: tuple[int, str, str] | None = None

    def apply() -> None:
        nonlocal pending, mapped_key
        pending = None
        if not window.winfo_exists():
            return
        current_surface = getattr(window, "_material_surface", surface)
        requested = getattr(window, "_material", material)
        effective = resolve_material(requested)
        key = (native_handle(window), current_surface, effective)
        effects = getattr(window, "_window_effects", WindowEffects())
        is_mapped = bool(window.winfo_viewable())
        # The first Map is a required native stabilization point (Tk may alter
        # frame styles even when it kept the same wrapper HWND). Only retained
        # windows that already completed a Map may skip an unchanged re-show.
        if (not is_mapped or mapped_key != key
                or getattr(window, "_window_effects_key", None) != key):
            effects = apply_window_effects(
                window, current_surface, material=requested,
            )
        if is_mapped:
            mapped_key = key
        # Win10 GDI fallback only; on Win11 the DWM owns corners. This still
        # runs after an unchanged-material skip because the size may differ.
        if radius is not None and not effects.rounded:
            set_rounded_region(window, radius)

    def mapped(event: tk.Event) -> None:
        nonlocal pending
        if event.widget == window and pending is None:
            pending = window.after_idle(apply)

    def destroyed(event: tk.Event) -> None:
        nonlocal pending
        if event.widget == window and pending is not None:
            window.after_cancel(pending)
            pending = None

    try:
        # Map/material setup may synchronously enter Tk's WndProc. Do not let
        # an unrelated queued Python idle callback run on this native stack.
        with tcl_events_deferred():
            window.update_idletasks()
            apply()
    except tk.TclError:
        pass
    window.bind("<Map>", mapped, add=True)
    window.bind("<Destroy>", destroyed, add=True)


class _SuggestedRect(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def install_dpi_handler(window: tk.Toplevel, callback: Callable[[int], None]) -> None:
    """Give an override-redirect window its own WM_DPICHANGED WNDPROC.

    WindowChrome covers the framed Toplevels; desktop cards and hover popups
    have no chrome and need this lightweight subclass instead. Tk re-entry is
    deferred with ``after_idle`` and the previous proc is restored on destroy.

    The outer wrapper HWND can differ between the withdrawn construction and
    the first Map (Tk reparents into a new top-level wrapper), so the subclass
    is re-applied from ``<Map>`` whenever the live HWND is not ours yet.
    """
    if getattr(window, "_dpi_handler", None) is not None:
        return
    try:
        user32 = ctypes.windll.user32
        user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.CallWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                                           ctypes.c_void_p, ctypes.c_void_p]
        user32.CallWindowProcW.restype = LRESULT
        user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        # One previous proc per subclassed HWND; the construction HWND may be
        # abandoned when Tk wraps the mapped toplevel.
        old_procs: dict[int, int] = {}

        def proc(hwnd: int, message: int, wparam: int, lparam: int) -> int:
            # A WNDPROC runs on top of whatever Tcl phase dispatched the
            # message; CallWindowProcW reaches Tk's own WndProc, whose
            # Tcl_ServiceAll() must stay a no-op while Python is on the stack.
            token = defer_tcl_events()
            try:
                previous = old_procs.get(hwnd, 0)
                if message == 0x02E0:  # WM_DPICHANGED
                    suggested = ctypes.cast(lparam, ctypes.POINTER(_SuggestedRect)).contents
                    user32.SetWindowPos(
                        hwnd, None, suggested.left, suggested.top,
                        suggested.right-suggested.left, suggested.bottom-suggested.top,
                        0x0014,  # SWP_NOZORDER | SWP_NOACTIVATE
                    )
                    window.after_idle(lambda: callback(int(wparam) & 0xffff))
                    return 0
                if message == 0x82:  # WM_NCDESTROY
                    old_procs.pop(hwnd, None)
                    if previous:
                        user32.SetWindowLongPtrW(hwnd, -4, previous)
                return int(user32.CallWindowProcW(previous, hwnd, message, wparam, lparam))
            finally:
                restore_tcl_events(token)

        reference = WNDPROC(proc)
        proc_address = ctypes.cast(reference, ctypes.c_void_p).value

        def install(event: tk.Event | None = None) -> None:
            hwnd = native_handle(window)
            if user32.GetWindowLongPtrW(hwnd, -4) == proc_address:
                return
            old_procs[hwnd] = user32.SetWindowLongPtrW(hwnd, -4, proc_address)

        install()
        window.bind("<Map>", install, add=True)
        # Retain the callback for the HWND lifetime.
        window._dpi_handler = {"proc": reference, "old_procs": old_procs}
    except (AttributeError, OSError, tk.TclError):
        pass
