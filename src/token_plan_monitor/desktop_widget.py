from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import logging
import tkinter as tk
from dataclasses import replace
from typing import Any, Callable, Mapping

from .models import CacheError, MonitorViewState, WidgetPreferences
from .presentation import (
    compact_remaining,
    hover_summary,
    menu_lines,
    state_tone,
    status_text,
)
from .storage import Storage
from .ui_graphics import RoundedPanel, RoundedProgress, set_rounded_region, apply_window_effects, install_window_effects

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


class DesktopWidget:
    MIN_WIDTH = 272
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
        self.window.overrideredirect(True)
        self.window.resizable(False, False)
        self.window.configure(background=colors["surface"])
        self.window.attributes("-topmost", self._preferences.topmost)
        install_window_effects(self.window, self.colors["surface"], 20)
        self._create_card()
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
        self.card = RoundedPanel(
            self.window,
            radius=20,
            fill=self.colors["card"],
            outline=self.colors["border"],
            background=self.colors["surface"],
            padding=(14, 11),
        )
        self.card.pack(fill="both", expand=True, padx=8, pady=8)
        card = self.card.content
        header = tk.Frame(card, background=self.colors["card"])
        header.pack(fill="x")
        self.status_dot = tk.Canvas(
            header,
            width=10,
            height=10,
            background=self.colors["card"],
            highlightthickness=0,
        )
        self.status_dot.pack(side="left", pady=(4, 0))
        self._dot = self.status_dot.create_oval(1, 1, 9, 9, outline="")
        self.plan_label = tk.Label(
            header,
            text="火山方舟",
            background=self.colors["card"],
            foreground=self.colors["text"],
            font=(self.font_family, 9, "bold"),
        )
        self.plan_label.pack(side="left", padx=(5, 0))
        self.status_label = tk.Label(
            header,
            text="待配置",
            background=self.colors["card"],
            foreground=self.colors["muted"],
            font=(self.font_family, 8),
        )
        self.status_label.pack(side="right")

        quotas = tk.Frame(card, background=self.colors["card"])
        quotas.pack(fill="x", pady=(9, 0))
        quotas.columnconfigure(0, weight=1, uniform="quota")
        quotas.columnconfigure(1, weight=1, uniform="quota")
        self.quota_labels: dict[str, tk.Label] = {}
        self.quota_progress: dict[str, RoundedProgress] = {}
        for column, (kind, title) in enumerate((("five_hour", "5 小时"), ("weekly", "每周"))):
            cell = tk.Frame(quotas, background=self.colors["card"])
            cell.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 10, 0))
            tk.Label(
                cell,
                text=title,
                background=self.colors["card"],
                foreground=self.colors["muted"],
                font=(self.font_family, 8),
            ).pack(anchor="w")
            value = tk.Label(
                cell,
                text="—",
                background=self.colors["card"],
                foreground=self.colors["text"],
                font=(self.font_family, 18, "bold"),
            )
            value.pack(anchor="w", pady=(1, 3))
            self.quota_labels[kind] = value
            progress = RoundedProgress(
                cell,
                background=self.colors["card"],
                trough=self.colors["trough"],
                height=4,
            )
            progress.pack(fill="x", pady=(0, 1))
            self.quota_progress[kind] = progress

    def _bind_recursive(self, widget: tk.Misc) -> None:
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
        self._state = state
        text = status_text(state)
        tone = state_tone(state)
        status_color = {
            "ok": self.colors["success"],
            "warning": self.colors["warning"],
            "error": self.colors["error"],
            "unconfigured": self.colors["muted"],
        }[tone]
        dot_color = self.colors["accent"] if state.is_refreshing else status_color
        self.status_label.config(text=text, foreground=status_color)
        self.plan_label.config(text=("Agent Plan" if state.snapshot.plan_type == "agent_plan" else "Coding Plan") if state.snapshot else "火山方舟")
        self.status_dot.itemconfigure(self._dot, fill=dot_color)
        windows = {window.kind: window for window in state.snapshot.windows} if state.snapshot else {}
        for kind, label in self.quota_labels.items():
            window = windows.get(kind)
            warning = window is not None and window.remaining_percent < 10
            color = self.colors["warning"] if warning else self.colors["text"]
            label.config(text=compact_remaining(state, kind), foreground=color)
            self.quota_progress[kind].set(
                window.remaining_percent if window is not None else 0,
                self.colors["warning"] if warning else self.colors["accent"],
            )
        self._fit_to_content()
        if self._hover is not None and self._hover.winfo_viewable():
            self._show_hover()

    def _fit_to_content(self) -> None:
        if self._closed:
            return
        self.window.update_idletasks()
        width = max(self.MIN_WIDTH, self.card.winfo_reqwidth() + 16)
        height = max(1, self.card.winfo_reqheight() + 16)
        if (width, height) == self._size:
            return
        self._size = (width, height)
        if self.window.winfo_viewable():
            x, y = self._clamp_position(
                self.window.winfo_x(), self.window.winfo_y(), width, height
            )
            self.window.geometry(f"{width}x{height}+{x}+{y}")
            apply_window_effects(self.window, self.colors["surface"])
            set_rounded_region(self.window, 20)
        else:
            self.window.geometry(f"{width}x{height}")

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
        set_rounded_region(self.window, 20)

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
            install_window_effects(hover, self.colors["surface"], 16)
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
        self._hover_label.config(text=hover_summary(self._state))
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
        set_rounded_region(self._hover, 16)

    def _on_press(self, event: tk.Event) -> None:
        self._cancel_hover()
        self._press = (
            event.x_root,
            event.y_root,
            self.window.winfo_x(),
            self.window.winfo_y(),
        )
        self._dragged = False

    def _on_drag(self, event: tk.Event) -> None:
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
        self._closed = True
        self._cancel_hover()
        for window in (self._hover, self.window):
            if window is not None:
                try:
                    window.destroy()
                except tk.TclError:
                    pass
        self._hover = None
