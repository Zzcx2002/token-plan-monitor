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

from . import claude_code
from .appearance import (
    OUTER_PADDING,
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

#: Status-label tones map to card-scoped ttk styles so they sit on the panel.
_STATUS_STYLE = {
    "neutral": "CardMuted.TLabel",
    "warning": "CardWarning.TLabel",
    "ok": "CardSuccess.TLabel",
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
        self.claude_panel: ClaudePanel | None = None

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

        claude_frame = panel("Claude Code 接入")
        self.claude_panel = ClaudePanel(self, claude_frame, app.claude_seed(provider))

        self.window.after_idle(
            self.entries["secret_key" if existing is not None else "access_key"].focus_set
        )
        # The catalogue auto-pulls on open and on every provider switch, but
        # only when a key and an Anthropic endpoint are actually available.
        self.window.after_idle(lambda: app.maybe_auto_fetch(provider))

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
        # Remember whatever Claude choices are typed (the key stays in memory).
        app.stash_claude_draft(new_settings.provider)
        self.close(commit=True)
        app.show_popup()

    # ------------------------------------------------------------ Claude panel
    def claude_form_values(self):
        """Snapshot the Claude section, or None when no panel is built."""
        if self.claude_panel is None:
            return None
        return self.claude_panel.form_values()

    def show_form_message(self, text: str, tone: str = "error") -> None:
        """Pinned-header feedback for Claude writes, tinted by tone."""
        style = {
            "error": "Error.TLabel",
            "ok": "Success.TLabel",
            "warning": "Warning.TLabel",
            "neutral": "Muted.TLabel",
        }.get(tone, "Error.TLabel")
        self._general_error.configure(text=text, style=style)
        self._fit()

    def apply_claude(self) -> None:
        """Validate, confirm, then write provider + model into Claude Code."""
        app = self.app
        panel = self.claude_panel
        if app._applying_claude or panel is None:
            return
        app._applying_claude = True
        try:
            provider = self.provider
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
        finally:
            app._applying_claude = False

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


class ClaudePanel:
    """The Claude Code hookup card inside the settings dialog.

    Holds only view state (comboboxes, entries, status label); per-provider
    drafts, the catalogue queue and the background thread all stay on the
    owning :class:`MonitorUI`. The three model pairs share one rule: a typed
    custom ID always outranks the combobox selection, and the leading
    "unset/default" rows mean "do not touch that env key".
    """

    def __init__(self, dialog: SettingsDialog, frame: ttk.Frame, seed: dict[str, str]):
        self.dialog = dialog
        self.app = dialog.app
        self.frame = frame
        # Mirrored into dialog.entries so the shared field-tagged validation
        # can focus a Claude entry exactly like a credential entry.
        self.entries: dict[str, ttk.Entry] = {}
        self.options = self.app.effective_claude_options(dialog.provider)
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
        self.status_label.grid(row=2, column=1, sticky="w", padx=(10, 0))
        frame.columnconfigure(1, weight=1)

        # --- Main model -----------------------------------------------------
        ttk.Label(frame, text="模型", style="Card.TLabel").grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(6, 5))
        model_values = labels + [CUSTOM_MARKER]
        self.model_combo = ttk.Combobox(
            frame, state="readonly", values=model_values,
        )
        model_index = (
            known.index(seed["claude_model"])
            if seed["claude_model"] in known else len(known)
        )
        self.model_combo.current(model_index)
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
        if seed["claude_auto_custom"]:
            auto_index = len(auto_values) - 1
        elif seed["claude_auto_model"] in known:
            auto_index = 1 + known.index(seed["claude_auto_model"])
        else:
            auto_index = 0
        self.auto_combo.current(auto_index)
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
        if seed["claude_subagent_custom"]:
            subagent_index = len(subagent_values) - 1
        elif seed["claude_subagent_model"] in known:
            subagent_index = 1 + known.index(seed["claude_subagent_model"])
        else:
            subagent_index = 0
        self.subagent_combo.current(subagent_index)
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
        ttk.Button(
            frame, text="写入 Claude 配置", style="Primary.TButton",
            command=dialog.apply_claude,
        ).grid(row=apply_row, column=0, columnspan=2, sticky="w", pady=(10, 0))

    def _entry(self, row: int, key: str, label: str, value: str,
               *, secret: bool = False) -> ttk.Entry:
        ttk.Label(self.frame, text=label, style="Card.TLabel").grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(6, 5))
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
