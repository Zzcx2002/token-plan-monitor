from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import logging
import tkinter as tk
import tkinter.font as tkfont
from dataclasses import replace
from typing import Any, Callable, Mapping

from .models import CacheError, MonitorViewState, WidgetPreferences
from .presentation import (
    PLAN_LABELS,
    compact_remaining,
    hover_summary,
    menu_lines,
    state_tone,
)
from .storage import Storage
from .window_geometry import WindowGeometry
from .appearance import installed_fonts
from .ui_graphics import RoundedPanel, set_rounded_region, apply_window_effects, install_window_effects, rounded_photo, dpi_scale, gradient_image

logger = logging.getLogger(__name__)


class RECT(ctypes.Structure):
    _fields_ = (
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    )


class MONITORINFO(ctypes.Structure):
    _fields_ = (
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wintypes.DWORD),
    )


_user32 = ctypes.windll.user32
_user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
_user32.MonitorFromPoint.restype = wintypes.HANDLE
_user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
_user32.GetMonitorInfoW.restype = wintypes.BOOL
_user32.SystemParametersInfoW.argtypes = [
    wintypes.UINT,
    wintypes.UINT,
    ctypes.c_void_p,
    wintypes.DWORD,
]
_user32.SystemParametersInfoW.restype = wintypes.BOOL


class QuotaCard(tk.Canvas):
    """Draw labels on the gradient itself, without opaque label rectangles."""
    def __init__(self, master: tk.Misc, colors: Mapping[str,str], font_family: str):
        self.scale=dpi_scale(master)
        self.colors,self.font_family=colors,font_family
        self.title_font=tkfont.Font(family=font_family,size=9,weight="bold")
        self.label_font=tkfont.Font(family=font_family,size=9)
        self.value_font=tkfont.Font(family=installed_fonts(tkfont.families(master))[1],size=18,weight="bold")
        width=max(round(264*self.scale),self.value_font.measure("100.0%")*2+round(54*self.scale))
        super().__init__(master,width=width,height=round(124*self.scale),bg=colors["surface"],
                         highlightthickness=0,borderwidth=0)
        self.state=MonitorViewState(status="unconfigured")
        self.phase=0
        self._surface_key=None
        self._surface=None
        self._progress_images=[]
        self.bind("<Configure>",lambda event:self.draw())

    def render(self,state:MonitorViewState) -> None:
        self.state=state
        self.draw()

    def draw(self) -> None:
        from PIL import Image, ImageDraw, ImageTk
        c,s=self.colors,self.scale
        width,height=self.winfo_width(),self.winfo_height()
        if min(width,height)<=1:
            return
        self.configure(background=c["surface"])
        key=width,height,c["card_top"],c["card_bottom"]
        if key!=self._surface_key:
            self._surface=rounded_photo(width,height,radius=round(20*s),fill=c["card_top"],
                                        end_fill=c["card_bottom"])
            self._surface_key=key
        self.delete("all")
        self.create_image(0,0,image=self._surface,anchor="nw")
        extra=max(0,height-124*s)
        state=self.state
        snapshot=state.snapshot
        # Look the plan type up instead of a two-way ternary, so a third plan
        # type (Bailian Token Plan) is not mislabelled as "Coding Plan".
        title=PLAN_LABELS.get(snapshot.plan_type,snapshot.plan_type) if snapshot else state.provider_label
        self.create_text(18*s,20*s,text=title,font=self.title_font,fill=c["text"],anchor="w")
        tone=state_tone(state)
        colour=c[{"ok":"success","warning":"warning","error":"error","unconfigured":"muted"}[tone]]
        dot_x=width-21*s
        if state.is_refreshing:
            self.create_arc(dot_x-4*s,16*s,dot_x+4*s,24*s,start=self.phase,extent=270,
                            style="arc",outline=c["accent"],width=max(1,round(2*s)))
        else:
            self.create_oval(dot_x-3*s,17*s,dot_x+3*s,23*s,fill=colour,outline="")
            if tone!="ok":
                message={"warning":"数据过期" if state.status=="stale" else "剩余偏低",
                         "error":"连接异常","unconfigured":"待配置"}[tone]
                self.create_text(dot_x-10*s,20*s,text=message,font=self.label_font,fill=colour,anchor="e")
        windows={w.kind:w for w in state.snapshot.windows} if state.snapshot else {}
        self._progress_images=[]
        column_width=(width-48*s)/2
        for index,(kind,label) in enumerate((("five_hour","5 小时剩余"),("weekly","每周剩余"))):
            x=18*s+index*(column_width+12*s)
            self.create_text(x,49*s+extra*.35,text=label,font=self.label_font,fill=c["muted"],anchor="w")
            window=windows.get(kind)
            warning=window is not None and window.remaining_percent<10
            self.create_text(x,76*s+extra*.65,text=compact_remaining(state,kind),font=self.value_font,
                             fill=c["warning"] if warning else c["text"],anchor="w")
            bar_width,bar_height=max(1,round(column_width)),max(3,round(4*s))
            bar=Image.new("RGBA",(bar_width*3,bar_height*3))
            mask=Image.new("L",bar.size)
            ImageDraw.Draw(mask).rounded_rectangle((0,0,bar.width-1,bar.height-1),radius=bar.height//2,fill=255)
            bar.paste(c["trough"],(0,0,bar.width,bar.height))
            if window:
                fill=round(bar.width*window.remaining_percent/100)
                if fill:
                    gradient=gradient_image(fill,bar.height,c["warning_start"] if warning else c["accent_start"],
                                            c["warning"] if warning else c["accent"],True)
                    bar.paste(gradient,(0,0))
            bar.putalpha(mask)
            image=ImageTk.PhotoImage(bar.resize((bar_width,bar_height),Image.Resampling.LANCZOS))
            self._progress_images.append(image)
            self.create_image(x,101*s+extra,image=image,anchor="nw")


class DesktopWidget:
    MIN_WIDTH = 280
    DRAG_THRESHOLD = 5

    def __init__(
        self,
        root: tk.Tk,
        storage: Storage,
        *,
        on_open: Callable[[], None],
        on_refresh: Callable[[], None],
        on_settings: Callable[[], None],
        on_exit: Callable[[], None],
        on_preferences_changed: Callable[[], None],
        colors: Mapping[str, str],
        font_family: str,
        shadow_opacity: float = 0.14,
    ):
        self.root = root
        self.storage = storage
        self.on_open = on_open
        self.on_refresh = on_refresh
        self.on_settings = on_settings
        self.on_exit = on_exit
        self.on_preferences_changed = on_preferences_changed
        self.colors = colors
        self.font_family = font_family
        self._padding = 0
        self.MIN_WIDTH = round(280*dpi_scale(root))
        self._spinner_after = None
        self._state = MonitorViewState(status="unconfigured")
        self._preferences = storage.load_widget_preferences()
        self._hover: tk.Toplevel | None = None
        self._hover_label: tk.Label | None = None
        self._hover_after: str | None = None
        self._press: tuple[int, int, int, int] | None = None
        self._dragged = False
        self._closed = False
        self._menu_topmost: tk.BooleanVar | None = None
        self._size = (self.MIN_WIDTH, 1)

        self.window = tk.Toplevel(root)
        self.window.withdraw()
        self.window.title("火山方舟桌面容量")
        self.window._shadow_opacity = shadow_opacity
        self.window.overrideredirect(True)
        self.window.resizable(False, False)
        self.window.configure(background=colors["surface"])
        self.window.attributes("-topmost", self._preferences.topmost)
        install_window_effects(self.window, self.colors["surface"], round(20*dpi_scale(self.window)))
        self.geometry=WindowGeometry(self.window,storage,"widget",(280,124))
        self._resize_press=None
        self._create_card()
        self.window.bind("<Configure>",self._on_size,add=True)
        self._bind_recursive(self.window)
        self.render(self._state)
        if self._preferences.visible:
            self.show()
        else:
            self.window.withdraw()

    @property
    def visible(self) -> bool:
        return self._preferences.visible

    @property
    def topmost(self) -> bool:
        return self._preferences.topmost

    def _create_card(self) -> None:
        self.card=QuotaCard(self.window,self.colors,self.font_family)
        self.card.pack(fill="both",expand=True,padx=self._padding,pady=self._padding)

    def set_theme(self, colors: Mapping[str,str], shadow_opacity: float) -> None:
        self.colors=colors
        self.window.configure(background=colors["surface"])
        self.window._shadow_opacity=shadow_opacity
        self.card.colors=colors
        self.card._surface_key=None
        self.card.draw()
        apply_window_effects(self.window,colors["surface"])
        if self._hover is not None:
            self._hover.destroy()
            self._hover=None
            self._hover_label=None

    def _bind_recursive(self, widget: tk.Misc) -> None:
        widget.bind("<Motion>",self._resize_cursor,add=True)
        widget.bind("<Enter>", self._on_enter, add=True)
        widget.bind("<Leave>", self._on_leave, add=True)
        widget.bind("<ButtonPress-1>", self._on_press, add=True)
        widget.bind("<B1-Motion>", self._on_drag, add=True)
        widget.bind("<ButtonRelease-1>", self._on_release, add=True)
        widget.bind("<Button-3>", self._show_menu, add=True)
        for child in widget.winfo_children():
            self._bind_recursive(child)

    def render(self, state: MonitorViewState) -> None:
        if self._closed:
            return
        self._state=state
        self.card.render(state)
        self._fit_to_content()
        if state.is_refreshing and self._spinner_after is None:
            self._spin()
        if self._hover is not None and self._hover.winfo_viewable():
            self._show_hover()

    def _spin(self) -> None:
        self._spinner_after=None
        if self._closed or not self._state.is_refreshing:
            return
        self.card.phase=(self.card.phase-30)%360
        self.card.draw()
        self._spinner_after=self.root.after(90,self._spin)

    def _fit_to_content(self) -> None:
        if self._closed or self.geometry.initialized:
            return
        self.window.update_idletasks()
        width=max(self.MIN_WIDTH,self.card.winfo_reqwidth())
        height=max(1,self.card.winfo_reqheight())
        self.geometry.fit((width,height))
        self._size=self.geometry.pixel_size

    def show(self) -> None:
        if self._closed:
            return
        self._fit_to_content()
        width, height = self._size
        if self._preferences.x is None or self._preferences.y is None:
            x, y = self._default_position()
        else:
            x, y = self._preferences.x, self._preferences.y
        x, y = self._clamp_position(x, y, width, height)
        self.window.geometry(f"{width}x{height}+{x}+{y}")
        self.window.deiconify()
        self.window.attributes("-topmost", self._preferences.topmost)
        apply_window_effects(self.window, self.colors["surface"])
        set_rounded_region(self.window, round(20*dpi_scale(self.window)))

    def set_visible(self, visible: bool) -> None:
        self._save_preferences(replace(self._preferences, visible=visible))
        if visible:
            self.show()
        else:
            self._cancel_hover()
            self.window.withdraw()

    def toggle_topmost(self) -> None:
        topmost = not self._preferences.topmost
        self._save_preferences(replace(self._preferences, topmost=topmost))
        self.window.attributes("-topmost", topmost)

    def _save_preferences(self, preferences: WidgetPreferences) -> None:
        self._preferences = preferences
        try:
            self.storage.save_widget_preferences(preferences)
        except CacheError as exc:
            logger.warning("Could not save desktop widget preferences: %s", exc.message)
        self.on_preferences_changed()

    def _on_enter(self, event: tk.Event) -> None:
        self._cancel_hover()
        if self.visible:
            self._hover_after = self.root.after(350, self._show_hover)

    def _on_leave(self, event: tk.Event) -> None:
        self.root.after(60, self._hide_hover_if_outside)

    def _hide_hover_if_outside(self) -> None:
        if self._closed:
            return
        containing = self.root.winfo_containing(
            self.root.winfo_pointerx(), self.root.winfo_pointery()
        )
        while containing is not None:
            if containing == self.window:
                return
            containing = getattr(containing, "master", None)
        self._cancel_hover()

    def _cancel_hover(self) -> None:
        if self._hover_after is not None:
            try:
                self.root.after_cancel(self._hover_after)
            except tk.TclError:
                pass
            self._hover_after = None
        if self._hover is not None and self._hover.winfo_exists():
            self._hover.withdraw()

    def _show_hover(self) -> None:
        self._hover_after = None
        if self._closed or not self.visible or not self.window.winfo_viewable():
            return
        if self._hover is None:
            hover = tk.Toplevel(self.root)
            hover.title("火山方舟容量摘要")
            hover.overrideredirect(True)
            hover.attributes("-topmost", True)
            hover.configure(background=self.colors["surface"])
            install_window_effects(hover, self.colors["surface"], round(16*dpi_scale(hover)))
            panel = RoundedPanel(
                hover,
                radius=13,
                fill=self.colors["card"],
                outline=self.colors["border"],
                background=self.colors["card"],
                padding=(10, 7),
            )
            panel.pack(padx=6, pady=6)
            self._hover_label = tk.Label(
                panel.content,
                justify="left",
                background=self.colors["card"],
                foreground=self.colors["text"],
                font=(self.font_family, 9),
            )
            self._hover_label.pack()
            self._hover = hover
        summary=hover_summary(self._state)
        if self._state.snapshot:
            summary+=f"\n每月剩余 {compact_remaining(self._state,'monthly')} · 更新于 {self._state.snapshot.queried_at.astimezone():%H:%M}"
        self._hover_label.config(text=summary)
        self._hover.update_idletasks()
        width = self._hover.winfo_reqwidth()
        height = self._hover.winfo_reqheight()
        x = self.window.winfo_x()
        y = self.window.winfo_y() - height - 8
        left, top, right, bottom = self._work_area(x, y, width, height)
        if y < top:
            y = self.window.winfo_y() + self.window.winfo_height() + 8
        x, y = self._clamp_position(x, y, width, height)
        self._hover.geometry(f"{width}x{height}+{x}+{y}")
        self._hover.deiconify()
        apply_window_effects(self._hover, self.colors["surface"])
        set_rounded_region(self._hover, round(16*dpi_scale(self._hover)))

    def _on_size(self,event:tk.Event) -> None:
        if event.widget==self.window and self._size!=(event.width,event.height):
            self._size=(event.width,event.height)
            set_rounded_region(self.window,round(20*dpi_scale(self.window)))

    def _edge(self,event:tk.Event) -> str:
        x,y=event.x_root-self.window.winfo_rootx(),event.y_root-self.window.winfo_rooty()
        edge=round(7*dpi_scale(self.window))
        return ("n" if y<edge else "s" if y>=self.window.winfo_height()-edge else "") + (
            "w" if x<edge else "e" if x>=self.window.winfo_width()-edge else "")

    def _resize_cursor(self,event:tk.Event) -> None:
        edge=self._edge(event)
        cursors={"n":"size_ns","s":"size_ns","e":"size_we","w":"size_we",
                 "ne":"size_ne_sw","sw":"size_ne_sw","nw":"size_nw_se","se":"size_nw_se"}
        event.widget.configure(cursor=cursors.get(edge,""))

    def _on_press(self, event: tk.Event) -> None:
        self._cancel_hover()
        edge=self._edge(event)
        if edge:
            self._resize_press=(edge,event.x_root,event.y_root,self.window.winfo_x(),
                                self.window.winfo_y(),self.window.winfo_width(),self.window.winfo_height())
            return
        self._press = (
            event.x_root,
            event.y_root,
            self.window.winfo_x(),
            self.window.winfo_y(),
        )
        self._dragged = False

    def _on_drag(self, event: tk.Event) -> None:
        if self._resize_press is not None:
            edge,sx,sy,x,y,w,h=self._resize_press
            dx,dy=event.x_root-sx,event.y_root-sy
            left,top,right,bottom=self._work_area(x,y,w,h)
            minw,minh=(round(n*dpi_scale(self.window)) for n in (280,124))
            nw=min(max(minw,w+dx if "e" in edge else w-dx if "w" in edge else w),right-left)
            nh=min(max(minh,h+dy if "s" in edge else h-dy if "n" in edge else h),bottom-top)
            nx=x+w-nw if "w" in edge else x
            ny=y+h-nh if "n" in edge else y
            nx,ny=self._clamp_position(nx,ny,nw,nh)
            self.window.geometry(f"{nw}x{nh}+{nx}+{ny}")
            return
        if self._press is None:
            return
        start_x, start_y, window_x, window_y = self._press
        dx = event.x_root - start_x
        dy = event.y_root - start_y
        if not self._dragged and max(abs(dx), abs(dy)) < self.DRAG_THRESHOLD:
            return
        self._dragged = True
        self.window.geometry(f"+{window_x + dx}+{window_y + dy}")

    def _on_release(self, event: tk.Event) -> None:
        if self._resize_press is not None:
            self._resize_press=None
            self.geometry.flush()
            self._save_preferences(replace(self._preferences,x=self.window.winfo_x(),y=self.window.winfo_y()))
            return
        if self._press is None:
            return
        dragged = self._dragged
        self._press = None
        self._dragged = False
        if dragged:
            x, y = self._clamp_position(
                self.window.winfo_x(),
                self.window.winfo_y(),
                self.window.winfo_width(),
                self.window.winfo_height(),
            )
            self.window.geometry(f"+{x}+{y}")
            self._save_preferences(replace(self._preferences, x=x, y=y))
        else:
            self.on_open()

    def _show_menu(self, event: tk.Event) -> None:
        self._cancel_hover()
        menu = tk.Menu(self.window, tearoff=False, font=(self.font_family, 9))
        for line in menu_lines(self._state):
            menu.add_command(label=line, state="disabled")
        menu.add_separator()
        menu.add_command(label="打开详细用量", command=self.on_open)
        refresh_state = (
            "disabled"
            if self._state.status == "unconfigured" or self._state.is_refreshing
            else "normal"
        )
        menu.add_command(label="立即刷新", command=self.on_refresh, state=refresh_state)
        self._menu_topmost = tk.BooleanVar(self.window, self.topmost)
        menu.add_checkbutton(
            label="置顶显示",
            variable=self._menu_topmost,
            command=self.toggle_topmost,
        )
        menu.add_command(label="隐藏桌面卡片", command=lambda: self.set_visible(False))
        menu.add_command(label="设置", command=self.on_settings)
        menu.add_separator()
        menu.add_command(label="退出", command=self.on_exit)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _default_position(self) -> tuple[int, int]:
        width, height = self._size
        rect = RECT()
        if _user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
            return rect.right - width - 24, rect.bottom - height - 24
        return (
            self.root.winfo_screenwidth() - width - 24,
            self.root.winfo_screenheight() - height - 80,
        )

    def _clamp_position(
        self, x: int, y: int, width: int, height: int
    ) -> tuple[int, int]:
        left, top, right, bottom = self._work_area(x, y, width, height)
        max_x = max(left, right - width)
        max_y = max(top, bottom - height)
        return min(max(x, left), max_x), min(max(y, top), max_y)

    def _work_area(self, x: int, y: int, width: int, height: int) -> tuple[int, int, int, int]:
        user32 = _user32
        point = wintypes.POINT(x + width // 2, y + height // 2)
        monitor = user32.MonitorFromPoint(point, 2)
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            rect = info.rcWork
            return rect.left, rect.top, rect.right, rect.bottom
        rect = RECT()
        if user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
            return rect.left, rect.top, rect.right, rect.bottom
        return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    def close(self) -> None:
        if self._closed:
            return
        self.geometry.flush()
        self._closed = True
        if self._spinner_after is not None:
            self.root.after_cancel(self._spinner_after)
            self._spinner_after = None
        self._cancel_hover()
        for window in (self._hover, self.window):
            if window is not None:
                try:
                    window.destroy()
                except tk.TclError:
                    pass
        self._hover = None
