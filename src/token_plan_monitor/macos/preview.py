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
import Quartz
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
    # Bailian Token Plan is metered monthly and reports usage ratios only, so
    # there are no absolute quota numbers to render.
    bailian_token = MonitorViewState(status="ok", provider="bailian", snapshot=UsageSnapshot(
        "token_plan", now,
        (QuotaWindow("monthly", 37.5, now + timedelta(days=12)),),
        "Token Plan 个人版"))
    # Bailian Coding Plan reports absolute Credits per window.
    bailian_coding = MonitorViewState(status="ok", provider="bailian", snapshot=UsageSnapshot(
        "coding_plan", now, tuple(
            QuotaWindow(kind, percent, now + timedelta(hours=4), 1000, percent * 10)
            for kind, percent in (("five_hour", 28), ("weekly", 64), ("monthly", 81))),
        "Coding Plan Standard"))
    return {
        "agent": ok,
        "bailian_token": bailian_token,
        "bailian_coding": bailian_coding,
        "bailian_unconfigured": MonitorViewState(status="unconfigured", provider="bailian"),
        "bailian_error": MonitorViewState(
            status="error", provider="bailian",
            error="AK/SK 鉴权失败，请检查凭据、权限和系统时间。", error_category="authentication"),
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
        self.refresh_count = 0
    def start(self):
        pass
    def stop(self):
        pass
    def get_state(self):
        return self.state
    def request_refresh(self):
        self.refresh_count += 1
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


class FakeMouseEvent:
    def __init__(self, location):
        self.location = location

    def modifierFlags(self):
        return 0

    def locationInWindow(self):
        return self.location


def check_card_modes(ui, storage):
    desktop_level = Quartz.CGWindowLevelForKey(Quartz.kCGDesktopIconWindowLevelKey) + 1
    desktop_behavior = (A.NSWindowCollectionBehaviorCanJoinAllSpaces
                        | A.NSWindowCollectionBehaviorStationary
                        | A.NSWindowCollectionBehaviorIgnoresCycle
                        | A.NSWindowCollectionBehaviorFullScreenNone)
    # Exercise the menu command route for both prior floating preferences.
    for topmost in (False, True):
        ui.preferences = replace(ui.preferences, topmost=topmost)
        ui.command("floating-mode")
        origin = ui.card.frame().origin
        for visible in (False, True):
            if ui.preferences.visible != visible:
                ui.toggle_card()
            ui.command("desktop-mode")
            assert ui.card.level() == desktop_level < A.NSNormalWindowLevel
            assert ui.card.collectionBehavior() == desktop_behavior
            assert ui.card.isVisible() == visible
            assert ui.card.frame().origin == origin
            menu = ui.make_menu()
            assert not menu.itemWithTitle_("卡片置顶").isEnabled()
            assert menu.itemWithTitle_("卡片置顶").state() == int(topmost)
            modes = menu.itemWithTitle_("显示模式").submenu()
            assert modes.itemWithTitle_("桌面组件").state() == A.NSControlStateValueOn
            assert modes.itemWithTitle_("悬浮卡片").state() == A.NSControlStateValueOff
            ui.toggle_topmost()
            assert ui.preferences.topmost == topmost
            assert ui.card.level() == desktop_level
            # Button-driven background updates and opening details must leave the card at desktop level.
            ui.card_refresh_button.performClick_(None)
            ui.drain_events()
            ui.command("details")
            assert ui.details.isVisible()
            assert ui.card.level() == desktop_level
            assert ui.card.isVisible() == visible
            assert storage.load_widget_preferences() == ui.preferences
            # Restore a new native card from disk as on application restart.
            restored = MonitorUI(FakeMonitor(ui.state), Storage(storage.data_dir, MemoryKeyring()))
            try:
                restored.create_card()
                assert restored.preferences == ui.preferences
                assert restored.card.level() == desktop_level
                assert restored.card.collectionBehavior() == desktop_behavior
                assert restored.card.isVisible() == visible
                assert restored.card.frame().origin == origin
                assert restored.card.frame().size.width == ui.card.frame().size.width
                assert restored.card.frame().size.height == ui.card.frame().size.height
            finally:
                restored.card.orderOut_(None)
                restored.card.close()
                ui.app.setDelegate_(ui.actions)
            ui.command("floating-mode")
            assert ui.preferences.topmost == topmost
            assert ui.card.level() == (A.NSFloatingWindowLevel if topmost else A.NSNormalWindowLevel)
            assert ui.card.collectionBehavior() == A.NSWindowCollectionBehaviorMoveToActiveSpace
            assert ui.card.isVisible() == visible
            assert ui.card.frame().origin == origin
            menu = ui.make_menu()
            assert menu.itemWithTitle_("卡片置顶").isEnabled()
            modes = menu.itemWithTitle_("显示模式").submenu()
            assert modes.itemWithTitle_("悬浮卡片").state() == A.NSControlStateValueOn
            assert modes.itemWithTitle_("桌面组件").state() == A.NSControlStateValueOff
    # Keep desktop mode active through movement, hiding, and a final reload.
    ui.command("desktop-mode")
    ui.card.setFrameOrigin_(F.NSMakePoint(origin.x - 20, origin.y - 20))
    ui.clamp_and_save_card()
    assert storage.load_widget_preferences() == ui.preferences
    ui.hide_card()
    assert not ui.card.isVisible()
    ui.toggle_card()
    assert ui.card.isVisible() and ui.card.level() == desktop_level
    assert storage.load_widget_preferences() == ui.preferences


def card_hit_test(ui, point):
    native_point = ui.card_view.convertPoint_toView_(point, ui.card_view.superview())
    return ui.card_view.hitTest_(native_point)


def card_refresh_probe_points(ui):
    button = ui.card_refresh_button
    frame = button.frame()
    hit = button.hitFrame()
    x, y, width, height = hit.origin.x, hit.origin.y, hit.size.width, hit.size.height
    cx, cy = x + width / 2, y + height / 2
    inside = [
        F.NSMakePoint(cx, cy),
        F.NSMakePoint(x + 0.5, cy), F.NSMakePoint(x + width - 0.5, cy),
        F.NSMakePoint(cx, y + 0.5), F.NSMakePoint(cx, y + height - 0.5),
    ]
    outside = [
        F.NSMakePoint(x - 0.5, cy), F.NSMakePoint(x + width + 0.5, cy),
        F.NSMakePoint(cx, y - 0.5), F.NSMakePoint(cx, y + height + 0.5),
    ]
    # The old implementation exposed this vertically mirrored area instead of the visible button.
    card_height = ui.card_view.bounds().size.height
    outside.append(F.NSMakePoint(cx, card_height - cy))
    # All four transparent corners remain inside the native frame but outside the bezel.
    for px in (frame.origin.x + 1, frame.origin.x + frame.size.width - 1):
        for py in (frame.origin.y + 1, frame.origin.y + frame.size.height - 1):
            outside.append(F.NSMakePoint(px, py))
    fx, fy = frame.origin.x, frame.origin.y
    fw, fh = frame.size.width, frame.size.height
    fcx, fcy = fx + fw / 2, fy + fh / 2
    outside.extend((
        F.NSMakePoint(fx - 0.5, fcy), F.NSMakePoint(fx + fw + 0.5, fcy),
        F.NSMakePoint(fcx, fy - 0.5), F.NSMakePoint(fcx, fy + fh + 0.5),
    ))
    for label in (ui.card_updated, ui.card_hint):
        bounds = label.frame()
        outside.append(F.NSMakePoint(
            bounds.origin.x + bounds.size.width / 2,
            bounds.origin.y + bounds.size.height / 2,
        ))
    return inside, outside


def assert_card_refresh_layout(ui):
    button = ui.card_refresh_button
    frame = button.frame()
    hit = button.hitFrame()
    size = ui.card_view.bounds().size
    assert (frame.size.width, frame.size.height) == (32, 32)
    assert (hit.size.width, hit.size.height) == (20, 20)
    assert (hit.origin.x, hit.origin.y) == (frame.origin.x + 6, frame.origin.y + 6)
    assert round(size.width - frame.origin.x - frame.size.width) == 12
    assert round(size.height - frame.origin.y - frame.size.height) == 12
    inside, outside = card_refresh_probe_points(ui)
    for point in inside:
        assert card_hit_test(ui, point) == button
    for point in outside:
        assert card_hit_test(ui, point) == ui.card_view
    native_center = ui.card_view.convertPoint_toView_(inside[0], ui.card_view.superview())
    assert native_center.y != inside[0].y
    assert ui.card_view.hitTest_(native_center) == button
    right_resize = F.NSMakePoint(size.width - 2, hit.origin.y + hit.size.height / 2)
    bottom_resize = F.NSMakePoint(hit.origin.x + hit.size.width / 2, size.height - 2)
    assert card_hit_test(ui, right_resize) == ui.card_view
    assert ui.card_view._edges_at(right_resize) == frozenset({"right"})
    assert card_hit_test(ui, bottom_resize) == ui.card_view
    assert ui.card_view._edges_at(bottom_resize) == frozenset({"bottom"})
    corner = F.NSMakePoint(size.width - 2, size.height - 2)
    assert card_hit_test(ui, corner) == ui.card_view
    assert ui.card_view._edges_at(corner) == frozenset({"right", "bottom"})
    for label in (ui.card_updated, ui.card_hint):
        assert label.frame().origin.x + label.frame().size.width <= frame.origin.x
    expected_tip = "正在刷新…" if not button.isEnabled() else "立即刷新"
    assert button.toolTip() == expected_tip
    assert button.view_stringForToolTip_point_userData_(
        button, 0, F.NSMakePoint(0, 0), None,
    ) == expected_tip
    return inside, outside


def click_card_at(ui, point):
    target = card_hit_test(ui, point)
    if target == ui.card_refresh_button:
        target.performClick_(None)
    else:
        assert target == ui.card_view
        event = FakeMouseEvent(ui.card_view.convertPoint_toView_(point, None))
        target.mouseDown_(event)
        target.mouseUp_(event)


def assert_card_refresh_activation(ui, should_refresh):
    inside, outside = assert_card_refresh_layout(ui)
    refresh_count = ui.monitor.refresh_count
    frame = ui.card.frame()
    for point in outside:
        click_card_at(ui, point)
    assert ui.monitor.refresh_count == refresh_count
    assert ui.card.frame().origin == frame.origin and ui.card.frame().size == frame.size
    click_card_at(ui, inside[0])
    assert ui.monitor.refresh_count == refresh_count + int(should_refresh)
    assert ui.card.frame().origin == frame.origin and ui.card.frame().size == frame.size
    assert not ui.details.isVisible()


def assert_details_width(ui):
    document = ui.details_scroll.documentView()
    expected_width = max(1, ui.details_scroll.contentView().bounds().size.width - 24)
    assert abs(document.frame().size.width - expected_width) < 0.01
    content_width = max(1, expected_width - 48)
    for view in document.subviews():
        if isinstance(view, (A.NSTextField, A.NSProgressIndicator)):
            assert abs(view.frame().size.width - content_width) < 0.01
        else:
            assert abs(view.frame().size.width - max(1, expected_width - 24)) < 0.01


def check_card_interactions_and_resize(ui, storage):
    assert not hasattr(ui, "card_menu_button")
    assert not hasattr(ui, "card_details_button")
    assert ui.make_menu().itemWithTitle_("打开用量详情") is not None
    card_menu = ui.make_menu(include_details=False)
    assert card_menu.itemWithTitle_("打开用量详情") is None
    assert card_menu.itemWithTitle_("立即刷新") is not None
    assert card_menu.itemWithTitle_("显示模式") is not None
    ui.details.orderOut_(None)
    assert_card_refresh_activation(ui, should_refresh=True)
    state = ui.state
    ui.render(replace(state, is_refreshing=True))
    assert not ui.card_refresh_button.isEnabled()
    assert ui.card_refresh_button.accessibilityLabel() == "正在刷新"
    assert_card_refresh_activation(ui, should_refresh=False)
    ui.render(state)
    assert ui.card_refresh_button.isEnabled()
    assert ui.card_refresh_button.accessibilityLabel() == "立即刷新"
    assert not ui.details.isVisible()
    frame = ui.card.frame()
    current = [F.NSMakePoint(frame.origin.x + 180, frame.origin.y + 80)]
    ui.card_view._mouse_location = lambda: current[0]
    event = FakeMouseEvent(F.NSMakePoint(180, 80))
    ui.card_view.mouseDown_(event)
    ui.card_view.mouseUp_(event)
    assert not ui.details.isVisible()
    for point in (F.NSMakePoint(350, 20), F.NSMakePoint(300, 140), F.NSMakePoint(30, 130)):
        assert card_hit_test(ui, point) == ui.card_view
    assert ui.card_view._edges_at(F.NSMakePoint(0, 0)) == frozenset({"left", "top"})
    assert ui.card_view._edges_at(F.NSMakePoint(383, 159)) == frozenset({"right", "bottom"})
    assert ui.card_view._edges_at(F.NSMakePoint(180, 80)) == frozenset()
    assert ui.card.contentMinSize().width == 320
    assert ui.card.contentMinSize().height == 160

    # Drive the production press/drag/release path from the bottom-right corner.
    visible = ui.card.screen().visibleFrame()
    ui.card.setFrameOrigin_(F.NSMakePoint(
        visible.origin.x + (visible.size.width - frame.size.width) / 2,
        visible.origin.y + (visible.size.height - frame.size.height) / 2,
    ))
    frame = ui.card.frame()
    width = min(520, round(visible.size.width))
    height = min(240, round(visible.size.height))
    current[0] = F.NSMakePoint(frame.origin.x + frame.size.width - 2, frame.origin.y + 2)
    event = FakeMouseEvent(F.NSMakePoint(frame.size.width - 2, 2))
    ui.card_view.mouseDown_(event)
    assert ui.card_view.resize_edges == frozenset({"right", "bottom"})
    current[0] = F.NSMakePoint(current[0].x + width - frame.size.width, current[0].y - height + frame.size.height)
    ui.card_view.mouseDragged_(event)
    ui.card_view.mouseUp_(event)
    resized = ui.card.frame()
    assert round(resized.size.width) == width
    assert round(resized.size.height) == height

    # A center drag still moves rather than resizes, and release persists both geometry fields.
    current[0] = F.NSMakePoint(resized.origin.x + resized.size.width / 2, resized.origin.y + resized.size.height / 2)
    event = FakeMouseEvent(F.NSMakePoint(resized.size.width / 2, resized.size.height / 2))
    ui.card_view.mouseDown_(event)
    assert ui.card_view.resize_edges == frozenset()
    current[0] = F.NSMakePoint(current[0].x + 30, current[0].y + 20)
    ui.card_view.mouseDragged_(event)
    ui.card_view.mouseUp_(event)
    expected = ui.card.frame()
    assert round(expected.origin.x - resized.origin.x) == 30
    assert round(expected.origin.y - resized.origin.y) == 20
    assert round(expected.size.width) == width and round(expected.size.height) == height
    assert storage.load_widget_preferences() == ui.preferences
    assert (ui.preferences.width, ui.preferences.height) == (round(expected.size.width), round(expected.size.height))
    column_widths = {round(view.frame().size.width, 2) for view in ui.card_values.values()}
    assert len(column_widths) == 1
    for view in list(ui.card_values.values()) + [ui.card_status, ui.card_updated, ui.card_hint]:
        assert view.frame().origin.x + view.frame().size.width <= ui.card_view.bounds().size.width + 0.01
    assert_card_refresh_layout(ui)
    restored = MonitorUI(FakeMonitor(ui.state), Storage(storage.data_dir, MemoryKeyring()))
    try:
        restored.create_card()
        assert restored.preferences == ui.preferences
        assert restored.card.frame().size.width == expected.size.width
        assert restored.card.frame().size.height == expected.size.height
    finally:
        restored.card.orderOut_(None)
        restored.card.close()
        ui.app.setDelegate_(ui.actions)


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
                    assert_details_width(ui)
                    assert_card_refresh_layout(ui)
                    assert ui.card_refresh_button.isEnabled() == (name != "refreshing")
                    assert ui.card_refresh_button.accessibilityLabel() == ("正在刷新" if name == "refreshing" else "立即刷新")
                    if name == "error":
                        assert ui.primary_button.title() == "检查凭据"
                    ui.details.setContentSize_(F.NSMakeSize(480, 338))
                    ui.layout_details()
                    ui.render_details_document()
                    assert_details_width(ui)
                    footer = ui.details_footer.frame()
                    assert footer.origin.y + footer.size.height <= ui.details_root.bounds().size.height
                    assert footer.origin.y >= ui.details_scroll.frame().origin.y + ui.details_scroll.frame().size.height
                    ui.details.displayIfNeeded()
                    capture(ui.details, args.artifacts / f"details-{name}-small.png")
                    ui.details.setContentSize_(F.NSMakeSize(720, 650))
                    ui.layout_details()
                    ui.render_details_document()
                    assert_details_width(ui)
                    ui.details.displayIfNeeded()
                    capture(ui.details, args.artifacts / f"details-{name}-wide.png")
                    ui.details.setContentSize_(F.NSMakeSize(480, 650))
                    ui.layout_details()
                    ui.render_details_document()
                    assert_details_width(ui)
                ui.render(states()["long"])
                ui.details_scroll.contentView().scrollToPoint_(F.NSMakePoint(0, 120))
                ui.details.makeFirstResponder_(ui.settings_button)
                before_focus = ui.details.firstResponder()
                ui.render(states()["long"])
                assert ui.details_scroll.contentView().bounds().origin.y == 120
                assert ui.details.firstResponder() == before_focus
                ui.details.setContentSize_(F.NSMakeSize(720, 650))
                ui.layout_details()
                ui.render_details_document()
                assert_details_width(ui)
                assert ui.details_scroll.contentView().bounds().origin.y == 120
                assert ui.details.firstResponder() == before_focus
                ui.details.setContentSize_(F.NSMakeSize(480, 650))
                ui.layout_details()
                ui.render_details_document()
                assert ui.details_scroll.contentView().bounds().origin.y == 120
                assert ui.details.firstResponder() == before_focus
                check_card_interactions_and_resize(ui, storage)
                ui.card.displayIfNeeded()
                capture(ui.card, args.artifacts / "card-resized.png")
                resized_frame = ui.card.frame()
                ui.card.setFrame_display_(F.NSMakeRect(
                    resized_frame.origin.x, resized_frame.origin.y, 320, 160,
                ), True)
                assert_card_refresh_layout(ui)
                ui.card.displayIfNeeded()
                capture(ui.card, args.artifacts / "card-minimum.png")
                ui.card.setFrame_display_(resized_frame, True)
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
                # Provider switching: rebuild is deferred, regions are revalidated
                # and each provider owns its own vault entries.
                ui.settings.orderOut_(None)
                ui.open_settings()
                assert [i.title() for i in ui.provider_popup.itemArray()] == ["火山方舟", "阿里云百炼"]
                assert ui.settings_provider == "volcengine"
                assert ui.fields["region"].stringValue() == "cn-beijing"
                ui.fields["region"].setStringValue_("cn-shanghai")
                ui.provider_popup.selectItemAtIndex_(1)
                ui.provider_changed()
                assert ui.settings_provider == "volcengine", "rebuild must not be synchronous"
                ui.flush_settings_rebuild()
                assert ui.settings_provider == "bailian"
                assert ui.fields["region"].stringValue() == "cn-beijing"
                assert ui.fields["ak"].accessibilityLabel() == "阿里云 AccessKey ID"
                assert ui.existing is None and ui.fields["ak"].stringValue() == ""
                capture(ui.settings, args.artifacts / "settings-bailian.png")
                ui.fields["ak"].setStringValue_("LTAI-preview")
                ui.fields["sk"].setStringValue_("bailian-secret")
                ui.save_settings()
                saved = storage.load_settings()
                assert (saved.provider, saved.region) == ("bailian", "cn-beijing")
                assert storage.load_credentials("bailian") == Credentials("LTAI-preview", "bailian-secret")
                # Saving Bailian credentials must not touch the Volcengine entries.
                assert {name for _, name in storage._keyring.values} == {
                    "bailian_access_key_id", "bailian_secret_access_key"}
                assert "bailian-secret" not in storage.config_path.read_text(encoding="utf-8")
                ui.toggle_card()
                assert not ui.card.isVisible()
                ui.toggle_card()
                assert ui.card.isVisible()
                ui.toggle_topmost()
                assert ui.card.level() == A.NSNormalWindowLevel
                check_card_modes(ui, storage)
                ui.preferences = replace(ui.preferences, x=-100000, y=-100000)
                ui.position_card()
                ui.clamp_and_save_card()
                assert storage.load_widget_preferences() == ui.preferences
                assert not ui.actions.windowShouldClose_(ui.details)
                assert not ui.details.isVisible()
                assert "winreg" not in __import__("sys").modules
                (args.artifacts / "smoke-result.txt").write_text(
                    f"PASS: {len(states())} fake states across both providers, default/small/wide responsive windows, remaining progress and AX labels, focus/scroll preservation, settings validation/help, card refresh button, click/move/resize gestures and no details buttons, geometry preferences, desktop/floating modes and menus, hidden/visible restart, shutdown\n"
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
