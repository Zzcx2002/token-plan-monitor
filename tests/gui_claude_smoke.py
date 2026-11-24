"""Exercise the Windows Claude Code hookup with temp paths, no cloud or tray.

Every Claude Code write goes through ``claude_paths_override`` into a temp
directory, so the real ``~/.claude`` is never touched; the model-list fetch
and the confirmation box are injected seams. Covers the same rules as the
macOS settings flow: validated writes with backup and onboarding merge, the
inference key never being echoed, cancel/validation doing zero writes,
per-provider draft memory, a background catalogue update, and shutdown
clearing the in-memory key drafts.
"""
import json
import tempfile
from pathlib import Path
from tkinter import ttk

from gui_preview import ARTIFACTS, FakeMonitor
from token_plan_monitor import claude_code
from token_plan_monitor.model_catalog import ModelCatalogError
from token_plan_monitor.providers import get_provider
from token_plan_monitor.storage import Storage
from token_plan_monitor.ui import MonitorUI
from token_plan_monitor.settings_view import CUSTOM_MARKER


class MemoryKeyring:
    def __init__(self):
        self.values = {}

    def get_password(self, service, username):
        return self.values.get((service, username))

    def set_password(self, service, username, value):
        self.values[service, username] = value


class Monitor(FakeMonitor):
    def update_configuration(self, settings, credentials):
        return None


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def pump(app, ms=120):
    app.root.after(ms, app.root.quit)
    app.root.mainloop()


def label_texts(window):
    return [w.cget("text") for w in descendants(window)
            if isinstance(w, ttk.Label) and w.cget("text")]


def main():
    with tempfile.TemporaryDirectory(dir=ARTIFACTS) as temporary:
        root = Path(temporary)
        monitor = Monitor()
        storage = Storage(root / "data", MemoryKeyring())
        app = MonitorUI(monitor, storage)
        callback_errors = []
        app.root.report_callback_exception = lambda *error: callback_errors.append(error)

        # Two isolated Claude "home" targets: one for the no-write cases,
        # one for the successful write flow.
        cancel_home = root / "cancel-home"
        write_home = root / "write-home"
        cancel_settings = cancel_home / "settings.json"
        cancel_onboarding = cancel_home / ".claude.json"
        settings_path = write_home / "settings.json"
        onboarding_path = write_home / ".claude.json"
        app.claude_paths_override = (cancel_settings, cancel_onboarding)

        # The catalogue "network" is an injected seam; record every call so
        # the auto/refresh gating can be asserted without real traffic.
        fetch_calls = []
        static_ids = tuple(option.id for option in get_provider("bailian").claude_models)

        def fake_fetch(base_url, key):
            fetch_calls.append((base_url, key))
            # Ark publishes no list endpoint: exercise the failure fallback
            # (keep the static/empty catalogue, surface a warning status).
            if "volces.com" in base_url:
                raise ModelCatalogError("模拟 Ark 端点不支持模型列表")
            return static_ids + ("brand-new-model",)

        app._fetch_model_ids = fake_fetch
        confirm_state = {"allow": True, "calls": []}
        busy_seen = []

        def confirm(lines):
            confirm_state["calls"].append(lines)
            # The button must already show its busy state while the modal
            # confirmation runs on the nested event loop.
            active_panel = dialog.claude_panel
            busy_seen.append((
                active_panel.apply_button.cget("text"),
                tuple(active_panel.apply_button.state())))
            return confirm_state["allow"]

        app._confirm_override = confirm
        try:
            app._create_popup()
            # Production starts this 100ms queue pump inside run(); the smoke
            # drives the same loop so the catalogue drain path is the real one.
            app.root.after(100, app._process_events)
            app.open_settings()
            pump(app)
            dialog = app._settings_dialog
            assert dialog is not None and dialog.claude_panel is not None
            panel = dialog.claude_panel
            # The override guarantees nothing can land in a real ~/.claude.
            assert str(cancel_settings).startswith(str(root))

            # Default provider is volcengine: no curated catalogue (the only
            # combo row is the custom marker) and the unverified-URL warning.
            assert dialog.provider == "volcengine"
            assert panel.model_combo.cget("values") == (CUSTOM_MARKER,)
            assert any("未经验证" in text for text in label_texts(dialog.window))

            # --- Zero-write cases before any vault key exists ---------------
            # The validator checks the model pair first, so give the Ark panel
            # a custom id and URL; the missing inference key then tags exactly
            # the api_key field and nothing is written.
            panel.entries["claude_model"].insert(0, "doubao-seed-model")
            panel.entries["base_url"].insert(
                0, "https://example.cn-beijing.volces.com/apps/anthropic")
            panel.entries["api_key"].insert(0, "   ")
            dialog.apply_claude()
            pump(app)
            assert "invalid" in panel.entries["api_key"].state()
            assert bool(dialog.errors["api_key"].grid_info())
            # Field-tagged validation errors stay inline; the card write
            # status must not claim a write happened.
            assert panel.write_status_label.cget("text") == ""
            assert not cancel_settings.exists() and not cancel_onboarding.exists()

            # Confirmation declined: still zero writes.
            panel.entries["api_key"].delete(0, "end")
            panel.entries["api_key"].insert(0, "sk-volc-test")
            confirm_state["allow"] = False
            dialog.apply_claude()
            pump(app)
            assert len(confirm_state["calls"]) == 1
            assert "写入目标" in confirm_state["calls"][0][0]
            assert not cancel_settings.exists() and not cancel_onboarding.exists()
            assert busy_seen[0] == ("写入中…", ("disabled",))
            assert "已取消" in panel.write_status_label.cget("text")
            assert panel.write_status_label.cget("style") == "CardMuted.TLabel"
            # The button is restored once the handler returns.
            assert panel.apply_button.cget("text") == "写入 Claude 配置"
            assert "disabled" not in panel.apply_button.state()
            assert dialog._general_error.cget("text") == ""
            confirm_state["allow"] = True

            # --- Switch to bailian for the full write flow ------------------
            dialog.provider_var.set(get_provider("bailian").label)
            dialog._schedule_rebuild()
            pump(app)
            assert dialog.provider == "bailian"
            panel = dialog.claude_panel
            # The pure seeder picks the provider default model; the optional
            # popups start at their leading "leave alone" rows.
            known = [option.id for option in panel.options]
            assert panel.model_combo.current() == known.index("auto")
            assert panel.auto_combo.current() == 0
            assert panel.subagent_combo.current() == 0
            popup, custom, base_url, key, prefs = panel.form_values()
            assert popup == "auto" and custom == "" and key == ""
            assert prefs.auto_mode_model == "" and prefs.subagent_model == ""

            # Manual refresh without a key is gated, with no fetch attempt.
            app.refresh_models("bailian")
            assert fetch_calls == []
            assert "请先填写推理 API Key" in panel.status_label.cget("text")
            panel.entries["api_key"].insert(0, "sk-sp-test-key")
            app.refresh_models("bailian")
            # The daemon worker hands back through the queue; pump until the
            # 100ms Tk drain has persisted it, bounded so a hang still fails.
            for _ in range(20):
                pump(app, 100)
                if storage.load_model_catalog("bailian"):
                    break
            assert not callback_errors, callback_errors
            assert len(fetch_calls) == 1
            assert fetch_calls[0][1] == "sk-sp-test-key"
            assert claude_code.ANTHROPIC_PATH in fetch_calls[0][0]
            cached = storage.load_model_catalog("bailian")
            assert "brand-new-model" in cached
            combo_values = panel.model_combo.cget("values")
            assert any("brand-new-model" in value for value in combo_values)
            assert panel.status_label.cget("text").startswith("已在线更新")
            # The selection survived the in-place combobox rebuild.
            assert panel.model_combo.current() == known.index("auto")

            # Both trigger events are bound on both editable endpoint fields.
            # (Synthetic key events need OS focus on Windows, so the debounce
            # machinery itself is driven deterministically below.)
            for field_key in ("base_url", "api_key"):
                sequences = panel.entries[field_key].bind()
                assert "<KeyRelease>" in sequences and "<FocusOut>" in sequences
            # Typing into the key field auto-fetches once after the 400ms
            # debounce; nothing starts inside the debounce window.
            panel.entries["api_key"].delete(0, "end")
            panel.entries["api_key"].insert(0, "sk-sp-typed")
            calls_before = len(fetch_calls)
            panel._schedule_fetch_check()
            assert panel._fetch_after is not None
            pump(app, 250)
            assert len(fetch_calls) == calls_before
            for _ in range(20):
                pump(app, 50)
                if len(fetch_calls) == calls_before + 1:
                    break
            assert len(fetch_calls) == calls_before + 1
            # A repeated schedule with the same signature is de-duped.
            panel._schedule_fetch_check()
            pump(app, 500)
            assert len(fetch_calls) == calls_before + 1
            # The applying guard blocks debounced fetches mid-write.
            app._applying_claude = True
            panel._schedule_fetch_check()
            pump(app, 500)
            assert len(fetch_calls) == calls_before + 1
            app._applying_claude = False
            # Restore the field value the write-flow assertions expect;
            # programmatic edits fire no KeyRelease, so no fetch follows.
            panel.entries["api_key"].delete(0, "end")
            panel.entries["api_key"].insert(0, "sk-sp-test-key")

            # --- Confirmed write: JSON, backup, onboarding, key hygiene -----
            app.claude_paths_override = (settings_path, onboarding_path)
            # Exercise the optional pairs too: auto picks a curated light
            # model, subagent an explicit one.
            panel.auto_combo.current(1 + known.index("qwen3.8-flash"))
            panel.subagent_combo.current(1 + known.index("qwen3.7-max"))
            dialog.apply_claude()
            pump(app)
            assert settings_path.exists() and onboarding_path.exists()
            document = json.loads(settings_path.read_text(encoding="utf-8"))
            env = document["env"]
            assert env[claude_code.ENV_MODEL] == "auto"
            assert env[claude_code.ENV_AUTH_TOKEN] == "sk-sp-test-key"
            assert env[claude_code.ENV_BASE_URL].endswith(claude_code.ANTHROPIC_PATH)
            assert env[claude_code.ENV_AUTO_MODE] == "qwen3.8-flash"
            assert env[claude_code.ENV_SUBAGENT] == "qwen3.7-max"
            assert document["model"] == "auto"
            assert json.loads(onboarding_path.read_text(encoding="utf-8"))[
                "hasCompletedOnboarding"] is True
            # The key is vaulted, never echoed back into the field, and the
            # vault entry is the one that was written.
            assert panel.entries["api_key"].get() == ""
            assert storage.load_inference_key("bailian") == "sk-sp-test-key"
            assert app.inference_key == "sk-sp-test-key"
            assert "已新建配置" in panel.write_status_label.cget("text")
            assert panel.write_status_label.cget("style") == "CardSuccess.TLabel"
            assert dialog._general_error.cget("text") == ""
            assert busy_seen[1] == ("写入中…", ("disabled",))
            assert "disabled" not in panel.apply_button.state()
            # The programmatic key clear starts no fetch by itself; the
            # post-write fetch re-pulls because the debounce test had replaced
            # the signature with another key (dedup keeps only the latest).
            assert len(fetch_calls) == calls_before + 2

            # A second apply backs up the first file and removes a shadowing
            # ANTHROPIC_API_KEY instead of carrying it forward.
            document["env"][claude_code.ENV_SHADOWING_KEY] = "stale"
            settings_path.write_text(
                json.dumps(document, ensure_ascii=False), encoding="utf-8")
            dialog.apply_claude()
            pump(app)
            backups = list(write_home.glob("settings.json.bak.*"))
            assert len(backups) == 1
            rewritten = json.loads(settings_path.read_text(encoding="utf-8"))
            assert claude_code.ENV_SHADOWING_KEY not in rewritten["env"]
            assert rewritten["env"][claude_code.ENV_AUTH_TOKEN] == "sk-sp-test-key"
            assert "已更新配置" in panel.write_status_label.cget("text")
            # Identical signature to the previous write: de-duped, no new fetch.
            assert len(fetch_calls) == calls_before + 2

            # An unexpected error (here: the confirmation hook raising) must
            # never reach Tk's callback reporter or leave the button stuck.
            panel.entries["api_key"].insert(0, "sk-sp-test-key")
            def boom(lines):
                raise RuntimeError("simulated confirmation failure")
            app._confirm_override = boom
            dialog.apply_claude()
            pump(app)
            assert not callback_errors, callback_errors
            assert "意外错误" in panel.write_status_label.cget("text")
            assert panel.write_status_label.cget("style") == "CardError.TLabel"
            assert panel.apply_button.cget("text") == "写入 Claude 配置"
            assert "disabled" not in panel.apply_button.state()
            assert app._applying_claude is False
            app._confirm_override = confirm
            # The failed write leaves the field untouched; clear it so the
            # draft section below starts from an empty key field as before.
            panel.entries["api_key"].delete(0, "end")

            # --- Draft memory across a provider switch ----------------------
            panel.entries["claude_model"].insert(0, "my.custom-model")
            panel.entries["base_url"].delete(0, "end")
            # Legacy regional aliyuncs URL kept deliberately: it must keep
            # normalizing and driving the fetch gating after the host change.
            panel.entries["base_url"].insert(
                0, "https://token-plan.cn-beijing.maas.aliyuncs.com/apps/anthropic")
            panel.entries["api_key"].insert(0, "sk-sp-draft")
            dialog.provider_var.set(get_provider("volcengine").label)
            dialog._schedule_rebuild()
            pump(app)
            assert dialog.provider == "volcengine"
            # Non-secret choices landed in claude.json; the key is memory only.
            stashed = storage.load_claude_prefs("bailian")
            assert stashed.custom_model == "my.custom-model"
            assert stashed.base_url.endswith(claude_code.ANTHROPIC_PATH)
            assert app._claude_key_drafts["bailian"] == "sk-sp-draft"
            volc_panel = dialog.claude_panel
            # The seeded Ark URL + drafted key trigger an auto-fetch; its
            # failure must keep the empty catalogue and the marker-only combo.
            pump(app, 300)
            assert storage.load_model_catalog("volcengine") == ()
            assert volc_panel.model_combo.cget("values") == (CUSTOM_MARKER,)
            assert "失败" in volc_panel.status_label.cget("text")
            assert any("未经验证" in text for text in label_texts(dialog.window))

            # Coming back reseeds from the draft: custom id and URL return,
            # while the secret stays out of the text field.
            dialog.provider_var.set(get_provider("bailian").label)
            dialog._schedule_rebuild()
            pump(app)
            panel = dialog.claude_panel
            assert panel.entries["claude_model"].get() == "my.custom-model"
            assert panel.entries["base_url"].get().endswith(claude_code.ANTHROPIC_PATH)
            assert panel.entries["api_key"].get() == ""
            assert app.stored_inference_key("bailian") == "sk-sp-draft"

            # A theme swap restyles the dialog without rebuilding the form.
            window_before = dialog.window
            app._set_theme("dark", persist=True)
            pump(app)
            assert dialog.window is window_before and dialog.claude_panel is panel
            app._set_theme("light", persist=True)
            pump(app)

            dialog.close()
            assert app.settings_window is None
            # Shutdown must not leave drafted inference keys in memory.
            assert app._claude_key_drafts.get("bailian") == "sk-sp-draft"
            app.shutdown()
            assert app._claude_key_drafts == {} and app._claude_drafts == {}
            assert not callback_errors, callback_errors
            print("PASS: claude write/backup/onboarding, key not echoed, "
                  "cancel and validation zero writes, provider drafts, "
                  "catalogue refresh, debounced auto-fetch and blocked "
                  "status, card write feedback and busy state, unexpected "
                  "exception containment, theme restyle, shutdown key cleanup")
        finally:
            app.shutdown()


if __name__ == "__main__":
    main()
