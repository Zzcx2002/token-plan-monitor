"""Isolated fake-data native preview and deterministic GUI smoke checks."""
from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import AppKit as A
import Foundation as F
from PyObjCTools import AppHelper

from ..models import Credentials, MonitorViewState, QuotaWindow, UsageSnapshot
from ..storage import Storage
from .ui import MonitorUI


class MemoryKeyring:
    def __init__(self):
        self.values = {}
    def get_password(self, service, username):
        return self.values.get((service, username))
    def set_password(self, service, username, value):
        self.values[service, username] = value


def states():
    now = datetime.now(UTC)
    snapshot = UsageSnapshot("agent_plan", now, tuple(
        QuotaWindow(kind, percent, now + timedelta(hours=4), 100, percent)
        for kind, percent in (("five_hour", 28), ("weekly", 64), ("monthly", 81))
    ), "Agent Plan Large")
    ok = MonitorViewState(status="ok", snapshot=snapshot)
    return {
        "agent": ok,
        "coding": replace(ok, snapshot=UsageSnapshot("coding_plan", now, tuple(
            QuotaWindow(w.kind, w.used_percent, w.reset_at) for w in snapshot.windows), "Coding Plan")),
        "refreshing": replace(ok, is_refreshing=True),
        "stale": replace(ok, status="stale", error="网络暂时不可用，请检查连接后重试。", error_category="network"),
        "unconfigured": MonitorViewState(status="unconfigured"),
        "error": MonitorViewState(status="error", error="AK/SK 鉴权失败，请检查凭据和系统时间。", error_category="authentication"),
        "long": replace(ok, status="stale", snapshot=replace(snapshot, plan_name="套餐名称用于验证自动换行 " * 12),
                        error="错误详情用于验证小屏幕下的滚动和完整显示。" * 30, error_category="network"),
        "limits": replace(ok, snapshot=replace(snapshot, windows=(
            QuotaWindow("five_hour", 0), QuotaWindow("weekly", 100), QuotaWindow("monthly", 95)))),
        "missing": replace(ok, snapshot=replace(snapshot, windows=(QuotaWindow("weekly", 40),))),
        "tied": replace(ok, snapshot=replace(snapshot, windows=tuple(QuotaWindow(k, 50) for k in ("five_hour", "weekly", "monthly")))),
    }


class FakeMonitor:
    def __init__(self, state):
        self.state = state
        self.on_state = None
    def start(self):
        pass
    def stop(self):
        pass
    def get_state(self):
        return self.state
    def request_refresh(self):
        if self.on_state:
            self.on_state(self.state)
    def refresh_if_due(self):
        pass
    def update_configuration(self, settings, credentials):
        self.request_refresh()


def capture(window, path):
    view = window.contentView()
    bitmap = view.bitmapImageRepForCachingDisplayInRect_(view.bounds())
    view.cacheDisplayInRect_toBitmapImageRep_(view.bounds(), bitmap)
    data = bitmap.representationUsingType_properties_(A.NSBitmapImageFileTypePNG, {})
    if not data.writeToFile_atomically_(str(path), True):
        raise RuntimeError(f"Could not save {path}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--state", choices=states(), default="agent")
    parser.add_argument("--artifacts", type=Path, default=Path("artifacts/macos"))
    args = parser.parse_args(argv)
    with TemporaryDirectory(prefix="token-monitor-preview-") as temporary:
        storage = Storage(Path(temporary), MemoryKeyring())
        storage.save_credentials(Credentials("preview-ak", "preview-secret"))
        ui = MonitorUI(FakeMonitor(states()[args.state]), storage)
        ui.setup()
        ui.show_details()
        failure = []
        def smoke():
            try:
                args.artifacts.mkdir(parents=True, exist_ok=True)
                if getattr(sys, "frozen", False):
                    import certifi
                    import keyring.backends.macOS as backend
                    import requests
                    import ssl
                    import objc
                    import PIL.Image
                    contents = Path(sys.executable).resolve().parents[1]
                    for module in (certifi, backend, requests, ssl, objc, PIL.Image, sys.modules[__name__]):
                        assert Path(module.__file__).resolve().is_relative_to(contents), module.__file__
                    assert Path(certifi.where()).is_file()
                    assert backend.Keyring.priority > 0
                for name, state in states().items():
                    ui.render(state)
                    ui.details.displayIfNeeded()
                    ui.card.displayIfNeeded()
                    capture(ui.details, args.artifacts / f"details-{name}.png")
                    capture(ui.card, args.artifacts / f"card-{name}.png")
                    assert ui.make_menu().numberOfItems() >= 8
                    progress = [v for v in ui.details_scroll.documentView().subviews() if isinstance(v, A.NSProgressIndicator)]
                    expected = {w.kind: w for w in state.snapshot.windows} if state.snapshot else {}
                    assert [v.doubleValue() for v in progress] == [max(0, min(100, expected[k].remaining_percent)) for k in ("five_hour", "weekly", "monthly") if k in expected]
                    assert all(v.accessibilityLabel().endswith("剩余容量") for v in progress)
                    assert all(ui.card_values[k].stringValue() == (f"{expected[k].remaining_percent:.1f}%" if k in expected else "—") for k in ("five_hour", "weekly", "monthly"))
                    if name == "error":
                        assert ui.primary_button.title() == "检查凭据"
                    ui.details.setContentSize_(F.NSMakeSize(480, 338))
                    ui.layout_details()
                    footer = ui.details_footer.frame()
                    assert footer.origin.y + footer.size.height <= ui.details_root.bounds().size.height
                    assert footer.origin.y >= ui.details_scroll.frame().origin.y + ui.details_scroll.frame().size.height
                    ui.details.displayIfNeeded()
                    capture(ui.details, args.artifacts / f"details-{name}-small.png")
                    ui.details.setContentSize_(F.NSMakeSize(480, 650))
                    ui.layout_details()
                ui.render(states()["long"])
                ui.details_scroll.contentView().scrollToPoint_(F.NSMakePoint(0, 120))
                ui.details.makeFirstResponder_(ui.settings_button)
                before_focus = ui.details.firstResponder()
                ui.render(states()["long"])
                assert ui.details_scroll.contentView().bounds().origin.y == 120
                assert ui.details.firstResponder() == before_focus
                assert ui.card_view.hitTest_(F.NSMakePoint(350, 20)) == ui.card_menu_button
                assert ui.card_view.hitTest_(F.NSMakePoint(300, 140)) == ui.card_details_button
                assert ui.card_view.hitTest_(F.NSMakePoint(30, 130)) == ui.card_view
                ui.open_settings()
                assert ui.settings.isVisible()
                capture(ui.settings, args.artifacts / "settings.png")
                ui.toggle_credential_help()
                assert not ui.help_text.isHidden()
                capture(ui.settings, args.artifacts / "settings-help.png")
                ui.toggle_credential_help()
                ui.fields["sk"].setStringValue_("")
                ui.save_settings()
                assert storage.load_credentials().secret_access_key == "preview-secret"
                storage._keyring.values.clear()
                ui.open_settings()
                assert ui.existing is None
                ui.save_settings()
                assert ui.setting_error.stringValue() == "请填写 AccessKey ID"
                assert ui.settings.firstResponder() == ui.fields["ak"].currentEditor()
                ui.fields["ak"].setStringValue_("test-ak")
                ui.save_settings()
                assert ui.fields["ak"].stringValue() == "test-ak"
                assert ui.setting_error.stringValue() == "首次连接请填写 Secret AccessKey"
                assert ui.settings.firstResponder() == ui.fields["sk"].currentEditor()
                capture(ui.settings, args.artifacts / "settings-first-use.png")
                ui.toggle_card()
                assert not ui.card.isVisible()
                ui.toggle_card()
                assert ui.card.isVisible()
                ui.toggle_topmost()
                assert ui.card.level() == A.NSNormalWindowLevel
                ui.preferences = replace(ui.preferences, x=-100000, y=-100000)
                ui.position_card()
                ui.clamp_and_save_card()
                assert storage.load_widget_preferences() == ui.preferences
                assert not ui.actions.windowShouldClose_(ui.details)
                assert not ui.details.isVisible()
                assert "winreg" not in __import__("sys").modules
                (args.artifacts / "smoke-result.txt").write_text(
                    "PASS: ten states, default/small windows, remaining progress and AX labels, focus/scroll preservation, settings validation/help, card hit testing/preferences, shutdown\n"
                    + ("PASS: bundled runtime, certificates and Keychain backend imports\n" if getattr(sys, "frozen", False) else ""))
                print("Native macOS smoke checks passed", flush=True)
            except BaseException as exc:
                import traceback
                traceback.print_exc()
                failure.append(exc)
            finally:
                ui.shutdown()
                ui.app.stop_(None)
                # Wake NSApplication.run so stop takes effect immediately.
                event = A.NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
                    A.NSEventTypeApplicationDefined, F.NSMakePoint(0, 0), 0, 0, 0, None, 0, 0, 0)
                ui.app.postEvent_atStart_(event, True)
        if args.smoke_test:
            AppHelper.callLater(0.5, smoke)
        try:
            ui.app.run()
        finally:
            ui.shutdown()
        if failure:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
