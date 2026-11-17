"""Settings dialog for the Windows Tk shell.

The dialog owns every piece of form state (entries, validation labels, Tk
variables) and the save/cancel lifecycle. Account storage, theme control and
monitor updates stay on the owning :class:`MonitorUI`, passed in as ``app``;
this keeps the dialog a view layer with no background threads of its own.

Provider switches follow the macOS shell's rules: the combobox only raises a
dirty flag and the actual form rebuild runs on a later ``after`` tick, so a
widget is never destroyed while its own event is still being dispatched.
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
    PROVIDER_REGIONS,
    ConfigurationError,
    Credentials,
    QueryError,
    Settings,
)
from .providers import PROVIDERS, get_provider
from .settings_rules import FieldValidationError, validate_configuration
from .ui_graphics import (
    RoundedPanel,
    ScrollableBody,
    dpi_scale,
    install_window_effects,
    window_work_area,
)
from .window_chrome import WindowChrome
from .window_geometry import WindowGeometry

#: Field ids raised by the shared validator map to the dialog's entry keys.
_FIELD_TO_ENTRY = {
    "ak": "access_key",
    "sk": "secret_key",
    "interval": "refresh",
    "region": "region",
    "alert_threshold": "alert_threshold",
}


class SettingsDialog:
    """Modal ``Toplevel`` carrying the provider, credentials and preferences form."""

    def __init__(self, app):
        self.app = app
        self.window: tk.Toplevel | None = None
        self.body: ScrollableBody | None = None
        self.form_frame: ttk.Frame | None = None
        self.entries: dict[str, ttk.Entry] = {}
        self.errors: dict[str, ttk.Label] = {}
        self.theme_var: tk.StringVar | None = None
        self.provider_var: tk.StringVar | None = None
        self.alert_enabled: tk.BooleanVar | None = None
        self.alert_reset: tk.BooleanVar | None = None
        self.original_mode: str | None = None
        self.provider: str = ""
        self.settings: Settings | None = None
        self.existing: Credentials | None = None
        self._general_error: ttk.Label | None = None
        self._footer: ttk.Frame | None = None
        self._rebuild_pending = False
        self._rebuild_after: str | None = None
        self._label_to_id = {spec.label: spec.id for spec in PROVIDERS}

    def open(self) -> tk.Toplevel:
        app = self.app
        self.settings = app.storage.load_settings()
        self.original_mode = app.theme_mode
        window = tk.Toplevel(app.root)
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
        # All rebuildable panels live in form_frame; the error label and footer
        # stay outside it so a provider switch never tears them down.
        self.form_frame = ttk.Frame(body.content, style="Card.TFrame")
        self.form_frame.pack(fill="x")
        general_error = ttk.Label(
            body.content, text="", style="Error.TLabel",
            wraplength=round(360 * dpi_scale(window)),
        )
        general_error.pack(anchor="w")
        self._general_error = general_error
        footer = ttk.Frame(
            window,
            padding=(app.px(OUTER_PADDING), 0, app.px(OUTER_PADDING), app.px(12)),
        )
        footer.pack(fill="x")
        self._footer = footer
        ttk.Button(footer, text="保存设置", style="Primary.TButton",
                   command=self._save).pack(side="right")
        ttk.Button(footer, text="取消", style="Secondary.TButton",
                   command=lambda: self.close()).pack(side="right", padx=(0, 8))
        window.bind("<Return>", lambda event: self._save())
        window.bind("<Escape>", lambda event: self.close())

        self.theme_var = tk.StringVar(window, app.theme_mode)
        self.provider_var = tk.StringVar(window)
        stored = self.settings
        try:
            existing = app.storage.load_credentials(stored.provider)
        except ConfigurationError:
            existing = None
        self._build_form(
            stored.provider, stored.region, str(stored.refresh_minutes),
            stored.alert.enabled, str(stored.alert.threshold_percent),
            stored.alert.notify_on_reset, existing,
        )
        window.after_idle(self._fit)
        return window

    # ------------------------------------------------------------------ form
    def _build_form(self, provider: str, region: str, interval: str,
                    alert_enabled: bool, alert_threshold: str, alert_reset: bool,
                    existing: Credentials | None) -> None:
        app = self.app
        spec = get_provider(provider)
        self.provider = provider
        self.existing = existing
        self.entries = {}
        self.errors = {}
        self.provider_var.set(spec.label)
        if self._general_error is not None:
            self._general_error.configure(text="")
        self.window.title(f"{spec.label}用量查询设置")
        for child in self.form_frame.winfo_children():
            child.destroy()

        def panel(title: str) -> tk.Frame:
            outer = RoundedPanel(
                self.form_frame, radius=14, fill=app.colors["card"],
                outline=app.colors["border"], background=app.colors["surface"],
                padding=(12, 10),
            )
            outer.pack(fill="x", pady=(0, SECTION_GAP))
            frame = outer.content
            frame.columnconfigure(0, weight=1)
            ttk.Label(frame, text=title, style="Section.TLabel").grid(
                row=0, column=0, columnspan=2, sticky="w", pady=(0, 10)
            )
            return frame

        provider_panel = panel("服务商")
        combo = ttk.Combobox(
            provider_panel, textvariable=self.provider_var, state="readonly",
            values=[option.label for option in PROVIDERS],
        )
        combo.grid(row=1, column=0, columnspan=2, sticky="ew")
        combo.bind("<<ComboboxSelected>>", lambda event: self._schedule_rebuild())

        appearance = panel("外观")
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
        note = spec.credential_note.format(store=app.storage.credential_store_name)
        ttk.Label(
            credentials_panel, text=note, style="CardMuted.TLabel",
            wraplength=round(340 * dpi_scale(self.window)),
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
                wraplength=round(315 * dpi_scale(self.window)),
            )
            self.entries[key] = field
            self.errors[key] = error
            return field

        entry(credentials_panel, "access_key", spec.access_key_label,
              existing.access_key_id if existing else "", 2)
        entry(credentials_panel, "secret_key",
              f"{spec.secret_key_label} · 留空保持不变", "", 5, True)

        preferences = panel("查询偏好")
        preferences.columnconfigure(0, weight=1)
        preferences.columnconfigure(1, weight=1)
        entry(preferences, "region", "Region", region, 1)
        entry(preferences, "refresh", "刷新间隔 · 分钟", interval, 1, column=1)
        # Equal columns keep the settings width stable across long values.
        self.entries["region"].configure(width=16)
        self.entries["refresh"].configure(width=12)

        alerts = panel("低额度告警")
        self.alert_enabled = tk.BooleanVar(self.window, alert_enabled)
        ttk.Checkbutton(
            alerts, text="启用系统通知（托盘提示）", variable=self.alert_enabled,
            style="Card.TCheckbutton",
        ).grid(row=1, column=0, columnspan=2, sticky="w")
        entry(alerts, "alert_threshold", "低额度阈值 · %（1–99）", alert_threshold, 2)
        self.alert_reset = tk.BooleanVar(self.window, alert_reset)
        ttk.Checkbutton(
            alerts, text="窗口重置时提醒", variable=self.alert_reset,
            style="Card.TCheckbutton",
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(6, 0))

        self.window.after_idle(
            self.entries["secret_key" if existing is not None else "access_key"].focus_set
        )

    # ------------------------------------------------------ provider switching
    def _schedule_rebuild(self) -> None:
        """Record the selection; rebuild on the next tick, never inline."""
        self._rebuild_pending = True
        if self._rebuild_after is None and self.window is not None:
            self._rebuild_after = self.window.after(20, self._flush_rebuild)

    def _flush_rebuild(self) -> None:
        self._rebuild_after = None
        if not self._rebuild_pending or self.window is None:
            return
        self._rebuild_pending = False
        provider = self._label_to_id.get(self.provider_var.get())
        if provider is None or provider == self.provider:
            return
        spec = get_provider(provider)
        # Carry typed values across, except a region this provider does not
        # serve: fall back to its default exactly like the macOS shell.
        region = self.entries["region"].get().strip()
        allowed = PROVIDER_REGIONS.get(provider)
        if allowed is not None and region not in allowed:
            region = spec.default_region
        interval = self.entries["refresh"].get().strip() or "15"
        # Alert choices are provider-independent: keep the typed values.
        alert_threshold = self.entries["alert_threshold"].get().strip() or "10"
        # Credentials are per provider: re-read this provider's own vault pair
        # and never carry the previous provider's secret across.
        try:
            existing = self.app.storage.load_credentials(provider)
        except ConfigurationError:
            existing = None
        self._build_form(
            provider, region, interval, self.alert_enabled.get(),
            alert_threshold, self.alert_reset.get(), existing,
        )

    # ----------------------------------------------------------------- helpers
    def focus(self) -> None:
        if self.window is not None and self.window.winfo_exists():
            self.window.deiconify()
            self.window.lift()
            self.window.focus_force()

    def _fit(self) -> None:
        window = self.window
        if window is None or not window.winfo_exists() or window._geometry.initialized:
            return
        window.update_idletasks()
        content = self.body.content
        width = max(round(460 * dpi_scale(window)), content.winfo_reqwidth())
        left, top, right, bottom = window_work_area(window)
        width = min(width, right - left - 40)
        fixed = (
            self._footer.winfo_reqheight()
            + window._chrome.header.winfo_reqheight()
            + self.app.px(8)
        )
        height = self.body.fit(max(200, bottom - top - 70 - fixed))
        window._geometry.fit((width, height + fixed))

    def _show_validation_error(self, message: str, field: str | None = None) -> None:
        if field is None:
            self._general_error.configure(text=message)
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
        self._fit()
        if field is not None:
            self.window.update_idletasks()
            target = self.entries[field].winfo_rooty() - self.body.content.winfo_rooty()
            self.body.canvas.yview_moveto(
                max(0, (target - 80) / max(1, self.body.content.winfo_height()))
            )

    def _save(self) -> None:
        app = self.app
        for key, field in self.entries.items():
            field.state(["!invalid"])
            self.errors[key].grid_remove()
        self._general_error.configure(text="")
        try:
            new_settings, new_credentials = validate_configuration(
                self.provider,
                self.entries["access_key"].get(),
                self.entries["secret_key"].get(),
                self.entries["region"].get(),
                self.entries["refresh"].get(),
                self.existing,
                alert_enabled=self.alert_enabled.get(),
                alert_threshold=self.entries["alert_threshold"].get(),
                alert_reset=self.alert_reset.get(),
            )
        except FieldValidationError as exc:
            self._show_validation_error(
                exc.message, _FIELD_TO_ENTRY.get(exc.field, exc.field)
            )
            return
        except QueryError as exc:
            # ConfigurationError (vault failures, bad credentials) is a subclass.
            self._show_validation_error(exc.message)
            return
        try:
            if new_credentials != self.existing:
                app.storage.save_credentials(new_credentials, self.provider)
            if new_settings != self.settings:
                app.storage.save_settings(new_settings)
            app.storage.save_appearance(AppearancePreferences(app.theme_mode))
        except QueryError as exc:
            self._show_validation_error(exc.message)
            return
        if new_settings != self.settings or new_credentials != self.existing:
            app.monitor.update_configuration(new_settings, new_credentials)
        self.close(commit=True)
        app.show_popup()

    def close(self, *, commit: bool = False) -> None:
        original = self.original_mode
        self.original_mode = None
        app = self.app
        app.settings_window = None
        app.settings_body = None
        app._settings_dialog = None
        window = self.window
        if window is not None and window.winfo_exists():
            if self._rebuild_after is not None:
                window.after_cancel(self._rebuild_after)
            window._geometry.flush()
            window.destroy()
        self.window = None
        self.body = None
        self._rebuild_after = None
        self._rebuild_pending = False
        if not commit and original is not None:
            app._set_theme(original)
        if app.popup is not None and app.popup.winfo_viewable():
            app.popup.focus_set()
