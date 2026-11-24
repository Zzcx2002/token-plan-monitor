from __future__ import annotations

import logging
import os
import queue
import threading
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from datetime import datetime, UTC
from tkinter import messagebox, ttk
from typing import Any

import pystray

from . import claude_code
from .assets import make_tray_icon
from .appearance import (AppearancePreferences, LIGHT, RADIUS, THEME_LABELS,
                         resolve_theme, installed_fonts, OUTER_PADDING, SECTION_GAP, ROW_PADDING)
from .claude_code import ClaudeConfigError
from .desktop_widget import DesktopWidget
from .history_view import HistoryWindow
from .model_catalog import (
    CATALOG_UNSUPPORTED_URL,
    catalog_block_reason,
    fetch_model_ids,
    merge_model_options,
)
from .ui_graphics import (RoundedPanel, RoundedProgress, ScrollableBody, Tooltip, dpi_scale, window_work_area,
                          install_window_effects, apply_window_effects, calibrate_tk_scaling,
                          combobox_palette, configure_combobox, configure_rounded_buttons,
                          configure_scrollbars, invalidate_scaled_caches, recolor_combobox_popdown,
                          resolve_material, tcl_events_deferred, transparency_enabled)
from .models import (
    ClaudeProviderPrefs,
    MonitorViewState,
    QueryError,
    QuotaWindow,
    WindowTrend,
    provider_label,
)
from .monitor import UsageMonitor
from .paths import resource_path
from .providers import get_provider
from .settings_rules import resolve_claude_seed
from .settings_view import SettingsDialog
from .presentation import (
    PLAN_LABELS,
    WINDOW_LABELS,
    alert_notification,
    format_datetime as _format_datetime,
    format_number as _format_number,
    menu_context_line,
    menu_window_line,
    state_tone,
    status_text as presentation_status_text,
    trend_line,
    tray_tooltip,
)
from .storage import Storage
from .window_geometry import WindowGeometry
from .window_chrome import WindowChrome

APP_ICON_PATH = resource_path("assets", "app.ico")
POPUP_WIDTH = 440
#: Point size for quota-detail auxiliary captions; one step below the 10pt
#: section titles so the hierarchy reads at a glance.
DETAIL_FONT_SIZE = 9
COLORS = LIGHT.colors

logger = logging.getLogger(__name__)


class MonitorUI:
    def __init__(self, monitor: UsageMonitor, storage: Storage):
        self.monitor = monitor
        self.storage = storage
        self.monitor.on_state = lambda state: self._events.put(("state", state))
        # Tray toast delivery for quota alerts; the shared monitor already
        # carries the trigger rules, Windows only supplies the channel.
        if getattr(self.monitor, "notifier", None) is None:
            self.monitor.notifier = self._notify_alert
        self._events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._closing = False
        self._state = MonitorViewState()
        self._layout_key: tuple[Any, ...] | None = None
        self._menu_key: tuple[Any, ...] | None = None
        self._popup_positioned = False
        appearance = storage.load_appearance()
        self.theme_mode = appearance.theme
        # Acrylic only resolves on Win11 with the system transparency switch on.
        self.material = resolve_material(appearance.material)
        self._system_transparency = transparency_enabled()
        self._dpi = 0
        self.theme = resolve_theme(self.theme_mode)
        self.colors = dict(self.theme.colors)
        self._details_expanded = False
        self._settings_dialog: SettingsDialog | None = None
        self._theme_poll_after = None
        self._tooltips = []
        # Claude Code hookup: per-provider form memory so a provider switch
        # never discards typed choices. Non-secret drafts also persist to
        # claude.json; the inference-key draft is memory only and is cleared
        # on shutdown. Mirrors the macOS shell's app-level ownership.
        self._claude_drafts: dict[str, ClaudeProviderPrefs] = {}
        self._claude_key_drafts: dict[str, str] = {}
        # Live model catalogue: background threads only hand results back
        # through this queue; every Tk mutation happens in the 100ms drain.
        self._catalog_events: queue.SimpleQueue = queue.SimpleQueue()
        self._catalog_inflight: set[str] = set()
        #: (base_url, key) signatures of the last automatic fetch per provider,
        #: so typing into the form cannot enqueue identical refetches.
        self._catalog_auto_signatures: dict[str, tuple[str, str]] = {}
        self._catalog_status: dict[str, tuple[str, str]] = {}
        # Injectable seams so the offline smoke stubs the network call and
        # the confirmation dialog without touching a real ~/.claude.
        self._fetch_model_ids = fetch_model_ids
        self._confirm_override: Any = None
        self.claude_paths_override: tuple[Path, Path] | None = None
        self.inference_key: str | None = None
        # Raised while the Claude write confirmation runs its nested event
        # loop, so a queued provider rebuild cannot detach the form mid-write.
        self._applying_claude = False

        self.root = tk.Tk()
        # Pin Tk scaling to the real monitor DPI before any font/style is built.
        self._dpi = round(calibrate_tk_scaling(self.root) * 96)
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
        self.settings_body: ScrollableBody | None = None
        self.history: HistoryWindow | None = None
        self.desktop_widget: DesktopWidget | None = None
        self.icon: pystray.Icon | None = None
        self._theme_poll_after = self.root.after(2000, self._poll_theme)

    @staticmethod
    def _apply_icon(window: tk.Tk | tk.Toplevel, default: bool = False) -> None:
        if not APP_ICON_PATH.exists():
            return
        try:
            # WM_SETICON is a synchronous native window-message boundary.
            with tcl_events_deferred():
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
        style.configure(
            "CardWarning.TLabel", background=self.colors["card"], foreground=self.colors["warning"]
        )
        # Quota-detail captions (reset time, used/absolute values, trend) are
        # deliberately smaller than the 10pt section labels and muted.
        style.configure(
            "DetailMuted.TLabel",
            font=(self.font_family, DETAIL_FONT_SIZE),
            background=self.colors["card"], foreground=self.colors["muted"],
        )
        style.configure(
            "DetailWarning.TLabel",
            font=(self.font_family, DETAIL_FONT_SIZE),
            background=self.colors["card"], foreground=self.colors["warning"],
        )
        style.configure(
            "CardSuccess.TLabel", background=self.colors["card"], foreground=self.colors["success"]
        )
        style.configure(
            "CardError.TLabel", background=self.colors["card"], foreground=self.colors["error"]
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
        configure_scrollbars(style, self.theme)
        configure_combobox(style, self.theme)
        style.configure("TEntry", fieldbackground=self.colors["card"], padding=self.px(9),
                        bordercolor=self.colors["border"], lightcolor=self.colors["border"],
                        darkcolor=self.colors["border"], foreground=self.colors["text"],
                        insertcolor=self.colors["text"])
        style.map("TEntry", bordercolor=[("focus", self.colors["accent"])],
                  lightcolor=[("focus", self.colors["accent"])],
                  darkcolor=[("focus", self.colors["accent"])])

        style.configure("FieldError.TLabel",background=self.colors["card"],foreground=self.colors["error"])
        # clam reads indicatorbackground/indicatorforeground (not indicatorcolor);
        # the old indicatorcolor lookup missed, leaving white indicator squares
        # in dark mode. The radio's custom layout hides the native indicator,
        # but the correct options are set defensively for any fallback layout.
        style.configure("Theme.TRadiobutton",background=self.colors["card"],foreground=self.colors["text"],
                        padding=(self.px(10),self.px(8)),indicatorbackground=self.colors["card"],
                        indicatorforeground=self.colors["accent"],focuscolor=self.colors["focus"])
        style.map("Theme.TRadiobutton",background=[("selected",self.colors["info_surface"]),
                                                    ("active",self.colors["hover"])],
                  foreground=[("selected",self.colors["accent"])],
                  indicatorbackground=[("selected",self.colors["info_surface"]),
                                       ("active",self.colors["hover"]),
                                       ("disabled",self.colors["trough"])],
                  indicatorforeground=[("selected",self.colors["accent"]),
                                       ("disabled",self.colors["muted"])])
        style.configure("Card.TCheckbutton",background=self.colors["card"],foreground=self.colors["text"],
                        padding=(self.px(4),self.px(4)),indicatorbackground=self.colors["card"],
                        indicatorforeground=self.colors["button_text"],focuscolor=self.colors["focus"])
        style.map("Card.TCheckbutton",background=[("active",self.colors["hover"])],
                  foreground=[("selected",self.colors["accent"])],
                  indicatorbackground=[("selected",self.colors["accent"]),
                                       ("active",self.colors["hover"]),
                                       ("disabled",self.colors["trough"])],
                  indicatorforeground=[("selected",self.colors["button_text"]),
                                       ("disabled",self.colors["muted"])])

    def _set_material(self, material: str, *, persist: bool = False) -> None:
        """Switch solid/acrylic; solid restores ClearType text immediately."""
        effective = resolve_material(material)
        self.material = effective
        if persist:
            self.storage.save_appearance(AppearancePreferences(self.theme_mode, material))
        history_window = self.history.window if self.history is not None else None
        for window in (self.popup, self.settings_window, history_window):
            if window is not None and window.winfo_exists():
                window._material = effective
                apply_window_effects(window, self.colors["surface"], material=effective)
        if self.desktop_widget is not None:
            self.desktop_widget.material = effective
            self.desktop_widget.set_theme(self.colors, self.theme.shadow_opacity)
        if self._settings_dialog is not None and self._settings_dialog.material_var is not None:
            self._settings_dialog.material_var.set(effective)

    def _set_theme(self, mode: str, *, persist: bool = False) -> None:
        preferences=AppearancePreferences(mode, self.material)
        if persist:
            self.storage.save_appearance(preferences)
            if self._settings_dialog is not None:
                self._settings_dialog.original_mode=mode
        old=self.colors
        selected=resolve_theme(mode)
        self.theme_mode=mode
        if self._settings_dialog is not None and self._settings_dialog.theme_var is not None:
            self._settings_dialog.theme_var.set(mode)
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
            if isinstance(widget,ttk.Combobox):
                # The option database only themes popdowns created afterwards;
                # an already-built popdown Listbox must be recolored directly.
                recolor_combobox_popdown(widget, combobox_palette(self.theme))
            if isinstance(widget,RoundedPanel):
                widget.set_theme(self.colors)
            elif isinstance(widget,RoundedProgress):
                widget.trough=self.colors["trough"]
                widget._key=None
                widget._redraw()
        visit(self.root)
        for tip in self._tooltips:
            tip.hide()
        history_window = self.history.window if self.history is not None else None
        for window in (self.popup,self.settings_window,history_window):
            if window is not None and window.winfo_exists():
                window._shadow_opacity=self.theme.shadow_opacity
                apply_window_effects(window,self.colors["surface"],material=self.material)
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
        # Re-resolve material when the system transparency switch flips, so an
        # active acrylic window falls back to solid (and back) without a restart.
        system_transparency = transparency_enabled()
        if system_transparency != self._system_transparency:
            self._system_transparency = system_transparency
            self._set_material(self.material)
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
            material=self.material,
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
                "用量历史",
                self._tray_command("history"),
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
                elif command == "history":
                    self.open_history()
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
        self._drain_model_catalog()
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
        install_window_effects(popup,self.colors["surface"],material=self.material)
        self.popup=popup
        popup._on_dpi_changed=self._on_dpi_changed
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
        self.quota_panel=RoundedPanel(parent,radius=RADIUS["panel"],fill=self.colors["card"],
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
            reset=ttk.Label(frame,text="重置时间暂无",style="DetailMuted.TLabel",
                            wraplength=round(320*dpi_scale(parent)))
            reset.grid(row=2,column=0,columnspan=2,sticky="w")
            details=ttk.Frame(frame,style="Card.TFrame")
            used=ttk.Label(details,text="",style="DetailMuted.TLabel")
            used.pack(anchor="w")
            absolute=ttk.Label(details,text="",style="DetailMuted.TLabel",
                               wraplength=round(320*dpi_scale(parent)),justify="left")
            absolute.pack(anchor="w",pady=(4,0))
            full_reset=ttk.Label(details,text="",style="DetailMuted.TLabel")
            full_reset.pack(anchor="w",pady=(4,0))
            trend=ttk.Label(details,text="",style="DetailMuted.TLabel",
                            wraplength=round(320*dpi_scale(parent)),justify="left")
            trend.pack(anchor="w",pady=(4,0))
            separator=tk.Frame(self.quota_panel.content,bg=self.colors["border"],height=1,
                               borderwidth=0,highlightthickness=0)
            self.rows[kind]={"frame":frame,"remaining":remaining,"used":used,"progress":progress,
                              "absolute":absolute,"reset":reset,"full_reset":full_reset,
                              "trend":trend,"details":details,"separator":separator}
        self.details_button=ttk.Button(parent,text="▸ 展开详情",style="Secondary.TButton",command=self._toggle_details)
        self.details_button.pack(anchor="w",pady=(SECTION_GAP,0))

    def _create_empty_panel(self, parent: ttk.Frame) -> None:
        panel = RoundedPanel(
            parent,
            radius=RADIUS["panel"],
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

    def _on_dpi_changed(self, dpi: int) -> None:
        """Rebuild scaled assets after moving across monitors.

        Tk 8.6 keeps one process-global scaling factor, so every open window
        re-lays-out together with the window that received WM_DPICHANGED.
        """
        if dpi == self._dpi:
            return
        self._dpi = dpi
        self.root.tk.call("tk", "scaling", dpi / 72.0)
        invalidate_scaled_caches()
        self._configure_style()

        def visit(widget: tk.Misc) -> None:
            for child in widget.winfo_children():
                visit(child)
            if isinstance(widget, RoundedPanel):
                widget.refresh_scale()
            elif isinstance(widget, RoundedProgress):
                widget._key = None
                widget._redraw()
            elif isinstance(widget, ScrollableBody):
                widget.refresh_scale()

        visit(self.root)
        history_window = self.history.window if self.history is not None else None
        for window in (self.popup, self.settings_window, history_window):
            if window is not None and window.winfo_exists():
                chrome = getattr(window, "_chrome", None)
                if chrome is not None:
                    chrome.refresh_scale()
                apply_window_effects(window, self.colors["surface"], material=self.material)
        if self.desktop_widget is not None:
            self.desktop_widget.on_scale_changed(dpi)
        self._render_state(self._state)

    def show_popup(self) -> None:
        if self.popup is None:
            return
        calibrate_tk_scaling(self.popup)
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
        if self.history is not None and self.history.window is not None:
            self.history.render(state)
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
        trends = {item.kind: item for item in state.trends}
        for kind, row in self.rows.items():
            window = windows.get(kind)
            if window is not None:
                self._render_window(row, window, unit, state.alert_threshold_percent,
                                    trends.get(kind))

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
            # Forget first so a changed window set leaves no stale separators,
            # then repack in canonical order with a hairline between rows.
            for kind in WINDOW_LABELS:
                row=self.rows[kind]
                row["frame"].pack_forget()
                row["separator"].pack_forget()
            visible=[kind for kind in WINDOW_LABELS if kind in visible_kinds]
            for index,kind in enumerate(visible):
                row=self.rows[kind]
                row["frame"].pack(fill="x")
                if index < len(visible)-1:
                    row["separator"].pack(fill="x",padx=self.px(8))
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

    def _notify_alert(self,event) -> None:
        """Quota alert channel: a pystray tray balloon.

        pystray 0.19.5's Windows backend only implements ``notify(message,
        title)`` with no click callback, so the balloon cannot be wired to
        open the details window -- a documented platform limitation. Users
        open the usage popup by clicking the tray icon itself instead.
        """
        if self.icon is None:
            return
        title,body=alert_notification(event,provider_label(event.provider))
        try:
            self.icon.notify(body,title)
        except Exception:
            logger.exception("Tray notification failed")

    def _render_window(self,row:dict[str,Any],window:QuotaWindow,unit:str="AFP",threshold:int=10,
                       trend:WindowTrend|None=None) -> None:
        warning=window.remaining_percent<threshold
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
        caption = trend_line(trend, window, unit)
        if caption:
            exhausting = (
                trend is not None
                and trend.exhausted_at is not None
                and trend.resets_before_exhaustion is False
            )
            row["trend"].configure(
                text=caption,
                style="DetailWarning.TLabel" if exhausting else "DetailMuted.TLabel",
            )
            row["trend"].pack(anchor="w",pady=(4,0))
        else:
            row["trend"].pack_forget()
        if self._details_expanded:
            row["details"].grid(row=3,column=0,columnspan=2,sticky="ew",pady=(10,0))
        else:
            row["details"].grid_remove()

    def open_settings(self) -> None:
        dialog = self._settings_dialog
        if dialog is not None and dialog._opening:
            return
        if (dialog is not None and dialog.window is not None
                and not dialog.window.winfo_exists()):
            dialog.dispose()
            dialog = None
        if dialog is not None and dialog.window is not None:
            if self.settings_window is dialog.window:
                # A second request during the deferred first present must not
                # focus half-laid-out content; a visible dialog only needs focus.
                if dialog._presented:
                    dialog.focus()
                return
            try:
                self.settings_window = dialog.open()
                self.settings_body = dialog.body
            except Exception:
                dialog.dispose()
                raise
            return
        dialog = SettingsDialog(self)
        self._settings_dialog = dialog
        try:
            self.settings_window = dialog.open()
            self.settings_body = dialog.body
        except Exception:
            dialog.dispose()
            raise

    def open_history(self) -> None:
        if self.history is not None and self.history.window is not None:
            self.history.focus()
            return
        window = HistoryWindow(self)
        self.history = window
        window.open(self._state)

    # ------------------------------------------------------- Claude Code hookup
    def claude_seed(self, provider: str, *, model_options=None) -> dict[str, str]:
        """Initial Claude section values for one provider: draft → live → prefs.

        Mirrors the macOS shell: the layering itself is the pure
        `resolve_claude_seed`; this only gathers the three sources. Falling
        back to the live file rather than to blank keeps a provider switch
        from wiping the base URL and model the user can see they had.
        """
        try:
            prefs = self.storage.load_claude_prefs(provider)
        except QueryError:
            prefs = ClaudeProviderPrefs()
        live_base_url = live_model = live_auto = live_subagent = ""
        try:
            document, _ = claude_code.read_document_at(self.resolve_claude_paths()[0])
        except (ClaudeConfigError, QueryError):
            document = None
        env = document.get("env") if isinstance(document, dict) else None
        if isinstance(env, dict):
            # Only non-secret fields are echoed; the token in the live file is
            # deliberately never surfaced into a text field.
            live_base_url = str(env.get(claude_code.ENV_BASE_URL) or "").strip()
            live_model = str(env.get(claude_code.ENV_MODEL) or "").strip()
            live_auto = str(env.get(claude_code.ENV_AUTO_MODE) or "").strip()
            live_subagent = str(env.get(claude_code.ENV_SUBAGENT) or "").strip()
        return resolve_claude_seed(
            get_provider(provider), self._claude_drafts.get(provider), prefs,
            live_base_url, live_model, live_auto,
            model_options=(
                model_options if model_options is not None
                else self.effective_claude_options(provider)
            ),
            live_subagent_model=live_subagent,
        )

    def effective_claude_options(self, provider: str):
        """Static catalogue merged with any cached live models for the provider."""
        spec = get_provider(provider)
        try:
            fetched = self.storage.load_model_catalog(provider)
        except QueryError:
            fetched = ()
        if fetched:
            return merge_model_options(spec.claude_models, fetched)
        return spec.claude_models

    def stored_inference_key(self, provider: str) -> str | None:
        """Best-known inference key for the "leave blank to keep" semantics."""
        return self._claude_key_drafts.get(provider) or self.inference_key

    def stash_claude_draft(self, provider: str) -> None:
        """Remember the open form's Claude values before a rebuild or save.

        Non-secret fields go to claude.json as well as the session draft; the
        inference key stays in memory only, for as long as this process runs.
        """
        dialog = self._settings_dialog
        if dialog is None or dialog.claude_panel is None:
            return
        _popup, _custom, _base, key_text, prefs = dialog.claude_form_values()
        self._claude_drafts[provider] = prefs
        if key_text.strip():
            self._claude_key_drafts[provider] = key_text.strip()
        try:
            self.storage.save_claude_prefs(prefs, provider)
        except QueryError:
            pass

    def resolve_claude_paths(self) -> tuple[Path, Path]:
        """Resolve both write targets from the real environment at call time.

        `claude_paths_override` lets the offline smoke point the whole write
        at a temp directory instead of a real `~/.claude`.
        """
        if self.claude_paths_override is not None:
            return self.claude_paths_override
        environ, home = dict(os.environ), Path.home()
        return (claude_code.resolve_settings_path(environ, home),
                claude_code.resolve_onboarding_path(environ, home))

    def confirm_claude_write(self, lines: list[str]) -> bool:
        """The write confirmation, with an injectable seam for the smoke."""
        if self._confirm_override is not None:
            if callable(self._confirm_override):
                return bool(self._confirm_override(lines))
            return bool(self._confirm_override)
        parent = self.settings_window if self.settings_window is not None else self.root
        return messagebox.askyesno(
            "写入 Claude Code 配置？", "\n".join(lines), parent=parent
        )

    # --------------------------------------------------------- model catalogue
    def catalog_status_for(self, provider: str) -> tuple[str, str]:
        if provider in self._catalog_status:
            return self._catalog_status[provider]
        try:
            fetched = self.storage.load_model_catalog(provider)
        except QueryError:
            fetched = ()
        if fetched:
            return (f"已缓存 {len(fetched)} 个在线模型", "neutral")
        return ("使用内置模型清单，可在线刷新", "neutral")

    def set_catalog_status(self, provider: str, text: str, tone: str) -> None:
        self._catalog_status[provider] = (text, tone)
        dialog = self._settings_dialog
        if (dialog is not None and dialog.window is not None
                and dialog.provider == provider and dialog.claude_panel is not None):
            dialog.claude_panel.set_status(text, tone)

    def catalog_fetch_target(self, provider: str) -> tuple[str, str]:
        """Resolve (base_url, key) from the live form, then stored defaults."""
        spec = get_provider(provider)
        dialog = self._settings_dialog
        base_url = key = ""
        on_this_provider = dialog is not None and dialog.provider == provider
        if on_this_provider and dialog.claude_panel is not None:
            base_url = dialog.claude_panel.entries["base_url"].get().strip()
            key = dialog.claude_panel.entries["api_key"].get().strip()
        if not base_url:
            region = ""
            if on_this_provider:
                region = dialog.entries["region"].get().strip()
            base_url = spec.default_claude_base_url(region or spec.default_region)
        if not key:
            key = self.stored_inference_key(provider) or ""
        return base_url, key

    def maybe_auto_fetch(self, provider: str) -> None:
        """Auto-pull on open / provider switch.

        Deliberately bypasses signature de-dup: every fresh presentation of
        the settings dialog gets a chance to refresh the catalogue, while
        field-driven debounce calls de-dup via :meth:`try_auto_fetch_models`.
        """
        self.try_auto_fetch_models(provider, skip_duplicate=False)

    def try_auto_fetch_models(self, provider: str, *, skip_duplicate: bool = True) -> None:
        """Fetch when the form is ready; otherwise explain the blockage.

        Used by the open/switch path, the debounced KeyRelease/FocusOut
        bindings and the post-save/post-write paths. A blocked precondition
        always lands on the card status label instead of silently leaving the
        static catalogue in place.
        """
        if provider in self._catalog_inflight or self._applying_claude:
            return
        base_url, key = self.catalog_fetch_target(provider)
        reason = catalog_block_reason(base_url, key)
        if reason is not None:
            self.set_catalog_status(provider, reason, "warning")
            return
        signature = (base_url, key)
        if skip_duplicate and self._catalog_auto_signatures.get(provider) == signature:
            return
        self._catalog_auto_signatures[provider] = signature
        self._start_model_fetch(provider, base_url, key)

    def refresh_models(self, provider: str) -> None:
        """Manual refresh button: force a re-pull with the current form values."""
        if provider in self._catalog_inflight:
            return
        base_url, key = self.catalog_fetch_target(provider)
        if not key:
            self.set_catalog_status(provider, "请先填写推理 API Key", "warning")
            return
        if claude_code.ANTHROPIC_PATH not in base_url:
            self.set_catalog_status(provider, CATALOG_UNSUPPORTED_URL, "warning")
            return
        self._catalog_auto_signatures[provider] = (base_url, key)
        self._start_model_fetch(provider, base_url, key)

    def _start_model_fetch(self, provider: str, base_url: str, key: str) -> None:
        self._catalog_inflight.add(provider)
        self.set_catalog_status(provider, "正在在线获取模型列表…", "neutral")
        threading.Thread(
            target=self._fetch_models_worker,
            args=(provider, base_url, key),
            name=f"model-catalog-{provider}",
            daemon=True,
        ).start()

    def _fetch_models_worker(self, provider: str, base_url: str, key: str) -> None:
        try:
            ids = self._fetch_model_ids(base_url, key)
            self._catalog_events.put((provider, ids, None))
        except QueryError as exc:
            self._catalog_events.put((provider, None, exc.message))
        except Exception:
            logger.exception("Unexpected model catalogue fetch failure")
            self._catalog_events.put((provider, None, "模型列表获取失败"))

    def _drain_model_catalog(self) -> None:
        """Apply background fetch results on the Tk thread; never block on it."""
        while True:
            try:
                provider, ids, error = self._catalog_events.get_nowait()
            except queue.Empty:
                return
            self._catalog_inflight.discard(provider)
            if error is not None:
                self.set_catalog_status(
                    provider, f"在线获取失败：{error}，已用内置清单", "warning"
                )
                continue
            try:
                self.storage.save_model_catalog(provider, ids)
            except QueryError:
                pass
            self.set_catalog_status(
                provider, f"已在线更新模型列表（{len(ids)} 个）", "ok"
            )
            dialog = self._settings_dialog
            if (dialog is not None and dialog.window is not None
                    and dialog.provider == provider
                    and not self._applying_claude
                    and dialog.claude_panel is not None):
                options = merge_model_options(get_provider(provider).claude_models, ids)
                dialog.claude_panel.apply_catalog_options(options)

    def _close_settings(self,window:tk.Toplevel|None=None,*,commit:bool=False) -> None:
        # App-level entry point kept for existing callers; SettingsDialog owns
        # the teardown, so the window argument is intentionally unused.
        if self._settings_dialog is not None:
            self._settings_dialog.close(commit=commit)

    # Read-only shims for smoke scripts written before the dialog extraction.
    @property
    def _settings_entries(self) -> dict[str,ttk.Entry]:
        return self._settings_dialog.entries if self._settings_dialog is not None else {}

    @property
    def _settings_errors(self) -> dict[str,ttk.Label]:
        return self._settings_dialog.errors if self._settings_dialog is not None else {}

    @property
    def _settings_alert_reset(self) -> tk.BooleanVar|None:
        return self._settings_dialog.alert_reset if self._settings_dialog is not None else None

    @property
    def _settings_alert_enabled(self) -> tk.BooleanVar|None:
        return self._settings_dialog.alert_enabled if self._settings_dialog is not None else None

    @property
    def _settings_theme_var(self) -> tk.StringVar|None:
        return self._settings_dialog.theme_var if self._settings_dialog is not None else None

    def shutdown(self) -> None:
        if self._closing:
            return
        self._closing = True
        # Never leave a drafted inference key reachable past this process.
        self._claude_key_drafts.clear()
        self._claude_drafts.clear()
        if self._theme_poll_after is not None:
            self.root.after_cancel(self._theme_poll_after)
            self._theme_poll_after=None
        if self._settings_dialog is not None:
            self._settings_dialog.dispose()
        history_window = self.history.window if self.history is not None else None
        for window in (self.popup,self.settings_window,history_window):
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
