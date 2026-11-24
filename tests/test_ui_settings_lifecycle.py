"""Settings-dialog construction and retained-window lifecycle."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import token_plan_monitor.ui as ui_module
from token_plan_monitor.ui import MonitorUI


def bare_app() -> MonitorUI:
    app = MonitorUI.__new__(MonitorUI)
    app._settings_dialog = None
    app.settings_window = None
    app.settings_body = None
    return app


def test_open_settings_coalesces_reentry_focuses_and_reuses_hidden(monkeypatch):
    app = bare_app()
    created = []

    class Window(SimpleNamespace):
        def winfo_exists(self):
            return True

    class Dialog:
        def __init__(self, owner):
            self.owner = owner
            self.window = None
            self.body = SimpleNamespace()
            self.focus_calls = 0
            self.open_calls = 0
            self._opening = False
            self._presented = False
            created.append(self)

        def open(self):
            self._opening = True
            self.open_calls += 1
            try:
                if self.open_calls == 1:
                    # A native callback during construction must not create a
                    # second dialog or focus the half-built first one.
                    self.owner.open_settings()
                    assert len(created) == 1
                    assert self.focus_calls == 0
                self.window = Window()
                self._presented = True
                return self.window
            finally:
                self._opening = False

        def focus(self):
            self.focus_calls += 1

    monkeypatch.setattr(ui_module, "SettingsDialog", Dialog)
    app.open_settings()
    assert len(created) == 1
    assert app.settings_window is created[0].window
    assert app.settings_body is created[0].body

    app.open_settings()
    assert len(created) == 1
    assert created[0].open_calls == 1
    assert created[0].focus_calls == 1

    # close() clears the visible alias but retains the native window.
    dialog = created[0]
    app.settings_window = None
    app.settings_body = None
    dialog._presented = False
    app.open_settings()
    assert len(created) == 1
    assert dialog.open_calls == 2
    assert app.settings_window is dialog.window
    assert app.settings_body is dialog.body


def test_open_settings_disposes_a_failed_dialog(monkeypatch):
    app = bare_app()
    dialog = None

    class Dialog:
        def __init__(self, owner):
            nonlocal dialog
            dialog = self
            self.owner = owner
            self.window = None
            self.body = None
            self.disposed = False
            self._opening = False

        def open(self):
            raise RuntimeError("construction failed")

        def dispose(self):
            self.disposed = True
            self.owner._settings_dialog = None
            self.owner.settings_window = None
            self.owner.settings_body = None

    monkeypatch.setattr(ui_module, "SettingsDialog", Dialog)
    with pytest.raises(RuntimeError, match="construction failed"):
        app.open_settings()
    assert dialog.disposed
    assert app._settings_dialog is None
    assert app.settings_window is None


READY_URL = "https://token-plan.maas.qianwenaiapi.com/apps/anthropic"


def _catalog_app(target):
    app = bare_app()
    app._catalog_inflight = set()
    app._catalog_auto_signatures = {}
    app._applying_claude = False
    app._target = target
    app.statuses = []
    app.starts = []
    app.catalog_fetch_target = lambda provider: app._target
    app.set_catalog_status = lambda provider, text, tone: app.statuses.append((text, tone))
    app._start_model_fetch = lambda provider, url, key: app.starts.append((provider, url, key))
    return app


def test_try_auto_fetch_reports_why_it_is_blocked_instead_of_staying_silent():
    app = _catalog_app((READY_URL, ""))
    app.try_auto_fetch_models("bailian")
    assert app.starts == []
    assert app.statuses == [("填写推理 API Key 后将自动获取模型列表", "warning")]

    app = _catalog_app(("https://host.example.com/api/v3", "sk-sp-x"))
    app.try_auto_fetch_models("bailian")
    assert app.starts == []
    assert app.statuses == [("该 Base URL 不支持在线获取模型", "warning")]


def test_try_auto_fetch_starts_once_per_signature_and_skips_duplicates():
    app = _catalog_app((READY_URL, "sk-sp-x"))
    app.try_auto_fetch_models("bailian")
    app.try_auto_fetch_models("bailian")
    assert len(app.starts) == 1
    assert app.starts[0] == ("bailian", READY_URL, "sk-sp-x")

    # A changed key is a new signature: one more fetch.
    app._target = (READY_URL, "sk-sp-new")
    app.try_auto_fetch_models("bailian")
    assert len(app.starts) == 2


def test_try_auto_fetch_guards_inflight_and_active_claude_write():
    app = _catalog_app((READY_URL, "sk-sp-x"))
    app._catalog_inflight.add("bailian")
    app.try_auto_fetch_models("bailian")
    assert app.starts == [] and app.statuses == []

    app._catalog_inflight.clear()
    app._applying_claude = True
    app.try_auto_fetch_models("bailian")
    assert app.starts == [] and app.statuses == []


def test_maybe_auto_fetch_bypasses_signature_dedup_on_every_presentation():
    app = _catalog_app((READY_URL, "sk-sp-x"))
    app.maybe_auto_fetch("bailian")
    app.maybe_auto_fetch("bailian")
    assert len(app.starts) == 2
