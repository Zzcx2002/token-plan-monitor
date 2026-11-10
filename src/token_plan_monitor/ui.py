from __future__ import annotations

import queue
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from datetime import datetime, UTC
from tkinter import messagebox, ttk
from typing import Any

import pystray

from .assets import make_tray_icon
from .appearance import (AppearancePreferences, LIGHT, THEME_LABELS, resolve_theme,
                         installed_fonts, OUTER_PADDING, SECTION_GAP, ROW_PADDING)
from .desktop_widget import DesktopWidget
from .ui_graphics import (RoundedPanel, RoundedProgress, ScrollableBody, Tooltip, dpi_scale, window_work_area,
                          install_window_effects, apply_window_effects, configure_rounded_buttons)
from .models import (
    ConfigurationError,
    Credentials,
    MonitorViewState,
    QueryError,
    QuotaWindow,
    Settings,
)
from .monitor import UsageMonitor
from .providers import get_provider
from .presentation import (
    PLAN_LABELS,
    WINDOW_LABELS,
    format_datetime as _format_datetime,
    format_number as _format_number,
    menu_context_line,
    menu_window_line,
    state_tone,
    status_text as presentation_status_text,
    tray_tooltip,
)
from .storage import Storage
from .window_geometry import WindowGeometry
from .window_chrome import WindowChrome

APP_ICON_PATH = Path(__file__).resolve().parents[2] / "assets" / "app.ico"
POPUP_WIDTH = 440
COLORS = LIGHT.colors


class MonitorUI:
    def __init__(self, monitor: UsageMonitor, storage: Storage):
        self.monitor = monitor
        self.storage = storage
        self.monitor.on_state = lambda state: self._events.put(("state", state))
        self._events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._closing = False
        self._state = MonitorViewState()
        self._layout_key: tuple[Any, ...] | None = None
        self._menu_key: tuple[Any, ...] | None = None
        self._popup_positioned = False
        self.theme_mode = storage.load_appearance().theme
        self.theme = resolve_theme(self.theme_mode)
        self.colors = dict(self.theme.colors)
        self._details_expanded = False
        self._settings_original_mode = None
        self._theme_poll_after = None
        self._tooltips = []

        self.root = tk.Tk()
        self.font_family, self.latin_font = installed_fonts(tkfont.families(self.root))
        self.root.option_add("*Font", (self.font_family, 10))
        self.root.withdraw()
        self.root.title("火山方舟用量监控")
        self._apply_icon(self.root, default=True)
        self._configure_style()

        self.popup: tk.Toplevel | None = None
        self.plan_label: ttk.Label | None = None
        self.header_status_label: ttk.Label | None = None
        self.status_label: ttk.Label | None = None
        self.updated_label: ttk.Label | None = None
        self.content_frame: ttk.Frame | None = None
        self.quota_frame: ttk.Frame | None = None
        self.empty_frame: tk.Frame | None = None
        self.empty_title_label: ttk.Label | None = None
        self.empty_text_label: ttk.Label | None = None
        self.empty_action_button: ttk.Button | None = None
        self.refresh_button: ttk.Button | None = None
        self.rows: dict[str, dict[str, Any]] = {}
        self.settings_window: tk.Toplevel | None = None
        self.desktop_widget: DesktopWidget | None = None
        self.icon: pystray.Icon | None = None
        self._theme_poll_after = self.root.after(2000, self._poll_theme)

    @staticmethod
    def _apply_icon(window: tk.Tk | tk.Toplevel, default: bool = False) -> None:
        if not APP_ICON_PATH.exists():
            return
        try:
            if default:
                window.iconbitmap(default=str(APP_ICON_PATH))
            else:
                window.iconbitmap(str(APP_ICON_PATH))
        except tk.TclError:
            pass

    def px(self,value:int) -> int:
        return round(value*dpi_scale(self.root))

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(
            ".",
            font=(self.font_family, 10),
            background=self.colors["surface"],
            foreground=self.colors["text"],
        )
        style.configure("TFrame", background=self.colors["surface"])
        style.configure(
            "Card.TFrame",
            background=self.colors["card"],
            bordercolor=self.colors["border"],
            borderwidth=0,
            relief="flat",
        )
        style.configure("TLabel", background=self.colors["surface"], foreground=self.colors["text"])
        style.configure(
            "Card.TLabel", background=self.colors["card"], foreground=self.colors["text"]
        )
        style.configure(
            "Title.TLabel", font=(self.font_family, 15, "bold"), foreground=self.colors["text"]
        )
        style.configure(
            "Section.TLabel",
            font=(self.font_family, 10, "bold"),
            background=self.colors["card"],
            foreground=self.colors["text"],
        )
        style.configure("Muted.TLabel", foreground=self.colors["muted"])
        style.configure("Plan.TLabel", font=(self.latin_font, 10), foreground=self.colors["muted"])
        style.configure(
            "CardMuted.TLabel", background=self.colors["card"], foreground=self.colors["muted"]
        )
        style.configure("Success.TLabel", foreground=self.colors["success"])
        style.configure("Error.TLabel", foreground=self.colors["error"])
        style.configure("Warning.TLabel", foreground=self.colors["warning"])
        style.configure(
            "Remaining.TLabel",
            font=(self.latin_font, 18, "bold"),
            background=self.colors["card"],
            foreground=self.colors["text"],
        )
        style.configure(
            "WarningRemaining.TLabel",
            font=(self.latin_font, 18, "bold"),
            background=self.colors["card"],
            foreground=self.colors["warning"],
        )
        style.configure(
            "EmptyTitle.TLabel",
            font=(self.font_family, 12, "bold"),
            background=self.colors["card"],
            foreground=self.colors["text"],
        )
        style.configure(
            "WarningBanner.TLabel",
            background=self.colors["warning_surface"],
            foreground=self.colors["warning"],
            padding=(12, 8),
        )
        style.configure(
            "InfoBanner.TLabel",
            background=self.colors["info_surface"],
            foreground=self.colors["accent_active"],
            padding=(12, 8),
        )
        style.configure("TButton", padding=(self.px(14), self.px(7)), borderwidth=0, focusthickness=2)
        style.configure("Toolbar.TButton",padding=(self.px(6),self.px(4)),font=("Segoe UI Symbol",12),
                        background=self.colors["surface"],foreground=self.colors["text"])
        style.configure(
            "Primary.TButton",
            font=(self.font_family, 9, "bold"),
            foreground=self.colors["button_text"],
            background=self.colors["accent"],
            focuscolor=self.colors["accent"],
        )
        style.map(
            "Primary.TButton",
            background=[
                ("disabled", self.colors["button_disabled"]),
                ("pressed", self.colors["accent_active"]),
                ("active", self.colors["accent_active"]),
            ],
            foreground=[("disabled", self.colors["muted"])],
        )
        style.configure(
            "Secondary.TButton",
            background=self.colors["card"],
            foreground=self.colors["text"],
            bordercolor=self.colors["border"],
            borderwidth=0,
        )
        style.map(
            "Secondary.TButton",
            background=[("pressed", self.colors["pressed"]), ("active", self.colors["hover"])],
        )
        configure_rounded_buttons(style, self.theme)
        style.configure("TEntry", fieldbackground=self.colors["card"], padding=self.px(9),
                        bordercolor=self.colors["border"], lightcolor=self.colors["border"],
                        darkcolor=self.colors["border"], foreground=self.colors["text"],
                        insertcolor=self.colors["text"])
        style.map("TEntry", bordercolor=[("focus", self.colors["accent"])],
                  lightcolor=[("focus", self.colors["accent"])],
                  darkcolor=[("focus", self.colors["accent"])])

        style.configure("FieldError.TLabel",background=self.colors["card"],foreground=self.colors["error"])
        style.configure("Theme.TRadiobutton",background=self.colors["card"],foreground=self.colors["text"],
                        padding=(self.px(10),self.px(8)),indicatorcolor=self.colors["card"],focuscolor=self.colors["focus"])
        style.map("Theme.TRadiobutton",background=[("selected",self.colors["info_surface"]),
                                                    ("active",self.colors["hover"])],
                  foreground=[("selected",self.colors["accent"])])

    def _set_theme(self, mode: str, *, persist: bool = False) -> None:
        preferences=AppearancePreferences(mode)
        if persist:
            self.storage.save_appearance(preferences)
            if self.settings_window is not None:
                self._settings_original_mode=mode
        old=self.colors
        selected=resolve_theme(mode)
        self.theme_mode=mode
        if hasattr(self,"_settings_theme_var") and self.settings_window is not None:
            self._settings_theme_var.set(mode)
        if selected.name==self.theme.name:
            return
        self.theme=selected
        self.colors=dict(selected.colors)
        self._configure_style()
        # ttk uses semantic styles; classic Tk surfaces require explicit updates.
        remap={value:self.colors[key] for key,value in old.items()}
        for key in ("highlight","trough","border","muted","text","surface","card"):
            remap[old[key]]=self.colors[key]
        def visit(widget: tk.Misc) -> None:
            for child in widget.winfo_children():
                visit(child)
            if not isinstance(widget,ttk.Widget):
                for option in ("background","foreground","highlightbackground","insertbackground"):
                    try:
                        value=str(widget.cget(option))
                        if value in remap:
                            widget.configure(**{option:remap[value]})
                    except tk.TclError:
                        pass
            if isinstance(widget,RoundedPanel):
                widget.set_theme(self.colors)
            elif isinstance(widget,RoundedProgress):
                widget.trough=self.colors["trough"]
                widget._key=None
                widget._redraw()
        visit(self.root)
        for tip in self._tooltips:
            tip.hide()
        for window in (self.popup,self.settings_window):
            if window is not None and window.winfo_exists():
                window._shadow_opacity=self.theme.shadow_opacity
                apply_window_effects(window,self.colors["surface"])
                window._chrome.set_theme(self.colors)
        if self.desktop_widget is not None:
            self.desktop_widget.set_theme(self.colors,self.theme.shadow_opacity)
        self._render_state(self._state)

    def _poll_theme(self) -> None:
        self._theme_poll_after=None
        if self._closing:
            return
        if self.theme_mode=="system":
            self._set_theme("system")
        if self.updated_label is not None and self._state.snapshot is not None:
            self.updated_label.configure(text=self._updated_text(self._state.snapshot.queried_at))
        self._theme_poll_after=self.root.after(2000,self._poll_theme)

    def _show_theme_menu(self) -> None:
        menu=tk.Menu(self.popup,tearoff=False)
        self._theme_menu=menu
        self._theme_var=tk.StringVar(self.root,self.theme_mode)
        def choose(mode: str) -> None:
            try:
                self._set_theme(mode,persist=True)
            except QueryError as exc:
                messagebox.showerror("无法保存外观",exc.message,parent=self.popup)
        for mode,label in THEME_LABELS.items():
            menu.add_radiobutton(label=label,value=mode,variable=self._theme_var,
                                 command=lambda value=mode:choose(value))
        try:
            menu.tk_popup(self.theme_button.winfo_rootx(),
                          self.theme_button.winfo_rooty()+self.theme_button.winfo_height())
        finally:
            menu.grab_release()

    def _toggle_details(self) -> None:
        self._details_expanded=not self._details_expanded
        self.details_button.configure(text="▾ 收起详情" if self._details_expanded else "▸ 展开详情")
        self._render_state(self._state)
        self._fit_popup()

    def run(self) -> None:
        self.monitor.start()
        self._state = self.monitor.get_state()
        self._create_popup()
        self._create_desktop_widget()
        self._render_state(self._state)
        self.icon = pystray.Icon(
            "token-plan-monitor",
            make_tray_icon("unconfigured"),
            "火山方舟用量监控",
            menu=self._create_tray_menu(),
        )
        self.icon.run_detached()
        self._render_state(self._state)
        self.root.after(100, self._process_events)
        if self._state.status == "unconfigured":
            self.root.after(200, self.open_settings)
        try:
            self.root.mainloop()
        finally:
            self.shutdown()

    def _create_desktop_widget(self) -> None:
        self.desktop_widget = DesktopWidget(
            self.root,
            self.storage,
            on_open=self.show_popup,
            on_refresh=self._request_refresh,
            on_settings=self.open_settings,
            on_exit=self.shutdown,
            on_preferences_changed=self._on_widget_preferences_changed,
            colors=self.colors,
            font_family=self.font_family,
            shadow_opacity=self.theme.shadow_opacity,
        )

    def _create_tray_menu(self) -> pystray.Menu:
        def has_snapshot(item: Any) -> bool:
            return self._state.snapshot is not None

        def window_text(kind: str) -> Any:
            return lambda item: menu_window_line(self._state, kind)

        def refresh_enabled(item: Any) -> bool:
            return self._state.status != "unconfigured" and not self._state.is_refreshing

        def widget_checked(item: Any) -> bool:
            return self.desktop_widget.visible if self.desktop_widget else True

        def topmost_checked(item: Any) -> bool:
            return self.desktop_widget.topmost if self.desktop_widget else True

        return pystray.Menu(
            pystray.MenuItem(
                lambda item: menu_context_line(self._state), None, enabled=False
            ),
            *(
                pystray.MenuItem(
                    window_text(kind),
                    None,
                    enabled=False,
                    visible=has_snapshot,
                )
                for kind in WINDOW_LABELS
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "打开详细用量",
                self._tray_command("show"),
                default=True,
            ),
            pystray.MenuItem(
                "立即刷新",
                self._tray_command("refresh"),
                enabled=refresh_enabled,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "桌面卡片",
                self._tray_command("toggle_widget"),
                checked=widget_checked,
            ),
            pystray.MenuItem(
                "卡片置顶",
                self._tray_command("toggle_topmost"),
                checked=topmost_checked,
            ),
            pystray.MenuItem("设置", self._tray_command("settings")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self._tray_command("exit")),
        )

    def _on_widget_preferences_changed(self) -> None:
        self._update_tray_menu()

    def _update_tray_menu(self) -> None:
        if self.icon is None:
            return
        key = (
            tray_tooltip(self._state),
            menu_context_line(self._state),
            *(menu_window_line(self._state, kind) for kind in WINDOW_LABELS),
            self._state.status,
            self._state.is_refreshing,
            self.desktop_widget.visible if self.desktop_widget else True,
            self.desktop_widget.topmost if self.desktop_widget else True,
        )
        if key != self._menu_key:
            self._menu_key = key
            self.icon.update_menu()

    def _tray_command(self, command: str) -> Any:
        def callback(icon: Any = None, item: Any = None) -> None:
            self._events.put((command, None))

        return callback

    def _process_events(self) -> None:
        if self._closing:
            return
        try:
            while True:
                command, payload = self._events.get_nowait()
                if command == "state":
                    self._state = payload
                    self._render_state(payload)
                elif command == "show":
                    self.show_popup()
                elif command == "refresh":
                    self._request_refresh()
                elif command == "settings":
                    self.open_settings()
                elif command == "toggle_widget" and self.desktop_widget is not None:
                    self.desktop_widget.set_visible(not self.desktop_widget.visible)
                elif command == "toggle_topmost" and self.desktop_widget is not None:
                    self.desktop_widget.toggle_topmost()
                elif command == "exit":
                    self.shutdown()
                    return
        except queue.Empty:
            pass
        self.root.after(100, self._process_events)

    def _create_popup(self) -> None:
        popup=tk.Toplevel(self.root)
        popup.withdraw()
        popup.title("火山方舟订阅容量")
        popup.configure(background=self.colors["surface"])
        popup.resizable(True,True)
        popup.protocol("WM_DELETE_WINDOW",popup.withdraw)
        popup.bind("<Escape>",lambda event:popup.withdraw())
        popup.bind("<F5>",lambda event:self._request_refresh())
        self._apply_icon(popup)
        install_window_effects(popup,self.colors["surface"])
        self.popup=popup
        self.popup_geometry=WindowGeometry(popup,self.storage,"popup",(360,220))
        popup._geometry=self.popup_geometry
        popup._chrome=WindowChrome(popup,"订阅用量",self.colors,self.font_family,popup.withdraw)
        self.popup_body=ScrollableBody(popup,self.colors)
        self.popup_body.pack(fill="both",expand=True)
        container=self.popup_body.content
        header=popup._chrome.actions
        self.theme_button=ttk.Button(header,text="◐",style="Toolbar.TButton",width=2,command=self._show_theme_menu)
        self.theme_button.pack(side="left")
        settings_button=ttk.Button(header,text="⚙",style="Toolbar.TButton",width=2,command=self.open_settings)
        settings_button.pack(side="left")
        for widget,text in ((self.theme_button,"切换外观：浅色、深色或跟随系统"),(settings_button,"打开设置")):
            self._tooltips.append(Tooltip(widget,text,lambda:self.colors))
        self.plan_label=ttk.Label(container,text="Agent Plan / Coding Plan",style="Plan.TLabel",
                                  wraplength=round(240*dpi_scale(popup)))
        self.plan_label.pack(anchor="w")
        self.header_status_label=ttk.Label(container,text="",style="Muted.TLabel")
        self.header_status_label.pack(anchor="w",pady=(8,0))
        self.status_label=ttk.Label(container,text="",wraplength=round(360*dpi_scale(popup)),justify="left")
        self.content_frame=ttk.Frame(container)
        self.content_frame.pack(fill="both",expand=True,pady=(SECTION_GAP,0))
        self.quota_frame=ttk.Frame(self.content_frame)
        self._create_quota_cards(self.quota_frame)
        self._create_empty_panel(self.content_frame)
        self.popup_footer=ttk.Frame(popup,padding=(self.px(OUTER_PADDING),0,self.px(OUTER_PADDING),self.px(12)))
        self.popup_footer.pack(fill="x")
        self.updated_label=ttk.Label(self.popup_footer,text="尚未更新",style="Muted.TLabel")
        self.updated_label.pack(side="left")
        self.refresh_button=ttk.Button(self.popup_footer,text="刷新",style="Primary.TButton",command=self._request_refresh)
        self.refresh_button.pack(side="right")

    def _create_quota_cards(self,parent:ttk.Frame) -> None:
        self.quota_panel=RoundedPanel(parent,radius=14,fill=self.colors["card"],
            outline=self.colors["border"],background=self.colors["surface"],padding=(9,3))
        self.quota_panel.pack(fill="x")
        for kind in ("five_hour","weekly","monthly"):
            frame=ttk.Frame(self.quota_panel.content,style="Card.TFrame",padding=(self.px(8),self.px(ROW_PADDING)))
            frame.columnconfigure(0,weight=1)
            ttk.Label(frame,text=WINDOW_LABELS[kind],style="Section.TLabel").grid(row=0,column=0,sticky="w")
            remaining=ttk.Label(frame,text="—",style="Remaining.TLabel")
            remaining.grid(row=0,column=1,sticky="e")
            progress=RoundedProgress(frame,background=self.colors["card"],trough=self.colors["trough"],
                                     height=max(4,round(5*dpi_scale(parent))))
            progress.grid(row=1,column=0,columnspan=2,sticky="ew",pady=(self.px(5),self.px(6)))
            reset=ttk.Label(frame,text="重置时间暂无",style="CardMuted.TLabel",
                            wraplength=round(320*dpi_scale(parent)))
            reset.grid(row=2,column=0,columnspan=2,sticky="w")
            details=ttk.Frame(frame,style="Card.TFrame")
            used=ttk.Label(details,text="",style="CardMuted.TLabel")
            used.pack(anchor="w")
            absolute=ttk.Label(details,text="",style="CardMuted.TLabel",
                               wraplength=round(320*dpi_scale(parent)),justify="left")
            absolute.pack(anchor="w",pady=(4,0))
            full_reset=ttk.Label(details,text="",style="CardMuted.TLabel")
            full_reset.pack(anchor="w",pady=(4,0))
            separator=tk.Frame(self.quota_panel.content,bg=self.colors["border"],height=1)
            self.rows[kind]={"frame":frame,"remaining":remaining,"used":used,"progress":progress,
                              "absolute":absolute,"reset":reset,"full_reset":full_reset,
                              "details":details,"separator":separator}
        self.details_button=ttk.Button(parent,text="▸ 展开详情",style="Secondary.TButton",command=self._toggle_details)
        self.details_button.pack(anchor="w",pady=(SECTION_GAP,0))

    def _create_empty_panel(self, parent: ttk.Frame) -> None:
        panel = RoundedPanel(
            parent,
            radius=14,
            fill=self.colors["card"],
            outline=self.colors["border"],
            background=self.colors["surface"],
            padding=(12, 8),
        )
        self.empty_frame = panel
        frame = panel.content
        self.empty_title_label = ttk.Label(
            frame,
            text="尚未配置用量查询",
            style="EmptyTitle.TLabel",
        )
        self.empty_title_label.pack(anchor="w")
        self.empty_text_label = ttk.Label(
            frame,
            text="",
            style="CardMuted.TLabel",
            wraplength=self.px(POPUP_WIDTH - 112),
            justify="left",
        )
        self.empty_text_label.pack(anchor="w", fill="x", pady=(7, 14))
        self.empty_action_button = ttk.Button(
            frame,
            text="去设置",
            style="Primary.TButton",
            command=self.open_settings,
        )
        self.empty_action_button.pack(anchor="w")

    def _request_refresh(self) -> None:
        if self._state.status != "unconfigured" and not self._state.is_refreshing:
            self.monitor.request_refresh()

    def show_popup(self) -> None:
        if self.popup is None:
            return
        self._fit_popup()
        self.popup.deiconify()
        if not self._popup_positioned:
            self.popup.update_idletasks()
            width, height = self.popup.winfo_width(), self.popup.winfo_height()
            left,top,right,bottom=window_work_area(self.popup)
            x=left+max(0,(right-left-width)//2)
            y=top+max(0,(bottom-top-height)//2)
            self.popup.geometry(f"{width}x{height}+{x}+{y}")
            self._popup_positioned = True
        self.popup.deiconify()
        self.popup.lift()
        self.popup.focus_force()

    def _fit_popup(self) -> None:
        if self.popup is None:
            return
        if self.popup_geometry.initialized:
            return
        self.popup.update_idletasks()
        scale=dpi_scale(self.popup)
        width=max(round(POPUP_WIDTH*scale),self.popup_body.content.winfo_reqwidth())
        left,top,right,bottom=window_work_area(self.popup)
        width=min(width,right-left-40)
        footer=self.popup_footer.winfo_reqheight()+self.popup._chrome.header.winfo_reqheight()+self.px(8)
        available=max(180,bottom-top-70-footer)
        height=self.popup_body.fit(available)+footer
        self._popup_size=(width,height)
        self.popup_geometry.fit((width,height))

    def _render_state(self, state: MonitorViewState) -> None:
        if self.desktop_widget is not None:
            self.desktop_widget.render(state)
        if self.icon is not None:
            self.icon.icon = make_tray_icon(state_tone(state))
            self.icon.title = tray_tooltip(state)
            self._update_tray_menu()
        if self.plan_label is None or self.content_frame is None:
            return
        snapshot = state.snapshot
        plan_text = (
            snapshot.plan_name or PLAN_LABELS.get(snapshot.plan_type, snapshot.plan_type)
            if snapshot
            else "火山方舟 Agent Plan / Coding Plan"
        )
        self.plan_label.config(text=plan_text)
        status_text = presentation_status_text(state)
        status_style = (
            "Error.TLabel"
            if state.status == "error"
            else "Warning.TLabel"
            if state.status == "stale"
            else "Success.TLabel"
            if state.status == "ok" and not state.is_refreshing
            else "Muted.TLabel"
        )
        self.header_status_label.config(text="" if state.status=="ok" and not state.is_refreshing else status_text, style=status_style)
        if state.status=="ok" and not state.is_refreshing:
            self.header_status_label.pack_forget()
        else:
            self.header_status_label.pack(before=self.content_frame,anchor="w",pady=(8,0))

        banner_text = ""
        banner_style = "WarningBanner.TLabel"
        if state.is_refreshing and snapshot:
            banner_text = "正在刷新，当前显示上次成功查询的数据。"
            banner_style = "InfoBanner.TLabel"
        elif state.status == "stale" or (state.error and snapshot):
            banner_text = f"数据可能过期 · {state.error or '请检查网络后重试'}"
        if banner_text:
            self.status_label.config(text=banner_text, style=banner_style)
            self.status_label.pack(
                before=self.content_frame, fill="x", pady=(12, 0)
            )
        else:
            self.status_label.pack_forget()
        self.updated_label.config(
            text=(
                self._updated_text(snapshot.queried_at)
                if snapshot
                else "尚未更新"
            )
        )

        windows = {window.kind: window for window in snapshot.windows} if snapshot else {}
        visible_kinds = tuple(kind for kind in WINDOW_LABELS if kind in windows)
        unit = get_provider(state.provider).unit_label
        for kind, row in self.rows.items():
            window = windows.get(kind)
            if window is not None:
                self._render_window(row, window, unit)

        empty_key: tuple[str, str, str] | None = None
        if not visible_kinds:
            empty_key = self._render_empty_state(state)
        self._set_popup_layout(bool(visible_kinds), visible_kinds)
        layout_key = (
            bool(visible_kinds),
            visible_kinds,
            plan_text,
            banner_text,
            empty_key,
        )
        self._layout_key = layout_key
        self._fit_popup()

        refresh_disabled = state.status == "unconfigured" or state.is_refreshing
        self.refresh_button.config(
            text="刷新中…" if state.is_refreshing else "刷新",
            state="disabled" if refresh_disabled else "!disabled",
        )

    def _set_popup_layout(self,has_quota:bool,visible_kinds:tuple[str,...]) -> None:
        if has_quota:
            self.empty_frame.pack_forget()
            self.quota_frame.pack(fill="x")
            for kind in WINDOW_LABELS:
                row=self.rows[kind]
                row["frame"].pack_forget()
                row["separator"].pack_forget()
                if kind in visible_kinds:
                    row["frame"].pack(fill="x")
        else:
            self.quota_frame.pack_forget()
            self.empty_frame.pack(fill="x")

    def _render_empty_state(self, state: MonitorViewState) -> tuple[str, str, str]:
        if state.status == "unconfigured":
            title = "尚未配置用量查询"
            text = "添加火山账号 AK/SK 后，即可在桌面查看订阅容量。"
            action = "去设置"
            command = self.open_settings
        elif state.status == "error":
            title = "暂时无法获取用量"
            text = state.error or "请检查网络连接后重试。"
            action = "重试"
            command = self._request_refresh
        elif state.is_refreshing:
            title = "正在获取订阅容量"
            text = "查询完成后，这里会显示 5 小时、每周和每月配额。"
            action = ""
            command = None
        else:
            title = "暂无用量的数据"
            text = "可以立即查询，或打开设置检查账号与刷新配置。"
            action = "立即刷新"
            command = self._request_refresh
        self.empty_title_label.config(text=title)
        self.empty_text_label.config(text=text)
        if action:
            self.empty_action_button.config(text=action, command=command)
            self.empty_action_button.pack(anchor="w")
        else:
            self.empty_action_button.pack_forget()
        return title, text, action

    @staticmethod
    def _updated_text(value:datetime) -> str:
        age=max(0,(datetime.now(UTC)-value).total_seconds())
        if age<60:
            return "刚刚更新"
        if age<3600:
            return f"{int(age//60)} 分钟前更新"
        return f"更新于 {value.astimezone():%m-%d %H:%M}"

    def _render_window(self,row:dict[str,Any],window:QuotaWindow,unit:str="AFP") -> None:
        warning=window.remaining_percent<10
        row["remaining"].configure(text=f"{window.remaining_percent:.1f}%",
            style="WarningRemaining.TLabel" if warning else "Remaining.TLabel")
        row["used"].configure(text=f"已用 {window.used_percent:.1f}%")
        row["progress"].set(window.remaining_percent,self.colors["warning"] if warning else self.colors["accent"],
                              self.colors["warning_start"] if warning else self.colors["accent_start"])
        has_absolute=window.quota is not None and window.used is not None
        if has_absolute:
            row["absolute"].configure(text=f"{unit} 剩余 {_format_number(window.remaining_absolute)} · "
                f"已用 {_format_number(window.used)} / 总额 {_format_number(window.quota)}")
            row["absolute"].pack(anchor="w",pady=(4,0),before=row["full_reset"])
        else:
            row["absolute"].pack_forget()
        if window.reset_at:
            local=window.reset_at.astimezone()
            delta=(window.reset_at-datetime.now(UTC)).total_seconds()
            if 0<delta<86400:
                summary=f"约 {max(1,round(delta/3600))} 小时后重置" if delta>=3600 else f"约 {max(1,round(delta/60))} 分钟后重置"
            else:
                summary=f"{local.month} 月 {local.day} 日 {local:%H:%M} 重置"
            full=_format_datetime(window.reset_at)
        else:
            summary,full="重置时间暂无","暂无"
        row["reset"].configure(text=("剩余偏低 · " if warning else "")+summary)
        row["full_reset"].configure(text=f"完整重置时间：{full}")
        if self._details_expanded:
            row["details"].grid(row=3,column=0,columnspan=2,sticky="ew",pady=(10,0))
        else:
            row["details"].grid_remove()

    def open_settings(self) -> None:
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.deiconify()
            self.settings_window.lift()
            self.settings_window.focus_force()
            return
        settings=self.storage.load_settings()
        try:
            existing=self.storage.load_credentials()
        except ConfigurationError:
            existing=None
        self._settings_original_mode=self.theme_mode
        window=tk.Toplevel(self.root)
        window.title("火山方舟用量查询设置")
        window.configure(background=self.colors["surface"])
        window.resizable(True,True)
        self._apply_icon(window)
        install_window_effects(window,self.colors["surface"])
        self.settings_window=window
        window._geometry=WindowGeometry(window,self.storage,"settings",(400,260))
        window._chrome=WindowChrome(window,"设置",self.colors,self.font_family,lambda:self._close_settings(window))
        window.protocol("WM_DELETE_WINDOW",lambda:self._close_settings(window))
        body=ScrollableBody(window,self.colors)
        body.pack(fill="both",expand=True)
        content=body.content
        def panel(title:str) -> tk.Frame:
            outer=RoundedPanel(content,radius=14,fill=self.colors["card"],outline=self.colors["border"],
                               background=self.colors["surface"],padding=(12,10))
            outer.pack(fill="x",pady=(0,SECTION_GAP))
            frame=outer.content
            frame.columnconfigure(0,weight=1)
            ttk.Label(frame,text=title,style="Section.TLabel").grid(row=0,column=0,columnspan=2,sticky="w",pady=(0,10))
            return frame
        appearance=panel("外观")
        self._settings_theme_var=tk.StringVar(window,self.theme_mode)
        selector=ttk.Frame(appearance,style="Card.TFrame")
        selector.grid(row=1,column=0,columnspan=2,sticky="ew")
        for column,(mode,label) in enumerate(THEME_LABELS.items()):
            selector.columnconfigure(column,weight=1)
            ttk.Radiobutton(selector,text=label,value=mode,variable=self._settings_theme_var,
                style="Theme.TRadiobutton",command=lambda:self._set_theme(self._settings_theme_var.get())).grid(
                    row=0,column=column,sticky="ew",padx=2)
        ttk.Label(appearance,text="即时预览，保存后记住选择。",style="CardMuted.TLabel").grid(
            row=2,column=0,columnspan=2,sticky="w",pady=(10,0))
        credentials=panel("账号凭据")
        ttk.Label(credentials,text="AK/SK 仅保存到 Windows 凭据管理器。",style="CardMuted.TLabel",
                  wraplength=round(340*dpi_scale(window))).grid(row=1,column=0,sticky="w",pady=(0,8))
        self._settings_entries={}
        self._settings_errors={}
        def entry(frame:tk.Frame,key:str,label:str,value:str,row:int,secret:bool=False,
                  column:int=0) -> ttk.Entry:
            ttk.Label(frame,text=label,style="Card.TLabel").grid(row=row,column=column,sticky="w",pady=(6,5))
            field=ttk.Entry(frame,width=28,show="•" if secret else "")
            field.insert(0,value)
            field.grid(row=row+1,column=column,sticky="ew",padx=(0,8) if column==0 and key=="region" else 0)
            error=ttk.Label(frame,text="",style="FieldError.TLabel",wraplength=round(315*dpi_scale(window)))
            self._settings_entries[key]=field
            self._settings_errors[key]=error
            return field
        entry(credentials,"access_key","AccessKey ID",existing.access_key_id if existing else "",2)
        entry(credentials,"secret_key","Secret AccessKey · 留空保持不变","",5,True)
        preferences=panel("查询偏好")
        preferences.columnconfigure(0,weight=1)
        preferences.columnconfigure(1,weight=1)
        entry(preferences,"region","Region",settings.region,1)
        entry(preferences,"refresh","刷新间隔 · 分钟",str(settings.refresh_minutes),1,column=1)
        # Equal columns keep the settings width stable across long values.
        self._settings_entries["region"].configure(width=16)
        self._settings_entries["refresh"].configure(width=12)
        entries=self._settings_entries
        footer=ttk.Frame(window,padding=(self.px(OUTER_PADDING),0,self.px(OUTER_PADDING),self.px(12)))
        footer.pack(fill="x")
        general_error=ttk.Label(content,text="",style="Error.TLabel",wraplength=round(360*dpi_scale(window)))
        general_error.pack(anchor="w")
        def fit() -> None:
            if not window.winfo_exists():
                return
            if window._geometry.initialized:
                return
            window.update_idletasks()
            width=max(round(460*dpi_scale(window)),content.winfo_reqwidth())
            left,top,right,bottom=window_work_area(window)
            width=min(width,right-left-40)
            fixed=footer.winfo_reqheight()+window._chrome.header.winfo_reqheight()+self.px(8)
            height=body.fit(max(200,bottom-top-70-fixed))
            window._geometry.fit((width,height+fixed))
        def show_validation_error(message:str,field:str|None=None) -> None:
            if field is None:
                general_error.configure(text=message)
            else:
                widget=entries[field]
                widget.state(["invalid"])
                label=self._settings_errors[field]
                label.configure(text=message)
                info=widget.grid_info()
                label.grid(row=int(info["row"])+1,column=int(info["column"]),sticky="w",pady=(4,0))
                widget.focus_set()
                widget.selection_range(0,"end")
            fit()
            if field:
                window.update_idletasks()
                target=entries[field].winfo_rooty()-content.winfo_rooty()
                body.canvas.yview_moveto(max(0,(target-80)/max(1,content.winfo_height())))
        def save() -> None:
            for key,field in entries.items():
                field.state(["!invalid"])
                self._settings_errors[key].grid_remove()
            general_error.configure(text="")
            try:
                new_settings=Settings(region=entries["region"].get(),refresh_minutes=int(entries["refresh"].get().strip()))
            except (ValueError,QueryError) as exc:
                message=exc.message if isinstance(exc,QueryError) else "刷新间隔必须是整数"
                show_validation_error(message,"region" if isinstance(exc,QueryError) and message.startswith("Region") else "refresh")
                return
            ak,sk=entries["access_key"].get().strip(),entries["secret_key"].get().strip()
            if not sk and existing is not None:
                sk=existing.secret_access_key
            try:
                new_credentials=Credentials(ak,sk)
            except QueryError as exc:
                show_validation_error(exc.message,"access_key" if not ak else "secret_key")
                return
            try:
                if new_credentials!=existing:
                    self.storage.save_credentials(new_credentials)
                if new_settings!=settings:
                    self.storage.save_settings(new_settings)
                self.storage.save_appearance(AppearancePreferences(self.theme_mode))
            except QueryError as exc:
                show_validation_error(exc.message)
                return
            if new_settings!=settings or new_credentials!=existing:
                self.monitor.update_configuration(new_settings,new_credentials)
            self._close_settings(window,commit=True)
            self.show_popup()
        ttk.Button(footer,text="保存设置",style="Primary.TButton",command=save).pack(side="right")
        ttk.Button(footer,text="取消",style="Secondary.TButton",command=lambda:self._close_settings(window)).pack(
            side="right",padx=(0,8))
        window.bind("<Return>",lambda event:save())
        window.bind("<Escape>",lambda event:self._close_settings(window))
        window.after_idle(fit)
        window.after_idle(entries["secret_key" if existing is not None else "access_key"].focus_set)
        self.settings_body=body

    def _close_settings(self,window:tk.Toplevel,*,commit:bool=False) -> None:
        original=self._settings_original_mode
        self._settings_original_mode=None
        self.settings_window=None
        if window.winfo_exists():
            window._geometry.flush()
            window.destroy()
        if not commit and original is not None:
            self._set_theme(original)
        if self.popup is not None and self.popup.winfo_viewable():
            self.popup.focus_set()

    def shutdown(self) -> None:
        if self._closing:
            return
        self._closing = True
        if self._theme_poll_after is not None:
            self.root.after_cancel(self._theme_poll_after)
            self._theme_poll_after=None
        for window in (self.popup,self.settings_window):
            if window is not None and window.winfo_exists():
                controller=getattr(window,"_geometry",None) or getattr(self,"popup_geometry",None)
                if controller:
                    controller.flush()
        self.monitor.stop()
        if self.desktop_widget is not None:
            self.desktop_widget.close()
        if self.icon is not None:
            self.icon.stop()
        try:
            self.root.destroy()
        except tk.TclError:
            pass
