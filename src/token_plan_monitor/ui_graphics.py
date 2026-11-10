from __future__ import annotations

import ctypes
import tkinter as tk
try:
    import winreg
except ModuleNotFoundError:
    winreg = None
from dataclasses import dataclass
from functools import lru_cache
from tkinter import ttk
from typing import Any, Callable, Mapping

from PIL import Image, ImageDraw, ImageFilter, ImageTk
from .appearance import LIGHT, Theme


def dpi_scale(widget: tk.Misc) -> float:
    return max(0.75, float(widget.tk.call("tk", "scaling")) / (96 / 72))


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
    def __init__(self, master: tk.Misc, *, radius: int = 14, fill: str,
                 outline: str, background: str, padding: int | tuple[int,int] = (16,14)):
        super().__init__(master, background=background, borderwidth=0, highlightthickness=0)
        self.radius, self.fill, self.outline = round(radius*dpi_scale(master)), fill, None
        self._image = None
        self._image_size = (0,0)
        self._background = tk.Label(self, background=background, borderwidth=0, highlightthickness=0)
        self._background.place(x=0,y=0,relwidth=1,relheight=1)
        self.content = tk.Frame(self,background=fill,borderwidth=0,highlightthickness=0)
        inset = max(round(14*dpi_scale(master)),self.radius//2)
        self.content.pack(fill="both",expand=True,padx=inset,pady=inset,
                          ipadx=round((padding[0] if isinstance(padding,tuple) else padding)*dpi_scale(master)),
                          ipady=round((padding[1] if isinstance(padding,tuple) else padding)*dpi_scale(master)))
        self.bind("<Configure>", self._redraw)

    def _redraw(self, event: tk.Event | None = None) -> None:
        size = self.winfo_width(), self.winfo_height()
        if size == self._image_size or min(size)<=1:
            return
        self._image_size = size
        self._image = rounded_photo(*size,radius=self.radius,fill=self.fill,shadow=True,
                                    background=str(self.cget("background")))
        self._background.configure(image=self._image)

    def set_theme(self, colors: Mapping[str,str]) -> None:
        self.fill,self.outline = colors["card"],None
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
                images.append(rounded_photo(px(88),px(38),radius=px(9),fill=top,end_fill=bottom,
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
        images=[rounded_photo(px(80),px(36),radius=px(8),fill=colors["hover"],outline=None if key=="border" else colors[key],
                              background=colors["card"]) for key in ("border","focus","error")]
        style.element_create(entry,"image",images[0],("invalid",images[2]),("focus",images[1]),border=px(10),padding=0,sticky="nsew")
        cache[entry]=images
    style.layout("TEntry",[(entry,{"sticky":"nsew","children":[
        ("Entry.padding",{"sticky":"nsew","children":[("Entry.textarea",{"sticky":"nsew"})]})]})])
    segment=f"Mac{tag}.segment"
    if segment not in style.element_names():
        images=[rounded_photo(px(90),px(34),radius=px(8),fill=colors[fill],outline=colors[border] if border=="focus" else None,
                              background=colors["card"])
                for fill,border in (("card","border"),("info_surface","accent"),("hover","border"),("card","focus"))]
        style.element_create(segment,"image",images[0],("focus",images[3]),
                             ("selected",images[1]),("active",images[2]),border=px(10),padding=0,sticky="nsew")
        cache[segment]=images
    style.layout("Theme.TRadiobutton",[(segment,{"sticky":"nsew","children":[
        ("Radiobutton.padding",{"sticky":"nsew","children":[("Radiobutton.label",{"sticky":"nsew"})]})]})])
    style.master._mac_button_images=cache


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
        install_window_effects(self.window,c["surface"],8)

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
        self.bar=ttk.Scrollbar(self,orient="vertical",command=self.canvas.yview)
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


def set_rounded_region(window: tk.Toplevel, radius: int) -> None:
    """Clip a borderless window to a native rounded region without color-key halos."""
    try:
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
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return bool(winreg.QueryValueEx(key, "EnableTransparency")[0])
    except OSError:
        return False


def apply_window_effects(window: tk.Toplevel, surface: str) -> WindowEffects:
    """Keep foreground opaque, exposing acrylic only through the surface colour.

    Failed/disabled composition leaves the ordinary solid Tk background intact.
    No global alpha or extra shadow window is used.
    """
    window._material_surface = surface
    rounded = enable_rounded_corners(window)
    glass = shadow = False
    try:
        window.attributes("-transparentcolor", "")
    except tk.TclError:
        return WindowEffects(rounded)
    try:
        hwnd = ctypes.c_void_p(native_handle(window))
        dwm = ctypes.windll.dwmapi
        dark = ctypes.c_uint32(int(sum(bytes.fromhex(surface[1:])) < 384))
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark), ctypes.sizeof(dark))
        border = ctypes.c_uint32(0xFFFFFFFE)  # DWMWA_COLOR_NONE (Windows 11)
        dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(border), ctypes.sizeof(border))
        # Suppress compositor and class drop shadows; never create a shadow HWND.
        policy = ctypes.c_int(1)  # DWMNCRP_DISABLED
        dwm.DwmSetWindowAttribute(hwnd, 2, ctypes.byref(policy), ctypes.sizeof(policy))
        class Margins(ctypes.Structure):
            _fields_ = [(name, ctypes.c_int) for name in ("left", "right", "top", "bottom")]
        dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(Margins(0, 0, 0, 0)))
        user32 = ctypes.windll.user32
        get_style, set_style = user32.GetClassLongPtrW, user32.SetClassLongPtrW
        get_style.argtypes = [ctypes.c_void_p, ctypes.c_int]
        get_style.restype = ctypes.c_size_t
        set_style.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t]
        set_style.restype = ctypes.c_size_t
        set_style(hwnd, -26, get_style(hwnd, -26) & ~0x00020000)
        if not transparency_enabled():
            return WindowEffects(rounded, False, shadow)

        class Accent(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint32) for name in ("state", "flags", "colour", "animation")]
        class CompositionData(ctypes.Structure):
            _fields_ = [("attribute", ctypes.c_int), ("data", ctypes.c_void_p), ("size", ctypes.c_size_t)]
        # Acrylic tint in AABBGGRR; keep enough translucency to reveal the desktop.
        red, green, blue = bytes.fromhex(surface[1:])
        tint = (0xC8 << 24) | (blue << 16) | (green << 8) | red
        accent = Accent(4, 0, tint, 0)
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
    return WindowEffects(rounded, glass, shadow)


def install_window_effects(window: tk.Toplevel, surface: str, radius: int | None = None) -> None:
    """Reapply after Tk has finished mapping/rebuilding the outer HWND."""
    pending: str | None = None

    def apply() -> None:
        nonlocal pending
        pending = None
        if window.winfo_viewable():
            window._window_effects = apply_window_effects(window, getattr(window,"_material_surface",surface))
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
