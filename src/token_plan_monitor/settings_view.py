"""Settings dialog for the Windows Tk shell.

The dialog owns every piece of form state (entries, validation labels, Tk
variables) and the save/cancel lifecycle. Account storage, theme control and
monitor updates stay on the owning :class:`MonitorUI`, passed in as ``app``;
this keeps the dialog a view layer with no background threads of its own.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .appearance import (
    OUTER_PADDING,
    SECTION_GAP,
    THEME_LABELS,
    AppearancePreferences,
)
from .models import (
    AlertSettings,
    ConfigurationError,
    Credentials,
    QueryError,
    Settings,
)
from .ui_graphics import (
    RoundedPanel,
    ScrollableBody,
    dpi_scale,
    install_window_effects,
    window_work_area,
)
from .window_chrome import WindowChrome
from .window_geometry import WindowGeometry


class SettingsDialog:
    """Modal ``Toplevel`` carrying the provider credentials and preferences form."""

    def __init__(self, app):
        self.app = app
        self.window: tk.Toplevel | None = None
        self.body: ScrollableBody | None = None
        self.entries: dict[str, ttk.Entry] = {}
        self.errors: dict[str, ttk.Label] = {}
        self.theme_var: tk.StringVar | None = None
        self.alert_enabled: tk.BooleanVar | None = None
        self.alert_reset: tk.BooleanVar | None = None
        self.original_mode: str | None = None
        self.settings: Settings | None = None
        self.existing: Credentials | None = None
        self._general_error: ttk.Label | None = None

    def open(self) -> tk.Toplevel:
        app = self.app
        settings = app.storage.load_settings()
        try:
            existing = app.storage.load_credentials()
        except ConfigurationError:
            existing = None
        self.settings = settings
        self.existing = existing
        self.original_mode = app.theme_mode
        window = tk.Toplevel(app.root)
        window.title("火山方舟用量查询设置")
        window.configure(background=app.colors["surface"])
        window.resizable(True, True)
        app._apply_icon(window)
        install_window_effects(window, app.colors["surface"])
        self.window = window
        window._geometry = WindowGeometry(window, app.storage, "settings", (400, 260))
        window._chrome = WindowChrome(
            window, "设置", app.colors, app.font_family, lambda: self.close()
        )
        window.protocol("WM_DELETE_WINDOW", lambda: self.close())
        body = ScrollableBody(window, app.colors)
        body.pack(fill="both", expand=True)
        self.body = body
        content = body.content

        def panel(title: str) -> tk.Frame:
            outer = RoundedPanel(
                content, radius=14, fill=app.colors["card"], outline=app.colors["border"],
                background=app.colors["surface"], padding=(12, 10),
            )
            outer.pack(fill="x", pady=(0, SECTION_GAP))
            frame = outer.content
            frame.columnconfigure(0, weight=1)
            ttk.Label(frame, text=title, style="Section.TLabel").grid(
                row=0, column=0, columnspan=2, sticky="w", pady=(0, 10)
            )
            return frame

        appearance = panel("外观")
        self.theme_var = tk.StringVar(window, app.theme_mode)
        selector = ttk.Frame(appearance, style="Card.TFrame")
        selector.grid(row=1, column=0, columnspan=2, sticky="ew")
        for column, (mode, label) in enumerate(THEME_LABELS.items()):
            selector.columnconfigure(column, weight=1)
            ttk.Radiobutton(
                selector, text=label, value=mode, variable=self.theme_var,
                style="Theme.TRadiobutton",
                command=lambda: app._set_theme(self.theme_var.get()),
            ).grid(row=0, column=column, sticky="ew", padx=2)
        ttk.Label(
            appearance, text="即时预览，保存后记住选择。", style="CardMuted.TLabel"
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))

        credentials_panel = panel("账号凭据")
        ttk.Label(
            credentials_panel, text="AK/SK 仅保存到 Windows 凭据管理器。",
            style="CardMuted.TLabel", wraplength=round(340 * dpi_scale(window)),
        ).grid(row=1, column=0, sticky="w", pady=(0, 8))

        def entry(frame: tk.Frame, key: str, label: str, value: str, row: int,
                  secret: bool = False, column: int = 0) -> ttk.Entry:
            ttk.Label(frame, text=label, style="Card.TLabel").grid(
                row=row, column=column, sticky="w", pady=(6, 5)
            )
            field = ttk.Entry(frame, width=28, show="•" if secret else "")
            field.insert(0, value)
            field.grid(
                row=row + 1, column=column, sticky="ew",
                padx=(0, 8) if column == 0 and key == "region" else 0,
            )
            error = ttk.Label(
                frame, text="", style="FieldError.TLabel",
                wraplength=round(315 * dpi_scale(window)),
            )
            self.entries[key] = field
            self.errors[key] = error
            return field

        entry(credentials_panel, "access_key", "AccessKey ID",
              existing.access_key_id if existing else "", 2)
        entry(credentials_panel, "secret_key", "Secret AccessKey · 留空保持不变", "", 5, True)

        preferences = panel("查询偏好")
        preferences.columnconfigure(0, weight=1)
        preferences.columnconfigure(1, weight=1)
        entry(preferences, "region", "Region", settings.region, 1)
        entry(preferences, "refresh", "刷新间隔 · 分钟",
              str(settings.refresh_minutes), 1, column=1)
        # Equal columns keep the settings width stable across long values.
        self.entries["region"].configure(width=16)
        self.entries["refresh"].configure(width=12)

        alerts = panel("低额度告警")
        self.alert_enabled = tk.BooleanVar(window, settings.alert.enabled)
        ttk.Checkbutton(
            alerts, text="启用系统通知（托盘提示）", variable=self.alert_enabled,
            style="Card.TCheckbutton",
        ).grid(row=1, column=0, columnspan=2, sticky="w")
        entry(alerts, "alert_threshold", "低额度阈值 · %（1–99）",
              str(settings.alert.threshold_percent), 2)
        self.alert_reset = tk.BooleanVar(window, settings.alert.notify_on_reset)
        ttk.Checkbutton(
            alerts, text="窗口重置时提醒", variable=self.alert_reset,
            style="Card.TCheckbutton",
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(6, 0))

        footer = ttk.Frame(
            window,
            padding=(app.px(OUTER_PADDING), 0, app.px(OUTER_PADDING), app.px(12)),
        )
        footer.pack(fill="x")
        general_error = ttk.Label(
            content, text="", style="Error.TLabel",
            wraplength=round(360 * dpi_scale(window)),
        )
        general_error.pack(anchor="w")
        self._general_error = general_error

        def fit() -> None:
            if not window.winfo_exists():
                return
            if window._geometry.initialized:
                return
            window.update_idletasks()
            width = max(round(460 * dpi_scale(window)), content.winfo_reqwidth())
            left, top, right, bottom = window_work_area(window)
            width = min(width, right - left - 40)
            fixed = (
                footer.winfo_reqheight()
                + window._chrome.header.winfo_reqheight()
                + app.px(8)
            )
            height = body.fit(max(200, bottom - top - 70 - fixed))
            window._geometry.fit((width, height + fixed))

        def show_validation_error(message: str, field: str | None = None) -> None:
            if field is None:
                general_error.configure(text=message)
            else:
                widget = self.entries[field]
                widget.state(["invalid"])
                label = self.errors[field]
                label.configure(text=message)
                info = widget.grid_info()
                label.grid(
                    row=int(info["row"]) + 1, column=int(info["column"]),
                    sticky="w", pady=(4, 0),
                )
                widget.focus_set()
                widget.selection_range(0, "end")
            fit()
            if field:
                window.update_idletasks()
                target = self.entries[field].winfo_rooty() - content.winfo_rooty()
                body.canvas.yview_moveto(
                    max(0, (target - 80) / max(1, content.winfo_height()))
                )

        def save() -> None:
            for key, field in self.entries.items():
                field.state(["!invalid"])
                self.errors[key].grid_remove()
            general_error.configure(text="")
            try:
                threshold = int(self.entries["alert_threshold"].get().strip())
            except ValueError:
                show_validation_error("阈值必须是 1–99 的整数", "alert_threshold")
                return
            if not 1 <= threshold <= 99:
                show_validation_error("阈值必须在 1–99 之间", "alert_threshold")
                return
            try:
                new_settings = Settings(
                    provider=settings.provider,
                    region=self.entries["region"].get(),
                    refresh_minutes=int(self.entries["refresh"].get().strip()),
                    alert=AlertSettings(
                        enabled=self.alert_enabled.get(),
                        threshold_percent=threshold,
                        notify_on_reset=self.alert_reset.get(),
                    ),
                )
            except (ValueError, QueryError) as exc:
                message = exc.message if isinstance(exc, QueryError) else "刷新间隔必须是整数"
                show_validation_error(
                    message,
                    "region" if isinstance(exc, QueryError) and message.startswith("Region")
                    else "refresh",
                )
                return
            ak = self.entries["access_key"].get().strip()
            sk = self.entries["secret_key"].get().strip()
            if not sk and existing is not None:
                sk = existing.secret_access_key
            try:
                new_credentials = Credentials(ak, sk)
            except QueryError as exc:
                show_validation_error(
                    exc.message, "access_key" if not ak else "secret_key"
                )
                return
            try:
                if new_credentials != existing:
                    app.storage.save_credentials(new_credentials)
                if new_settings != settings:
                    app.storage.save_settings(new_settings)
                app.storage.save_appearance(AppearancePreferences(app.theme_mode))
            except QueryError as exc:
                show_validation_error(exc.message)
                return
            if new_settings != settings or new_credentials != existing:
                app.monitor.update_configuration(new_settings, new_credentials)
            self.close(commit=True)
            app.show_popup()

        ttk.Button(footer, text="保存设置", style="Primary.TButton",
                   command=save).pack(side="right")
        ttk.Button(footer, text="取消", style="Secondary.TButton",
                   command=lambda: self.close()).pack(side="right", padx=(0, 8))
        window.bind("<Return>", lambda event: save())
        window.bind("<Escape>", lambda event: self.close())
        window.after_idle(fit)
        window.after_idle(
            self.entries["secret_key" if existing is not None else "access_key"].focus_set
        )
        return window

    def focus(self) -> None:
        if self.window is not None and self.window.winfo_exists():
            self.window.deiconify()
            self.window.lift()
            self.window.focus_force()

    def close(self, *, commit: bool = False) -> None:
        original = self.original_mode
        self.original_mode = None
        app = self.app
        app.settings_window = None
        app.settings_body = None
        app._settings_dialog = None
        window = self.window
        if window is not None and window.winfo_exists():
            window._geometry.flush()
            window.destroy()
        self.window = None
        self.body = None
        if not commit and original is not None:
            app._set_theme(original)
        if app.popup is not None and app.popup.winfo_viewable():
            app.popup.focus_set()
