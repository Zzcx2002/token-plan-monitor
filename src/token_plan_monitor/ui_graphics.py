from __future__ import annotations

import ctypes
import tkinter as tk
from ctypes import wintypes as wt
try:
    import winreg
except ModuleNotFoundError:
    winreg = None
from dataclasses import dataclass
from functools import lru_cache
from tkinter import ttk
from typing import Any, Callable, Mapping

# Keep the native callback type in this low-level module so window_chrome and
# the override-redirect DPI watcher can share it without circular imports.
LRESULT = ctypes.c_ssize_t
WNDPROC = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
    LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM
)

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
        self._image = rounded_photo(*size,radius=self.radius,fill=self.fill,outline=self.outline,
                                    shadow=True,background=str(self.cget("background")))
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
    """Only the body scrolls; window actions are packed separately."""
    def __init__(self,master:tk.Misc,colors:Mapping[str,str]):
        super().__init__(master,bg=colors["surface"])
        self.canvas=tk.Canvas(self,bg=colors["surface"],highlightthickness=0,height=1)
        self.bar=ttk.Scrollbar(self,orient="vertical",command=self.canvas.yview,
                               style="Vertical.TScrollbar")
        self.canvas.pack(side="left",fill="both",expand=True)
        self.content=ttk.Frame(self.canvas,padding=round(16*dpi_scale(master)))
        self.item=self.canvas.create_window(0,0,window=self.content,anchor="nw")
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.canvas.bind("<Configure>",self._resize)
        self.content.bind("<Configure>",self._content_changed)
        self._wrap_pending=None
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
        def visit(parent:tk.Misc) -> None:
            for child in parent.winfo_children():
                if isinstance(child,(tk.Label,ttk.Label)) and float(child.cget("wraplength") or 0)>0:
                    if child.master.winfo_width()<100:
                        continue
                    inset=64 if child.master==self.content else 24
                    available=max(40,child.master.winfo_width()-round(inset*dpi_scale(self)))
                    if int(float(child.cget("wraplength")))!=available:
                        child.configure(wraplength=available)
                visit(child)
        visit(self.content)

    def _resize(self,event:tk.Event) -> None:
        self.canvas.itemconfigure(self.item,width=event.width)
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self._update_scrollbar()

    def _update_scrollbar(self) -> None:
        if self.content.winfo_reqheight()>self.canvas.winfo_height():
            self.bar.pack(side="right",fill="y")
        else:
            self.bar.pack_forget()
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
        if self.content.winfo_height()<=self.canvas.winfo_height():
            return
        y=event.widget.winfo_rooty()-self.canvas.winfo_rooty()
        bottom=y+event.widget.winfo_height()
        visible=self.canvas.winfo_height()
        start=self.canvas.canvasy(0)
        if y<0:
            self.canvas.yview_moveto(max(0,(start+y-12)/self.content.winfo_height()))
        elif bottom>visible:
            self.canvas.yview_moveto((start+bottom-visible+12)/self.content.winfo_height())

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
        if desired>height:
            self.bar.pack(side="right",fill="y")
        else:
            self.bar.pack_forget()
            self.canvas.yview_moveto(0)
        self.canvas.configure(height=max(1,height))
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
    # Record on every call (not just install time) so material/theme/DPI
    # switches leave an authoritative state on the window.
    window._window_effects = effects
    return effects


def install_window_effects(window: tk.Toplevel, surface: str,
                           radius: int | None = None, *, material: str = "solid") -> None:
    """Apply material while the window is still hidden, then re-check on Map.

    Applying immediately (the HWND exists after Toplevel construction) avoids
    the first-frame solid flash that a deferred timer produced.
    """
    window._material = material
    pending: str | None = None

    def apply() -> None:
        nonlocal pending
        pending = None
        if not window.winfo_exists():
            return
        effects = apply_window_effects(
            window, getattr(window, "_material_surface", surface),
            material=getattr(window, "_material", material),
        )
        # Win10 GDI fallback only; on Win11 the DWM owns corners.
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
