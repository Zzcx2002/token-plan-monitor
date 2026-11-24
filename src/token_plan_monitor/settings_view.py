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

import logging
import tkinter as tk
from tkinter import ttk

from . import claude_code
from .appearance import (
    MATERIAL_LABELS,
    OUTER_PADDING,
    RADIUS,
    SECTION_GAP,
    THEME_LABELS,
    AppearancePreferences,
)
from .claude_code import ClaudeConfigError
from .models import (
    PROVIDER_REGIONS,
    ClaudeProviderPrefs,
    ConfigurationError,
    Credentials,
    QueryError,
    Settings,
)
from .providers import PROVIDERS, get_provider
from .settings_rules import (
    FieldValidationError,
    validate_claude_configuration,
    validate_configuration,
)
from .ui_graphics import (
    RoundedPanel,
    ScrollableBody,
    dpi_scale,
    install_window_effects,
    tcl_events_deferred,
    is_windows_11,
    window_work_area,
)
from .window_chrome import WindowChrome
from .window_geometry import WindowGeometry

logger = logging.getLogger(__name__)

#: Field ids raised by the shared validator map to the dialog's entry keys.
_FIELD_TO_ENTRY = {
    "ak": "access_key",
    "sk": "secret_key",
    "interval": "refresh",
    "region": "region",
    "alert_threshold": "alert_threshold",
    "model": "claude_model",
    "auto_model": "claude_auto_model",
    "subagent_model": "claude_subagent_model",
    "base_url": "base_url",
    "api_key": "api_key",
}

#: Combobox sentinel rows; the free-form Entry next to each combobox wins when
#: its row is selected.
CUSTOM_MARKER = "自定义模型 ID…"
AUTO_UNSET = "不设置（保持现状）"
SUBAGENT_DEFAULT = "默认（保持现有值；为空时填入主模型）"

#: The Claude panel's full-width hint labels need ~480 logical pixels and the
#: whole form requests ~560; a smaller window would clip them, so the geometry
#: floor matches the form's natural width instead of an arbitrary 400px.
SETTINGS_MINIMUM_SIZE = (560, 260)

#: Status-label tones map to card-scoped ttk styles so they sit on the panel.
_STATUS_STYLE = {
    "neutral": "CardMuted.TLabel",
    "warning": "CardWarning.TLabel",
    "ok": "CardSuccess.TLabel",
    "error": "CardError.TLabel",
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
        self.material_var: tk.StringVar | None = None
        self.provider_var: tk.StringVar | None = None
        self.alert_enabled: tk.BooleanVar | None = None
        self.alert_reset: tk.BooleanVar | None = None
        self.original_mode: str | None = None
        self.original_material: str | None = None
        self.provider: str = ""
        self.settings: Settings | None = None
        self.existing: Credentials | None = None
        self._general_error: ttk.Label | None = None
        self._footer: ttk.Frame | None = None
        self._rebuild_pending = False
        self._rebuild_after: str | None = None
        self._presented = False
        self._closing = False
        self._fit_after: str | None = None
        self._present_after: str | None = None
        self._anchor_after: str | None = None
        self._opening = False
        self._shell_scale = 0.0
        self._label_to_id = {spec.label: spec.id for spec in PROVIDERS}
        self.claude_panel: ClaudePanel | None = None

    def open(self) -> tk.Toplevel:
        """Create once, then reset and reveal the retained dialog on later opens."""
        app = self.app
        self._opening = True
        try:
            self._closing = False
            self._presented = False
            self.original_mode = app.theme_mode
            self.original_material = app.material
            stored = app.storage.load_settings()
            self.settings = stored
            if self.window is None or not self.window.winfo_exists():
                self.provider = ""
                self.entries = {}
                self.errors = {}
                self.claude_panel = None
                self._create_shell()
            else:
                self._sync_window_to_app()
            window = self.window
            try:
                existing = app.storage.load_credentials(stored.provider)
            except ConfigurationError:
                existing = None
            options = app.effective_claude_options(stored.provider)
            if self.provider != stored.provider or not self.entries:
                self._build_form(
                    stored.provider, stored.region, str(stored.refresh_minutes),
                    stored.alert.enabled, str(stored.alert.threshold_percent),
                    stored.alert.notify_on_reset, existing, focus_entry=False,
                    model_options=options,
                )
            else:
                self._reset_form(stored, existing, options)
            self.theme_var.set(app.theme_mode)
            self.material_var.set(app.material)
            if window._geometry.initialized:
                self.body.canvas.yview_moveto(0)
                self._present_after = window.after_idle(self._present)
            else:
                self._fit_after = window.after_idle(self._fit)
            return window
        finally:
            self._opening = False

    def _create_shell(self) -> None:
        app = self.app
        window = tk.Toplevel(app.root)
        self.window = window
        # Native material/chrome calls can pump Map and focus events. Keep the
        # empty Toplevel hidden until the form and its first geometry are done.
        window.withdraw()
        window.configure(background=app.colors["surface"])
        window.resizable(True, True)
        app._apply_icon(window)
        install_window_effects(window, app.colors["surface"], material=app.material)
        window._on_dpi_changed = app._on_dpi_changed
        window._geometry = WindowGeometry(
            window, app.storage, "settings", SETTINGS_MINIMUM_SIZE,
            cap_height=True)
        window._chrome = WindowChrome(
            window, "设置", app.colors, app.font_family, lambda: self.close()
        )
        window.protocol("WM_DELETE_WINDOW", lambda: self.close())
        body = ScrollableBody(window, app.colors)
        body.pack(fill="both", expand=True)
        self.body = body
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
        self.material_var = tk.StringVar(window, app.material)
        self.provider_var = tk.StringVar(window)
        self._shell_scale = dpi_scale(window)

    def _sync_window_to_app(self) -> None:
        """Hidden windows are absent from app.settings_window theme/DPI loops."""
        app = self.app
        window = self.window
        window.configure(background=app.colors["surface"])
        window._material = app.material
        window._material_surface = app.colors["surface"]
        window._shadow_opacity = app.theme.shadow_opacity
        window._chrome.set_theme(app.colors)
        scale = dpi_scale(window)
        if scale != self._shell_scale:
            window._chrome.refresh_scale()
            self._shell_scale = scale

    def _reset_form(self, stored: Settings, existing: Credentials | None,
                    options) -> None:
        """Refresh a same-provider form without destroying its rendered panels."""
        app = self.app
        self.existing = existing
        try:
            app.inference_key = app.storage.load_inference_key(stored.provider)
        except ConfigurationError:
            app.inference_key = None
        self.provider_var.set(get_provider(stored.provider).label)
        self.window.title(f"{get_provider(stored.provider).label}用量查询设置")
        values = {
            "access_key": existing.access_key_id if existing else "",
            "secret_key": "",
            "region": stored.region,
            "refresh": str(stored.refresh_minutes),
            "alert_threshold": str(stored.alert.threshold_percent),
        }
        for key, field in self.entries.items():
            field.state(["!invalid"])
            error = self.errors.get(key)
            if error is not None:
                error.grid_remove()
                error.configure(text="")
        for key, value in values.items():
            field = self.entries[key]
            field.delete(0, "end")
            field.insert(0, value)
        self.alert_enabled.set(stored.alert.enabled)
        self.alert_reset.set(stored.alert.notify_on_reset)
        self._general_error.configure(text="", style="Error.TLabel")
        self.claude_panel.reset(
            app.claude_seed(stored.provider, model_options=options), options=options,
        )

    # ------------------------------------------------------------------ form
    def _build_form(self, provider: str, region: str, interval: str,
                    alert_enabled: bool, alert_threshold: str, alert_reset: bool,
                    existing: Credentials | None, *, focus_entry: bool = True,
                    model_options=None) -> None:
        app = self.app
        spec = get_provider(provider)
        self.provider = provider
        self.existing = existing
        self.entries = {}
        self.errors = {}
        if self.claude_panel is not None:
            self.claude_panel.cancel_pending_fetch()
        self.claude_panel = None
        try:
            app.inference_key = app.storage.load_inference_key(provider)
        except ConfigurationError:
            app.inference_key = None
        self.provider_var.set(spec.label)
        if self._general_error is not None:
            self._general_error.configure(text="")
        self.window.title(f"{spec.label}用量查询设置")
        for child in self.form_frame.winfo_children():
            child.destroy()

        def panel(title: str) -> tk.Frame:
            outer = RoundedPanel(
                self.form_frame, radius=RADIUS["panel"], fill=app.colors["card"],
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
        ttk.Label(
            appearance, text="窗口材质", style="Section.TLabel"
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(14, 0))
        material_selector = ttk.Frame(appearance, style="Card.TFrame")
        material_selector.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        for column, (mode, label) in enumerate(MATERIAL_LABELS.items()):
            material_selector.columnconfigure(column, weight=1)
            material_button = ttk.Radiobutton(
                material_selector, text=label, value=mode, variable=self.material_var,
                style="Theme.TRadiobutton",
                command=lambda: app._set_material(self.material_var.get()),
            )
            material_button.grid(row=0, column=column, sticky="ew", padx=2)
            # Acrylic needs the Windows 11 DWM path; on Windows 10 it renders black.
            if mode == "acrylic" and not is_windows_11():
                material_button.state(("disabled",))
        ttk.Label(
            appearance,
            text="亚克力为磨砂透明效果；受系统限制，分层窗口会关闭文字 ClearType，实色文字最清晰。",
            style="CardMuted.TLabel", wraplength=round(340 * dpi_scale(self.window)),
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(10, 0))

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

        claude_frame = panel("Claude Code 接入")
        options = model_options or app.effective_claude_options(provider)
        self.claude_panel = ClaudePanel(
            self, claude_frame,
            app.claude_seed(provider, model_options=options), options=options,
        )

        if focus_entry:
            self.window.after_idle(self._focus_selected_entry)

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
        # The write confirmation spins a nested Tk event loop, so the 20ms
        # rebuild timer keeps firing while apply_claude still holds the form.
        # Leave the flag raised and retry on the next tick instead of
        # detaching the widgets mid-write.
        if self.app._applying_claude:
            self._rebuild_after = self.window.after(100, self._flush_rebuild)
            return
        self._rebuild_pending = False
        provider = self._label_to_id.get(self.provider_var.get())
        if provider is None or provider == self.provider:
            return
        spec = get_provider(provider)
        # Stash the outgoing provider's Claude form *before* the rebuild, or
        # the new panel would discard whatever was typed. The new provider is
        # then seeded from its own draft/prefs, falling back to the live file.
        self.app.stash_claude_draft(self.provider)
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
        self.app.maybe_auto_fetch(provider)

    # ----------------------------------------------------------------- helpers
    def focus(self) -> None:
        if self.window is not None and self.window.winfo_exists():
            with tcl_events_deferred():
                self.window.deiconify()
                self.window.lift()
                self.window.focus_force()

    def _focus_selected_entry(self) -> None:
        if not self._presented or self.window is None:
            return
        key = "secret_key" if self.existing is not None else "access_key"
        field = self.entries.get(key)
        if field is not None and field.winfo_exists():
            field.focus_set()

    def _present(self) -> None:
        self._present_after = None
        window = self.window
        if (self._closing or window is None or not window.winfo_exists()
                or self._presented):
            return
        self._presented = True
        # Defer activation to the next event turn so retained child windows can
        # process Map/Configure first; no full update() or off-screen transition.
        with tcl_events_deferred():
            window.deiconify()
            window.update_idletasks()
            self.body.canvas.yview_moveto(0)
        self._anchor_after = window.after(0, self._anchor_initial_view)

    def _anchor_initial_view(self) -> None:
        self._anchor_after = None
        if (self._closing or self.body is None or self.window is None
                or not self.window.winfo_exists()):
            return
        window = self.window
        with tcl_events_deferred():
            window.update_idletasks()
            self.body.canvas.yview_moveto(0)
            window.lift()
            window.focus_force()
        # Network work starts only after the first complete visible viewport;
        # hidden retention/reset never triggers a catalogue request.
        self.app.maybe_auto_fetch(self.provider)

    def _fit(self) -> None:
        self._fit_after = None
        window = self.window
        if (self._closing or window is None or not window.winfo_exists()
                or window._geometry.initialized):
            return
        # Geometry work includes several native message-pumping boundaries.
        # Keep the existing sizing/centering order, but defer nested Tcl
        # servicing for the whole calculation and schedule presentation after
        # the ambient notifier mode has been restored.
        with tcl_events_deferred():
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
            if not self._presented:
                # Size alone does not constrain Windows' default cascade origin.
                fitted_width, fitted_height = window._geometry.pixel_size
                x = left + max(10, (right - left - fitted_width) // 2)
                y = top + max(10, (bottom - top - fitted_height) // 2)
                window.geometry(f"+{x}+{y}")
        if not self._presented and not self._closing:
            self._present_after = window.after(0, self._present)

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
            self._scroll_widget_into_view(self.entries[field])

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
            app.storage.save_appearance(
                AppearancePreferences(app.theme_mode, app.material))
        except QueryError as exc:
            self._show_validation_error(exc.message)
            return
        if new_settings != self.settings or new_credentials != self.existing:
            app.monitor.update_configuration(new_settings, new_credentials)
        # Remember whatever Claude choices are typed (the key stays in memory).
        app.stash_claude_draft(new_settings.provider)
        # The window hides on save, but the worker/drain still persist the
        # fetched catalogue to models.json so the next open starts fresh.
        app.try_auto_fetch_models(new_settings.provider, skip_duplicate=True)
        self.close(commit=True)
        app.show_popup()

    # ------------------------------------------------------------ Claude panel
    def claude_form_values(self):
        """Snapshot the Claude section, or None when no panel is built."""
        if self.claude_panel is None:
            return None
        return self.claude_panel.form_values()

    def show_form_message(self, text: str, tone: str = "error") -> None:
        """Feedback for Claude writes, tinted by tone.

        Routed to a label inside the Claude card so it cannot fall below the
        scroll fold; the form-level label remains only as a no-panel fallback.
        """
        panel = self.claude_panel
        if panel is not None:
            panel.set_write_status(text, tone)
            self._fit()
            self._scroll_widget_into_view(panel.write_status_label)
            return
        style = {
            "error": "Error.TLabel",
            "ok": "Success.TLabel",
            "warning": "Warning.TLabel",
            "neutral": "Muted.TLabel",
        }.get(tone, "Error.TLabel")
        self._general_error.configure(text=text, style=style)
        self._fit()

    def _scroll_widget_into_view(self, widget: tk.Misc, *, margin: int = 80) -> None:
        """Scroll the canvas so `widget` (a card child) sits inside the viewport."""
        if self.window is None or not self.window.winfo_exists():
            return
        self.window.update_idletasks()
        target = widget.winfo_rooty() - self.body.content.winfo_rooty()
        self.body.canvas.yview_moveto(
            max(0, (target - margin) / max(1, self.body.content.winfo_height()))
        )

    def apply_claude(self) -> None:
        """Validate, confirm, then write provider + model into Claude Code."""
        app = self.app
        panel = self.claude_panel
        if app._applying_claude or panel is None:
            return
        provider = self.provider
        app._applying_claude = True
        panel.cancel_pending_fetch()
        panel.set_write_status("")
        panel.set_write_busy(True)
        succeeded = False
        try:
            # Paint the busy state before any nested event loop (the confirm
            # box) so the click always gives immediate visible feedback.
            panel.frame.update_idletasks()
            popup, custom, base_url, key_text, prefs = panel.form_values()
            existing_key = app.stored_inference_key(provider)
            settings_path, onboarding_path = app.resolve_claude_paths()
            try:
                request = validate_claude_configuration(
                    provider, self.entries["region"].get(),
                    popup, custom, base_url, key_text, existing_key,
                    prefs.auto_mode_model, prefs.auto_mode_custom_model,
                    prefs.subagent_model, prefs.subagent_custom_model,
                )
            except FieldValidationError as exc:
                self._show_validation_error(
                    exc.message, _FIELD_TO_ENTRY.get(exc.field, exc.field)
                )
                return
            except QueryError as exc:
                # ConfigurationError (vault failures) is a subclass.
                self.show_form_message(exc.message, "error")
                return
            try:
                document, had_comments = claude_code.read_document_at(settings_path)
            except ClaudeConfigError as exc:
                self.show_form_message(
                    f"{exc.message}；原始文件未被修改，请先修复后重试。", "error"
                )
                return
            patch = claude_code.compute_env_patch(request, document)
            changes = claude_code.describe_changes(
                request, patch, document, settings_path, had_comments
            )
            # Refresh once more so the busy label is on screen behind the modal.
            self.window.update_idletasks()
            if not app.confirm_claude_write(changes):
                self.show_form_message("已取消，未改动 Claude 配置。", "neutral")
                return
            try:
                result = claude_code.apply_request(request, settings_path)
                # The vault now holds exactly the key written into the config,
                # whether it came from the field or from a session draft.
                app.storage.save_inference_key(request.api_key, provider)
                app.inference_key = request.api_key
                app._claude_key_drafts.pop(provider, None)
                panel.entries["api_key"].delete(0, "end")
                # The file we just wrote is now reality; drop the session draft
                # so a later hand-edit of the endpoint outranks the cache.
                app._claude_drafts.pop(provider, None)
                applied = ClaudeProviderPrefs(
                    model=request.model, custom_model="", base_url=request.base_url
                )
                app.storage.save_claude_prefs(applied, provider)
            except QueryError as exc:
                # Covers ClaudeConfigError, CacheError and keyring failures.
                self.show_form_message(f"{exc.message}；请检查磁盘权限后重试。", "error")
                return
            notes = ["已新建配置" if result.created else "已更新配置"]
            if result.backup_path is not None:
                notes.append(f"原文件已备份为 {result.backup_path.name}")
            if not claude_code.mark_onboarding_completed(onboarding_path):
                notes.append("未能合并 .claude.json 的引导标记（已跳过）")
            notes.append("请重开 Claude Code 会话以确认生效")
            self.show_form_message("；".join(notes), "ok")
            succeeded = True
        except Exception:
            # A packaged build has no console for Tk's default traceback; log
            # the detail and surface a generic red card message (no exc text,
            # which could carry path or secret fragments).
            logger.exception("Unexpected Claude config write failure")
            self.show_form_message(
                "写入 Claude 配置时发生意外错误，详情见日志。", "error")
        finally:
            app._applying_claude = False
            panel.set_write_busy(False)
            if succeeded:
                # The key field was cleared programmatically (no KeyRelease),
                # and the vault now holds the key, so this resolves to the
                # just-written credentials; signature dedup makes it a no-op
                # when the catalogue is already current.
                app.try_auto_fetch_models(provider, skip_duplicate=True)

    def _cancel_callbacks(self) -> None:
        window = self.window
        if self.claude_panel is not None:
            self.claude_panel.cancel_pending_fetch()
        if window is not None and window.winfo_exists():
            for after_id in (
                self._fit_after, self._present_after, self._anchor_after,
                self._rebuild_after,
            ):
                if after_id is not None:
                    try:
                        window.after_cancel(after_id)
                    except tk.TclError:
                        pass
        self._fit_after = None
        self._present_after = None
        self._anchor_after = None
        self._rebuild_after = None
        self._rebuild_pending = False

    def _clear_secrets(self) -> None:
        for key in ("secret_key", "api_key"):
            field = self.entries.get(key)
            if field is not None and field.winfo_exists():
                field.delete(0, "end")

    def close(self, *, commit: bool = False) -> None:
        """Hide for a fast later open; disposal is reserved for failure/shutdown."""
        if self._closing:
            return
        self._closing = True
        original = self.original_mode
        original_material = self.original_material
        self.original_mode = None
        self.original_material = None
        app = self.app
        if app._settings_dialog is self:
            app.settings_window = None
            app.settings_body = None
        self._cancel_callbacks()
        self._clear_secrets()
        window = self.window
        if window is not None and window.winfo_exists():
            with tcl_events_deferred():
                geometry = getattr(window, "_geometry", None)
                if geometry is not None:
                    geometry.flush()
                window.withdraw()
        self._presented = False
        if not commit:
            if original is not None:
                app._set_theme(original)
            if original_material is not None and original_material != app.material:
                app._set_material(original_material)
        if app.popup is not None and app.popup.winfo_viewable():
            app.popup.focus_set()

    def dispose(self) -> None:
        """Release the retained native window and all callback references."""
        self._closing = True
        self._cancel_callbacks()
        self._clear_secrets()
        app = self.app
        if app._settings_dialog is self:
            app.settings_window = None
            app.settings_body = None
            app._settings_dialog = None
        window = self.window
        if window is not None and window.winfo_exists():
            with tcl_events_deferred():
                geometry = getattr(window, "_geometry", None)
                if geometry is not None:
                    geometry.flush()
                window.destroy()
        self.window = None
        self.body = None
        self.form_frame = None
        self.claude_panel = None
        self.entries = {}
        self.errors = {}
        self._presented = False


class ClaudePanel:
    """The Claude Code hookup card inside the settings dialog.

    Holds only view state (comboboxes, entries, status label); per-provider
    drafts, the catalogue queue and the background thread all stay on the
    owning :class:`MonitorUI`. The three model pairs share one rule: a typed
    custom ID always outranks the combobox selection, and the leading
    "unset/default" rows mean "do not touch that env key".
    """

    def __init__(self, dialog: SettingsDialog, frame: ttk.Frame, seed: dict[str, str],
                 *, options=None):
        self.dialog = dialog
        self.app = dialog.app
        self.frame = frame
        # Mirrored into dialog.entries so the shared field-tagged validation
        # can focus a Claude entry exactly like a credential entry.
        self.entries: dict[str, ttk.Entry] = {}
        self.labels: dict[str, ttk.Label] = {}
        # Debounce handle for the KeyRelease/FocusOut-driven catalogue fetch.
        self._fetch_after: str | None = None
        self.options = (
            options if options is not None
            else self.app.effective_claude_options(dialog.provider)
        )
        spec = get_provider(dialog.provider)
        scale = dpi_scale(dialog.window)
        known = [option.id for option in self.options]
        labels = [option.label for option in self.options]

        ttk.Label(
            frame,
            text="选择模型后点击下方按钮，把服务商接入信息写入 Claude Code 本地配置。",
            style="CardMuted.TLabel",
            wraplength=round(340 * scale),
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 8))

        refresh = ttk.Button(
            frame, text="刷新模型列表", style="Secondary.TButton",
            command=lambda: self.app.refresh_models(dialog.provider),
        )
        refresh.grid(row=2, column=0, sticky="w")
        status_text, status_tone = self.app.catalog_status_for(dialog.provider)
        self.status_label = ttk.Label(
            frame, text=status_text, style=_STATUS_STYLE.get(status_tone, "CardMuted.TLabel"),
            wraplength=round(250 * scale),
        )
        # ew lets the label track the weighted cell; the ScrollableBody wrap
        # walk then caps its wrap length to the real allocation (the refresh
        # button owns column 0), so a long blocked-reason status never clips.
        self.status_label.grid(row=2, column=1, sticky="ew", padx=(10, 0))
        frame.columnconfigure(1, weight=1)

        # --- Main model -----------------------------------------------------
        ttk.Label(frame, text="模型", style="Card.TLabel").grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(6, 5))
        model_values = labels + [CUSTOM_MARKER]
        self.model_combo = ttk.Combobox(
            frame, state="readonly", values=model_values,
        )
        self._select_combo(
            self.model_combo, known, seed["claude_model"],
            seed["claude_custom"], leading=0, fallback=len(known),
        )
        self.model_combo.grid(row=4, column=0, columnspan=2, sticky="ew")
        self._entry(
            5, "claude_model",
            "自定义模型 ID（在此填写时优先于上方选择）",
            seed["claude_custom"],
        )

        # --- Auto mode classifier (optional) --------------------------------
        ttk.Label(
            frame, text="Auto mode 分类模型（可选）", style="Card.TLabel",
        ).grid(row=8, column=0, columnspan=2, sticky="w", pady=(6, 5))
        auto_values = [AUTO_UNSET] + labels + [CUSTOM_MARKER]
        self.auto_combo = ttk.Combobox(
            frame, state="readonly", values=auto_values,
        )
        self._select_combo(
            self.auto_combo, known, seed["claude_auto_model"],
            seed["claude_auto_custom"], leading=1, fallback=0,
        )
        self.auto_combo.grid(row=9, column=0, columnspan=2, sticky="ew")
        ttk.Label(
            frame,
            text=("Auto mode 用该模型判断每条命令是否安全。建议选轻量模型："
                  "分类模型偏重时判断可能超时，工具调用会被拦下重试。"),
            style="CardMuted.TLabel", wraplength=round(340 * scale),
        ).grid(row=10, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self._entry(
            11, "claude_auto_model",
            "Auto mode 自定义模型 ID（优先于上方选择；留空且选「不设置」时不写入）",
            seed["claude_auto_custom"],
        )

        # --- Explicit subagent model (optional) -----------------------------
        ttk.Label(
            frame, text="Subagent 模型（可选）", style="Card.TLabel",
        ).grid(row=14, column=0, columnspan=2, sticky="w", pady=(6, 5))
        subagent_values = [SUBAGENT_DEFAULT] + labels + [CUSTOM_MARKER]
        self.subagent_combo = ttk.Combobox(
            frame, state="readonly", values=subagent_values,
        )
        self._select_combo(
            self.subagent_combo, known, seed["claude_subagent_model"],
            seed["claude_subagent_custom"], leading=1, fallback=0,
        )
        self.subagent_combo.grid(row=15, column=0, columnspan=2, sticky="ew")
        self._entry(
            16, "claude_subagent_model",
            "Subagent 自定义模型 ID（优先于上方选择；留空且选「默认」时沿用现有规则）",
            seed["claude_subagent_custom"],
        )

        # --- Endpoint + inference key ---------------------------------------
        self._entry(
            19, "base_url",
            "Anthropic Base URL（留空使用服务商默认地址）",
            seed["claude_base_url"],
        )
        key_title = (
            "推理 API Key（已保存，留空保持不变）"
            if self.app.stored_inference_key(dialog.provider)
            else f"推理 API Key（必填{spec.inference_key_hint}）"
        )
        self._entry(22, "api_key", key_title, "", secret=True)
        # The live catalogue becomes available as soon as a key and a usable
        # URL exist; debounce both fields so ordinary typing fires one fetch.
        for field_key in ("base_url", "api_key"):
            self.entries[field_key].bind(
                "<KeyRelease>", self._schedule_fetch_check, add="+")
            self.entries[field_key].bind(
                "<FocusOut>", self._schedule_fetch_check, add="+")

        if not spec.claude_base_url_verified:
            ttk.Label(
                frame,
                text="⚠ 该服务商的 Anthropic 兼容端点未经验证，请填写服务商提供的地址并自行确认。",
                style="CardWarning.TLabel", wraplength=round(340 * scale),
            ).grid(row=25, column=0, columnspan=2, sticky="w", pady=(6, 0))
            apply_row = 26
        else:
            apply_row = 25
        # No key equivalent: an accidental Enter must never trigger an
        # external config change, so the write button is click-only.
        self.apply_button = ttk.Button(
            frame, text="写入 Claude 配置", style="Primary.TButton",
            command=dialog.apply_claude,
        )
        self.apply_button.grid(
            row=apply_row, column=0, columnspan=2, sticky="w", pady=(10, 0))
        # Pinned in-card result feedback: the form-level label below the
        # scrollable body can sit below the fold once geometry is capped.
        self.write_status_label = ttk.Label(
            frame, text="", style="CardMuted.TLabel",
            wraplength=round(340 * scale), justify="left",
        )
        self.write_status_label.grid(
            row=apply_row + 1, column=0, columnspan=2, sticky="w", pady=(8, 0))

    @staticmethod
    def _select_combo(combo: ttk.Combobox, known: list[str], selected: str,
                      custom: str, *, leading: int, fallback: int) -> None:
        if custom:
            combo.current(len(known) + leading)
        elif selected in known:
            combo.current(leading + known.index(selected))
        else:
            combo.current(fallback)

    @staticmethod
    def _set_entry(field: ttk.Entry, value: str) -> None:
        field.delete(0, "end")
        field.insert(0, value)

    def reset(self, seed: dict[str, str], *, options) -> None:
        """Apply authoritative values while retaining widgets and their images."""
        self.cancel_pending_fetch()
        self.options = options
        known = [option.id for option in options]
        labels = [option.label for option in options]
        self.model_combo.configure(values=labels + [CUSTOM_MARKER])
        self.auto_combo.configure(values=[AUTO_UNSET] + labels + [CUSTOM_MARKER])
        self.subagent_combo.configure(
            values=[SUBAGENT_DEFAULT] + labels + [CUSTOM_MARKER])
        for combo, selected, custom, leading, fallback in (
            (self.model_combo, seed["claude_model"], seed["claude_custom"], 0, len(known)),
            (self.auto_combo, seed["claude_auto_model"], seed["claude_auto_custom"], 1, 0),
            (self.subagent_combo, seed["claude_subagent_model"],
             seed["claude_subagent_custom"], 1, 0),
        ):
            self._select_combo(
                combo, known, selected, custom, leading=leading, fallback=fallback,
            )
        for key, value in (
            ("claude_model", seed["claude_custom"]),
            ("claude_auto_model", seed["claude_auto_custom"]),
            ("claude_subagent_model", seed["claude_subagent_custom"]),
            ("base_url", seed["claude_base_url"]),
            ("api_key", ""),
        ):
            self._set_entry(self.entries[key], value)
        spec = get_provider(self.dialog.provider)
        key_title = (
            "推理 API Key（已保存，留空保持不变）"
            if self.app.stored_inference_key(self.dialog.provider)
            else f"推理 API Key（必填{spec.inference_key_hint}）"
        )
        self.labels["api_key"].configure(text=key_title)
        status_text, status_tone = self.app.catalog_status_for(self.dialog.provider)
        self.set_status(status_text, status_tone)
        self.set_write_status("")
        self.set_write_busy(False)

    def _entry(self, row: int, key: str, label: str, value: str,
               *, secret: bool = False) -> ttk.Entry:
        label_widget = ttk.Label(self.frame, text=label, style="Card.TLabel")
        label_widget.grid(row=row, column=0, columnspan=2, sticky="w", pady=(6, 5))
        self.labels[key] = label_widget
        field = ttk.Entry(self.frame, width=28, show="•" if secret else "")
        field.insert(0, value)
        field.grid(row=row + 1, column=0, columnspan=2, sticky="ew")
        # The error label stays ungridded until the shared validator tags
        # this field; the row below each entry is reserved for it.
        error = ttk.Label(
            self.frame, text="", style="FieldError.TLabel",
            wraplength=round(315 * dpi_scale(self.dialog.window)),
        )
        self.entries[key] = field
        self.dialog.entries[key] = field
        self.dialog.errors[key] = error
        return field

    def form_values(self):
        """Snapshot the section: (popup id, custom, base url, key, prefs)."""
        known = [option.id for option in self.options]
        index = self.model_combo.current()
        popup = known[index] if 0 <= index < len(known) else ""
        custom = self.entries["claude_model"].get().strip()
        # Leading "unset/default" row shifts the catalogue by one; the last
        # row is the custom marker, whose value lives in the text field.
        auto_index = self.auto_combo.current()
        auto_popup = known[auto_index - 1] if 1 <= auto_index <= len(known) else ""
        auto_custom = self.entries["claude_auto_model"].get().strip()
        subagent_index = self.subagent_combo.current()
        subagent_popup = (
            known[subagent_index - 1] if 1 <= subagent_index <= len(known) else ""
        )
        subagent_custom = self.entries["claude_subagent_model"].get().strip()
        base_url = self.entries["base_url"].get().strip()
        key = self.entries["api_key"].get()
        prefs = ClaudeProviderPrefs(
            model="" if custom else popup,
            custom_model=custom,
            base_url=base_url,
            auto_mode_model="" if auto_custom else auto_popup,
            auto_mode_custom_model=auto_custom,
            subagent_model="" if subagent_custom else subagent_popup,
            subagent_custom_model=subagent_custom,
        )
        return popup, custom, base_url, key, prefs

    def apply_catalog_options(self, options) -> None:
        """Rebuild just the comboboxes in place, preserving the selection.

        A full form rebuild would discard typing in the credential fields and
        bounce focus; a selected model the plan no longer offers is demoted
        into its custom field rather than lost.
        """
        popup, custom, _base, _key, prefs = self.form_values()
        current_auto = prefs.auto_mode_custom_model or prefs.auto_mode_model
        current_subagent = prefs.subagent_custom_model or prefs.subagent_model
        self.options = options
        known = [option.id for option in options]
        labels = [option.label for option in options]

        self.model_combo.configure(values=labels + [CUSTOM_MARKER])
        if custom:
            self.model_combo.current(len(known))
        elif popup in known:
            self.model_combo.current(known.index(popup))
        elif popup:
            self.entries["claude_model"].delete(0, "end")
            self.entries["claude_model"].insert(0, popup)
            self.model_combo.current(len(known))
        else:
            self.model_combo.current(0)

        self.auto_combo.configure(values=[AUTO_UNSET] + labels + [CUSTOM_MARKER])
        if prefs.auto_mode_custom_model:
            self.auto_combo.current(len(known) + 1)
        elif current_auto in known:
            self.auto_combo.current(1 + known.index(current_auto))
        elif current_auto:
            self.entries["claude_auto_model"].delete(0, "end")
            self.entries["claude_auto_model"].insert(0, current_auto)
            self.auto_combo.current(len(known) + 1)
        else:
            self.auto_combo.current(0)

        self.subagent_combo.configure(
            values=[SUBAGENT_DEFAULT] + labels + [CUSTOM_MARKER])
        if prefs.subagent_custom_model:
            self.subagent_combo.current(len(known) + 1)
        elif current_subagent in known:
            self.subagent_combo.current(1 + known.index(current_subagent))
        elif current_subagent:
            self.entries["claude_subagent_model"].delete(0, "end")
            self.entries["claude_subagent_model"].insert(0, current_subagent)
            self.subagent_combo.current(len(known) + 1)
        else:
            self.subagent_combo.current(0)

    def set_status(self, text: str, tone: str) -> None:
        self.status_label.configure(
            text=text, style=_STATUS_STYLE.get(tone, "CardMuted.TLabel"))

    def set_write_status(self, text: str, tone: str = "neutral") -> None:
        """Result of the Claude write, pinned inside the card and always visible."""
        self.write_status_label.configure(
            text=text, style=_STATUS_STYLE.get(tone, "CardMuted.TLabel"))

    def set_write_busy(self, busy: bool) -> None:
        """Immediate press feedback; the disabled state blocks double clicks."""
        if busy:
            self.apply_button.configure(text="写入中…", state=["disabled"])
        else:
            self.apply_button.configure(text="写入 Claude 配置", state=["!disabled"])

    # ------------------------------------------------- automatic catalogue pull
    def _schedule_fetch_check(self, event=None) -> None:
        """Coalesce field edits into one auto-fetch attempt 400ms after typing."""
        dialog = self.dialog
        if (dialog.claude_panel is not self or dialog._closing
                or dialog._rebuild_pending or dialog.window is None):
            return
        self.cancel_pending_fetch()
        self._fetch_after = dialog.window.after(400, self._flush_fetch_check)

    def _flush_fetch_check(self) -> None:
        self._fetch_after = None
        dialog = self.dialog
        # The panel may have been replaced or the dialog closed while waiting.
        if (dialog.claude_panel is not self or dialog._closing
                or dialog._rebuild_pending or dialog.window is None
                or not dialog.window.winfo_exists()):
            return
        self.app.try_auto_fetch_models(dialog.provider, skip_duplicate=True)

    def cancel_pending_fetch(self) -> None:
        if self._fetch_after is not None and self.dialog.window is not None:
            try:
                self.dialog.window.after_cancel(self._fetch_after)
            except tk.TclError:
                pass
        self._fetch_after = None
