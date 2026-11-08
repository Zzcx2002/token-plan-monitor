from __future__ import annotations

import queue
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

import pystray
from PIL import ImageTk

from .assets import make_brand_icon, make_tray_icon
from .desktop_widget import DesktopWidget
from .ui_graphics import RoundedPanel, RoundedProgress, install_window_effects, configure_rounded_buttons
from .models import (
    ConfigurationError,
    Credentials,
    MonitorViewState,
    QueryError,
    QuotaWindow,
    Settings,
)
from .monitor import UsageMonitor
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

APP_ICON_PATH = Path(__file__).resolve().parents[2] / "assets" / "app.ico"
FONT_FAMILY = "Microsoft YaHei UI"
POPUP_WIDTH = 520
COLORS = {
    "surface": "#F2F3F6",
    "card": "#FFFFFF",
    "border": "#E3E5EB",
    "text": "#1D1D1F",
    "muted": "#636875",
    "accent": "#007AFF",
    "accent_active": "#0062CC",
    "success": "#248A3D",
    "warning": "#B25000",
    "warning_surface": "#FFF7E8",
    "error": "#D70015",
    "info_surface": "#EDF5FF",
    "trough": "#E9E9EE",
}


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

        self.root = tk.Tk()
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

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(
            ".",
            font=(FONT_FAMILY, 9),
            background=COLORS["surface"],
            foreground=COLORS["text"],
        )
        style.configure("TFrame", background=COLORS["surface"])
        style.configure(
            "Card.TFrame",
            background=COLORS["card"],
            bordercolor=COLORS["border"],
            borderwidth=0,
            relief="flat",
        )
        style.configure("TLabel", background=COLORS["surface"], foreground=COLORS["text"])
        style.configure(
            "Card.TLabel", background=COLORS["card"], foreground=COLORS["text"]
        )
        style.configure(
            "Title.TLabel", font=(FONT_FAMILY, 17, "bold"), foreground=COLORS["text"]
        )
        style.configure(
            "Section.TLabel",
            font=(FONT_FAMILY, 10, "bold"),
            background=COLORS["card"],
            foreground=COLORS["text"],
        )
        style.configure("Muted.TLabel", foreground=COLORS["muted"])
        style.configure(
            "CardMuted.TLabel", background=COLORS["card"], foreground=COLORS["muted"]
        )
        style.configure("Success.TLabel", foreground=COLORS["success"])
        style.configure("Error.TLabel", foreground=COLORS["error"])
        style.configure("Warning.TLabel", foreground=COLORS["warning"])
        style.configure(
            "Remaining.TLabel",
            font=(FONT_FAMILY, 16, "bold"),
            background=COLORS["card"],
            foreground=COLORS["text"],
        )
        style.configure(
            "WarningRemaining.TLabel",
            font=(FONT_FAMILY, 16, "bold"),
            background=COLORS["card"],
            foreground=COLORS["warning"],
        )
        style.configure(
            "EmptyTitle.TLabel",
            font=(FONT_FAMILY, 12, "bold"),
            background=COLORS["card"],
            foreground=COLORS["text"],
        )
        style.configure(
            "WarningBanner.TLabel",
            background=COLORS["warning_surface"],
            foreground=COLORS["warning"],
            padding=(12, 8),
        )
        style.configure(
            "InfoBanner.TLabel",
            background=COLORS["info_surface"],
            foreground=COLORS["accent_active"],
            padding=(12, 8),
        )
        style.configure("TButton", padding=(16, 9), borderwidth=0, focusthickness=2)
        style.configure(
            "Primary.TButton",
            font=(FONT_FAMILY, 9, "bold"),
            foreground="#FFFFFF",
            background=COLORS["accent"],
            focuscolor=COLORS["accent"],
        )
        style.map(
            "Primary.TButton",
            background=[
                ("disabled", "#A9C7F5"),
                ("pressed", COLORS["accent_active"]),
                ("active", COLORS["accent_active"]),
            ],
            foreground=[("disabled", "#F8FAFC")],
        )
        style.configure(
            "Secondary.TButton",
            background=COLORS["card"],
            foreground=COLORS["text"],
            bordercolor=COLORS["border"],
            borderwidth=1,
        )
        style.map(
            "Secondary.TButton",
            background=[("pressed", "#E8EDF4"), ("active", "#F0F3F8")],
        )
        configure_rounded_buttons(style)
        style.configure("TEntry", fieldbackground=COLORS["card"], padding=9,
                        bordercolor=COLORS["border"], lightcolor=COLORS["border"],
                        darkcolor=COLORS["border"])
        style.map("TEntry", bordercolor=[("focus", COLORS["accent"])],
                  lightcolor=[("focus", COLORS["accent"])],
                  darkcolor=[("focus", COLORS["accent"])])

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
            colors=COLORS,
            font_family=FONT_FAMILY,
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
        popup = tk.Toplevel(self.root)
        popup.title("火山方舟订阅容量")
        popup.configure(background=COLORS["surface"])
        popup.resizable(False, False)
        popup.geometry(f"{POPUP_WIDTH}x320")
        popup.protocol("WM_DELETE_WINDOW", popup.withdraw)
        popup.bind("<Escape>", lambda event: popup.withdraw())
        popup.bind("<F5>", lambda event: self._request_refresh())
        self._apply_icon(popup)
        install_window_effects(popup, COLORS["surface"])
        popup.withdraw()
        self.popup = popup

        container = ttk.Frame(popup, padding=24)
        container.pack(fill="both", expand=True)
        header = ttk.Frame(container)
        header.pack(fill="x")
        header.columnconfigure(1, weight=1)
        self._brand_image = ImageTk.PhotoImage(make_brand_icon(32))
        ttk.Label(header, image=self._brand_image).grid(
            row=0, column=0, rowspan=2, sticky="nw", padx=(0, 11)
        )
        ttk.Label(header, text="订阅容量", style="Title.TLabel").grid(
            row=0, column=1, sticky="w"
        )
        self.header_status_label = ttk.Label(header, text="待配置", style="Muted.TLabel")
        self.header_status_label.grid(row=0, column=2, sticky="e", pady=(3, 0))
        self.plan_label = ttk.Label(
            header,
            text="火山方舟 Agent Plan / Coding Plan",
            style="Muted.TLabel",
            wraplength=POPUP_WIDTH - 100,
        )
        self.plan_label.grid(row=1, column=1, columnspan=2, sticky="w", pady=(3, 0))

        self.status_label = ttk.Label(
            container,
            text="",
            wraplength=POPUP_WIDTH - 42,
            justify="left",
        )
        self.content_frame = ttk.Frame(container)
        self.content_frame.pack(fill="both", expand=True, pady=(15, 0))
        self.quota_frame = ttk.Frame(self.content_frame)
        self._create_quota_cards(self.quota_frame)
        self._create_empty_panel(self.content_frame)

        footer = ttk.Frame(container)
        footer.pack(fill="x", pady=(12, 0))
        self.updated_label = ttk.Label(footer, text="尚未更新", style="Muted.TLabel")
        self.updated_label.pack(anchor="w", pady=(0, 8))
        actions = ttk.Frame(footer)
        actions.pack(fill="x")
        ttk.Button(
            actions,
            text="设置",
            style="Secondary.TButton",
            command=self.open_settings,
        ).pack(side="right")
        self.refresh_button = ttk.Button(
            actions,
            text="立即刷新",
            style="Primary.TButton",
            command=self._request_refresh,
        )
        self.refresh_button.pack(side="right", padx=(0, 8))

    def _create_quota_cards(self, parent: ttk.Frame) -> None:
        for kind in ("five_hour", "weekly", "monthly"):
            panel = RoundedPanel(
                parent,
                radius=14,
                fill=COLORS["card"],
                outline=COLORS["border"],
                background=COLORS["surface"],
                padding=(17, 12),
            )
            frame = panel.content
            frame.columnconfigure(0, weight=1)
            ttk.Label(frame, text=WINDOW_LABELS[kind], style="Section.TLabel").grid(
                row=0, column=0, sticky="w"
            )
            remaining = ttk.Label(frame, text="剩余 —", style="Remaining.TLabel")
            remaining.grid(row=0, column=1, rowspan=2, sticky="e")
            used = ttk.Label(frame, text="已用 —", style="CardMuted.TLabel")
            used.grid(row=1, column=0, sticky="w", pady=(3, 0))
            progress = RoundedProgress(
                frame,
                background=COLORS["card"],
                trough=COLORS["trough"],
            )
            progress.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 8))
            absolute = ttk.Label(
                frame,
                text="",
                style="CardMuted.TLabel",
                wraplength=POPUP_WIDTH - 100,
                justify="left",
            )
            absolute.grid(row=3, column=0, columnspan=2, sticky="w")
            reset = ttk.Label(frame, text="重置时间：暂无", style="CardMuted.TLabel")
            reset.grid(row=4, column=0, columnspan=2, sticky="w", pady=(3, 0))
            self.rows[kind] = {
                "frame": panel,
                "remaining": remaining,
                "used": used,
                "progress": progress,
                "absolute": absolute,
                "reset": reset,
            }

    def _create_empty_panel(self, parent: ttk.Frame) -> None:
        panel = RoundedPanel(
            parent,
            radius=14,
            fill=COLORS["card"],
            outline=COLORS["border"],
            background=COLORS["surface"],
            padding=(18, 14),
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
            wraplength=POPUP_WIDTH - 112,
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
        if not self._popup_positioned:
            self.popup.update_idletasks()
            width = POPUP_WIDTH
            height = max(240, self.popup.winfo_reqheight())
            x = max(0, (self.popup.winfo_screenwidth() - width) // 2)
            y = max(0, (self.popup.winfo_screenheight() - height) // 2)
            self.popup.geometry(f"{width}x{height}+{x}+{y}")
            self._popup_positioned = True
        self.popup.deiconify()
        self.popup.lift()
        self.popup.focus_force()

    def _fit_popup(self) -> None:
        if self.popup is None:
            return
        self.popup.update_idletasks()
        height = max(240, self.popup.winfo_reqheight())
        if self.popup.winfo_width() != POPUP_WIDTH or self.popup.winfo_height() != height:
            self.popup.geometry(f"{POPUP_WIDTH}x{height}")

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
        self.header_status_label.config(text=status_text, style=status_style)

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
                f"更新于 {_format_datetime(snapshot.queried_at)}"
                if snapshot
                else "尚未更新"
            )
        )

        windows = {window.kind: window for window in snapshot.windows} if snapshot else {}
        visible_kinds = tuple(kind for kind in WINDOW_LABELS if kind in windows)
        for kind, row in self.rows.items():
            window = windows.get(kind)
            if window is not None:
                self._render_window(row, window)

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
        if layout_key != self._layout_key:
            self._layout_key = layout_key
            self._fit_popup()

        refresh_disabled = state.status == "unconfigured" or state.is_refreshing
        self.refresh_button.config(
            text="刷新中…" if state.is_refreshing else "立即刷新",
            state="disabled" if refresh_disabled else "!disabled",
        )

    def _set_popup_layout(self, has_quota: bool, visible_kinds: tuple[str, ...]) -> None:
        if has_quota:
            self.empty_frame.pack_forget()
            self.quota_frame.pack(fill="x")
            for kind in WINDOW_LABELS:
                row = self.rows[kind]
                if kind in visible_kinds:
                    row["frame"].pack(fill="x", pady=(0, 10))
                else:
                    row["frame"].pack_forget()
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
    def _render_window(row: dict[str, Any], window: QuotaWindow) -> None:
        warning = window.remaining_percent < 10
        row["remaining"].config(
            text=f"剩余 {window.remaining_percent:.1f}%",
            style="WarningRemaining.TLabel" if warning else "Remaining.TLabel",
        )
        row["used"].config(text=f"已用 {window.used_percent:.1f}%")
        row["progress"].set(
            window.used_percent,
            COLORS["warning"] if warning else COLORS["accent"],
        )
        if window.quota is not None and window.used is not None:
            row["absolute"].config(
                text=(
                    f"AFP 剩余 {_format_number(window.remaining_absolute)} · "
                    f"已用 {_format_number(window.used)} / "
                    f"总额 {_format_number(window.quota)}"
                )
            )
        else:
            row["absolute"].config(text="该套餐仅提供百分比")
        reset = _format_datetime(window.reset_at) if window.reset_at else "暂无"
        row["reset"].config(text=f"重置时间：{reset}")

    def open_settings(self) -> None:
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.deiconify()
            self.settings_window.lift()
            self.settings_window.focus_force()
            return

        settings = self.storage.load_settings()
        try:
            existing = self.storage.load_credentials()
        except ConfigurationError:
            existing = None

        # Keep this independent: transient windows can stay hidden with a withdrawn owner.
        window = tk.Toplevel(self.root)
        window.title("火山方舟用量查询设置")
        window.configure(background=COLORS["surface"], padx=22, pady=22)
        window.resizable(False, False)
        self._apply_icon(window)
        install_window_effects(window, COLORS["surface"])
        self.settings_window = window
        window.protocol("WM_DELETE_WINDOW", lambda: self._close_settings(window))

        ttk.Label(window, text="用量查询设置", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            window,
            text="使用账号 AK/SK 查询控制面配额，不是推理 API Key。",
            style="Muted.TLabel",
            wraplength=420,
        ).pack(anchor="w", pady=(5, 16))

        credentials_panel = RoundedPanel(
            window,
            radius=14,
            fill=COLORS["card"],
            outline=COLORS["border"],
            background=COLORS["surface"],
            padding=(14, 12),
        )
        credentials_panel.pack(fill="x")
        credentials_frame = credentials_panel.content
        credentials_frame.columnconfigure(0, weight=1)
        ttk.Label(credentials_frame, text="账号凭据", style="Section.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 4)
        )
        ttk.Label(
            credentials_frame,
            text="仅保存到 Windows 凭据管理器，建议使用最小权限子账号。",
            style="CardMuted.TLabel",
            wraplength=390,
        ).grid(row=1, column=0, sticky="w", pady=(0, 10))

        entries: dict[str, ttk.Entry] = {}
        credential_fields = (
            ("access_key", "AccessKey ID", existing.access_key_id if existing else "", False),
            ("secret_key", "Secret AccessKey（留空保持不变）", "", True),
        )
        for index, (key, label, value, secret) in enumerate(credential_fields):
            row = 2 + index * 2
            ttk.Label(credentials_frame, text=label, style="Card.TLabel").grid(
                row=row, column=0, sticky="w", pady=(5, 4)
            )
            entry = ttk.Entry(credentials_frame, width=46, show="•" if secret else "")
            entry.insert(0, value)
            entry.grid(row=row + 1, column=0, sticky="ew")
            entries[key] = entry

        preferences_panel = RoundedPanel(
            window,
            radius=14,
            fill=COLORS["card"],
            outline=COLORS["border"],
            background=COLORS["surface"],
            padding=(14, 12),
        )
        preferences_panel.pack(fill="x", pady=(12, 0))
        preferences_frame = preferences_panel.content
        preferences_frame.columnconfigure(0, weight=3)
        preferences_frame.columnconfigure(1, weight=1)
        ttk.Label(preferences_frame, text="查询偏好", style="Section.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )
        ttk.Label(preferences_frame, text="Region", style="Card.TLabel").grid(
            row=1, column=0, sticky="w", pady=(0, 4)
        )
        ttk.Label(
            preferences_frame,
            text="刷新间隔（分钟，5–1440）",
            style="Card.TLabel",
        ).grid(row=1, column=1, sticky="w", padx=(12, 0), pady=(0, 4))
        region_entry = ttk.Entry(preferences_frame, width=28)
        region_entry.insert(0, settings.region)
        region_entry.grid(row=2, column=0, sticky="ew")
        entries["region"] = region_entry
        refresh_entry = ttk.Entry(preferences_frame, width=16)
        refresh_entry.insert(0, str(settings.refresh_minutes))
        refresh_entry.grid(row=2, column=1, sticky="ew", padx=(12, 0))
        entries["refresh"] = refresh_entry

        def show_validation_error(message: str, field: str | None = None) -> None:
            messagebox.showerror("设置无效", message, parent=window)
            if field is not None:
                entries[field].focus_set()
                entries[field].selection_range(0, "end")

        def save() -> None:
            try:
                new_settings = Settings(
                    region=entries["region"].get(),
                    refresh_minutes=int(entries["refresh"].get().strip()),
                )
            except (ValueError, QueryError) as exc:
                message = exc.message if isinstance(exc, QueryError) else "刷新间隔必须是整数"
                field = (
                    "region"
                    if isinstance(exc, QueryError) and exc.message.startswith("Region")
                    else "refresh"
                )
                show_validation_error(message, field)
                return

            ak = entries["access_key"].get().strip()
            sk = entries["secret_key"].get().strip()
            if not sk and existing is not None:
                sk = existing.secret_access_key
            try:
                credentials = Credentials(ak, sk)
            except QueryError as exc:
                show_validation_error(exc.message, "access_key" if not ak else "secret_key")
                return
            try:
                self.storage.save_credentials(credentials)
                self.storage.save_settings(new_settings)
            except QueryError as exc:
                show_validation_error(exc.message)
                return
            self.monitor.update_configuration(new_settings, credentials)
            self._close_settings(window)
            self.show_popup()

        buttons = ttk.Frame(window)
        buttons.pack(fill="x", pady=(16, 0))
        ttk.Button(
            buttons,
            text="取消",
            style="Secondary.TButton",
            command=lambda: self._close_settings(window),
        ).pack(side="right")
        ttk.Button(
            buttons,
            text="保存设置",
            style="Primary.TButton",
            command=save,
        ).pack(side="right", padx=(0, 8))
        window.bind("<Return>", lambda event: save())
        window.bind("<Escape>", lambda event: self._close_settings(window))
        initial_field = "secret_key" if existing is not None else "access_key"
        window.after_idle(entries[initial_field].focus_set)

    def _close_settings(self, window: tk.Toplevel) -> None:
        if window.winfo_exists():
            window.destroy()
        self.settings_window = None
        if self.popup is not None and self.popup.winfo_viewable():
            self.popup.focus_set()

    def shutdown(self) -> None:
        if self._closing:
            return
        self._closing = True
        self.monitor.stop()
        if self.desktop_widget is not None:
            self.desktop_widget.close()
        if self.icon is not None:
            self.icon.stop()
        try:
            self.root.destroy()
        except tk.TclError:
            pass
