"""AppKit shell. All Cocoa objects are owned and mutated on the main thread."""
from __future__ import annotations

import logging
import queue
from dataclasses import replace

import AppKit as A
import Foundation as F
import Quartz  # registers CGColor / CALayer bridge metadata
from objc import super

from ..assets import make_tray_icon
from ..models import CARD_MIN_HEIGHT, CARD_MIN_WIDTH, QueryError, WINDOW_ORDER
from ..presentation import (
    WINDOW_LABELS, compact_remaining, format_datetime, format_number,
    hover_summary, menu_lines, state_tone, status_text,
)
from .layout import (
    FieldValidationError,
    clamp_card,
    fit_card_size,
    lowest_windows,
    primary_action,
    resize_card_frame,
    validate_configuration,
)

logger = logging.getLogger(__name__)
TITLE = "火山方舟用量监控"
TEXT = A.NSColor.colorWithCalibratedWhite_alpha_(0.15, 1)
SECONDARY = A.NSColor.colorWithCalibratedWhite_alpha_(0.28, 1)
WARNING = A.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.48, 0.29, 0.02, 1)
ERROR = A.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.66, 0.12, 0.09, 1)
SUCCESS = A.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.08, 0.37, 0.18, 1)
INFO = A.NSColor.colorWithCalibratedRed_green_blue_alpha_(0, 0.30, 0.63, 1)


def status_presentation(state):
    text = status_text(state)
    low = state.snapshot and any(w.remaining_percent < 10 for w in state.snapshot.windows)
    if low:
        text += " · 额度偏低"
    if state.status == "error":
        color = ERROR
    elif state.status == "stale" or low:
        color = WARNING
    elif state.is_refreshing:
        color = INFO
    elif state.status == "ok":
        color = SUCCESS
    else:
        color = SECONDARY
    return text, color


def rect(x, y, w, h):
    return F.NSMakeRect(x, y, w, h)


def alert(message, title=TITLE):
    panel = A.NSAlert.alloc().init()
    panel.setMessageText_(title)
    panel.setInformativeText_(message)
    panel.addButtonWithTitle_("好")
    A.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    panel.runModal()


def show_already_running():
    app = A.NSApplication.sharedApplication()
    app.setActivationPolicy_(A.NSApplicationActivationPolicyAccessory)
    alert("程序已在运行，请从菜单栏打开用量窗口。")


def text_label(parent, text, x, y, width, size=13, *, bold=False, color=None):
    view = A.NSTextField.wrappingLabelWithString_(text)
    font = A.NSFont.boldSystemFontOfSize_(size) if bold else A.NSFont.systemFontOfSize_(size)
    view.setFont_(font)
    view.setTextColor_(color or TEXT)
    bounds = view.attributedStringValue().boundingRectWithSize_options_(
        F.NSMakeSize(width, 100000),
        A.NSStringDrawingUsesLineFragmentOrigin | A.NSStringDrawingUsesFontLeading,
    )
    height = max(size + 6, bounds.size.height + 6)
    view.setFrame_(rect(x, y, width, height))
    parent.addSubview_(view)
    return view


def label(parent, text, x, y, width, size=13, *, bold=False, color=None):
    return text_label(parent, text, x, y, width, size, bold=bold, color=color).frame().size.height


def button(parent, title, x, y, width, target, command, *, key=None, view_type=None):
    view = (view_type or FocusButton).buttonWithTitle_target_action_(title, target, "menuAction:")
    view.setFrame_(rect(x, y, width, 32))
    view.setBezelStyle_(A.NSBezelStyleRounded)
    view.setIdentifier_(command)
    if key is not None:
        view.setKeyEquivalent_(key)
    parent.addSubview_(view)
    return view


def quota_background(parent, y, width, height):
    panel = A.NSView.alloc().initWithFrame_(rect(12, y, width, height))
    panel.setWantsLayer_(True)
    panel.layer().setCornerRadius_(12)
    panel.layer().setBackgroundColor_(A.NSColor.whiteColor().CGColor())
    panel.layer().setBorderWidth_(0.5)
    panel.layer().setBorderColor_(A.NSColor.colorWithCalibratedWhite_alpha_(0.85, 1).CGColor())
    parent.addSubview_positioned_relativeTo_(panel, A.NSWindowBelow, None)


class FlippedView(A.NSView):
    def isFlipped(self):
        return True


class FocusButton(A.NSButton):
    def acceptsFirstResponder(self):
        return True

    def needsPanelToBecomeKey(self):
        return True

    def keyDown_(self, event):
        if event.charactersIgnoringModifiers() in ("\r", " "):
            self.performClick_(None)
        else:
            super().keyDown_(event)


class CardRefreshButton(FocusButton):
    # The native rounded image-only bezel is about 20 points inside its 32-point frame.
    hit_inset = 6

    def hitFrame(self):
        return F.NSInsetRect(self.frame(), self.hit_inset, self.hit_inset)

    def toolTip(self):
        return getattr(self, "_compact_tool_tip", None)

    def setToolTip_(self, text):
        self._compact_tool_tip = text
        self.removeAllToolTips()
        if text:
            hit_bounds = F.NSInsetRect(self.bounds(), self.hit_inset, self.hit_inset)
            self.addToolTipRect_owner_userData_(hit_bounds, self, None)

    def view_stringForToolTip_point_userData_(self, view, tag, point, user_data):
        return self._compact_tool_tip


class CardPanel(A.NSPanel):
    def canBecomeKeyWindow(self):
        return True

    def cancelOperation_(self, sender):
        self.orderOut_(None)
        self.owner.hide_card()


class CardView(FlippedView):
    resize_margin = 8

    def hitTest_(self, point):
        # Buttons stay interactive, including while disabled; labels remain draggable.
        if not F.NSPointInRect(point, self.frame()):
            return None
        for child in reversed(list(self.subviews())):
            if not isinstance(child, A.NSButton) or child.isHidden():
                continue
            hit_frame = child.hitFrame() if isinstance(child, CardRefreshButton) else child.frame()
            if F.NSPointInRect(point, hit_frame):
                return child
        return self

    def acceptsFirstMouse_(self, event):
        return True

    def _mouse_location(self):
        return A.NSEvent.mouseLocation()

    def resizeSubviewsWithOldSize_(self, old_size):
        super().resizeSubviewsWithOldSize_(old_size)
        self.owner.layout_card()

    def _edges_at(self, point):
        size = self.bounds().size
        edges = set()
        if point.x <= self.resize_margin:
            edges.add("left")
        elif point.x >= size.width - self.resize_margin:
            edges.add("right")
        if point.y <= self.resize_margin:
            edges.add("top")
        elif point.y >= size.height - self.resize_margin:
            edges.add("bottom")
        return frozenset(edges)

    def resetCursorRects(self):
        super().resetCursorRects()
        size = self.bounds().size
        margin = self.resize_margin
        horizontal_height = max(0, size.height - margin * 2)
        vertical_width = max(0, size.width - margin * 2)
        self.addCursorRect_cursor_(rect(0, margin, margin, horizontal_height), A.NSCursor.resizeLeftRightCursor())
        self.addCursorRect_cursor_(rect(size.width - margin, margin, margin, horizontal_height), A.NSCursor.resizeLeftRightCursor())
        self.addCursorRect_cursor_(rect(margin, 0, vertical_width, margin), A.NSCursor.resizeUpDownCursor())
        self.addCursorRect_cursor_(rect(margin, size.height - margin, vertical_width, margin), A.NSCursor.resizeUpDownCursor())
        for corner in (
            rect(0, 0, margin, margin),
            rect(size.width - margin, 0, margin, margin),
            rect(0, size.height - margin, margin, margin),
            rect(size.width - margin, size.height - margin, margin, margin),
        ):
            self.addCursorRect_cursor_(corner, A.NSCursor.crosshairCursor())

    def mouseDown_(self, event):
        self.context_click = bool(event.modifierFlags() & A.NSEventModifierFlagControl)
        if self.context_click:
            self.rightMouseDown_(event)
            return
        self.press = self._mouse_location()
        frame = self.window().frame()
        self.origin = frame.origin
        self.initial_frame = (frame.origin.x, frame.origin.y, frame.size.width, frame.size.height)
        point = self.convertPoint_fromView_(event.locationInWindow(), None)
        self.resize_edges = self._edges_at(point)
        self.dragged = False

    def mouseDragged_(self, event):
        if getattr(self, "context_click", False):
            return
        point = self._mouse_location()
        dx, dy = point.x - self.press.x, point.y - self.press.y
        if not self.dragged and dx * dx + dy * dy < 16:
            return
        self.dragged = True
        if self.resize_edges:
            native_screen = self.window().screen()
            screen = None
            if native_screen is not None:
                visible = native_screen.visibleFrame()
                screen = (visible.origin.x, visible.origin.y, visible.size.width, visible.size.height)
            frame = resize_card_frame(self.initial_frame, dx, dy, self.resize_edges, screen)
            self.window().setFrame_display_(rect(*frame), True)
        else:
            self.window().setFrameOrigin_(F.NSMakePoint(self.origin.x + dx, self.origin.y + dy))

    def mouseUp_(self, event):
        if getattr(self, "context_click", False):
            return
        if getattr(self, "dragged", False):
            self.owner.clamp_and_save_card()
        self.resize_edges = frozenset()

    def rightMouseDown_(self, event):
        A.NSMenu.popUpContextMenu_withEvent_forView_(self.owner.make_menu(include_details=False), event, self)

    def otherMouseDown_(self, event):
        self.rightMouseDown_(event)


class Actions(F.NSObject):
    def statusClick_(self, sender):
        event = A.NSApplication.sharedApplication().currentEvent()
        if event.type() == A.NSEventTypeRightMouseUp or event.modifierFlags() & A.NSEventModifierFlagControl:
            menu = self.owner.make_menu()
            self.owner.status_item.popUpStatusItemMenu_(menu)
        else:
            self.owner.show_details()

    def menuAction_(self, sender):
        command = sender.representedObject() if isinstance(sender, A.NSMenuItem) else sender.identifier()
        self.owner.command(str(command))

    def windowDidResize_(self, notification):
        if notification.object() == self.owner.details:
            self.owner.layout_details()
            self.owner.render_details_document()

    def tick_(self, timer):
        self.owner.drain_events()

    def wake_(self, notification):
        self.owner.monitor.refresh_if_due()
        self.owner.position_card()

    def screenChanged_(self, notification):
        self.owner.position_card()

    def accessibilityChanged_(self, notification):
        self.owner.apply_materials()

    def applicationShouldTerminateAfterLastWindowClosed_(self, application):
        return False

    def applicationShouldHandleReopen_hasVisibleWindows_(self, application, visible):
        self.owner.show_details()
        return True

    def applicationWillTerminate_(self, notification):
        self.owner.shutdown()

    def windowShouldClose_(self, window):
        window.orderOut_(None)
        return False


class MonitorUI:
    def __init__(self, monitor, storage):
        self.monitor = monitor
        self.storage = storage
        self.app = A.NSApplication.sharedApplication()
        self.app.setActivationPolicy_(A.NSApplicationActivationPolicyAccessory)
        self.actions = Actions.alloc().init()
        self.actions.owner = self
        self.app.setDelegate_(self.actions)
        self.events = queue.SimpleQueue()
        self.monitor.on_state = self.events.put
        self.state = self.monitor.get_state()
        self.preferences = storage.load_widget_preferences()
        self.status_item = None
        self.details = None
        self.settings = None
        self.card = None
        self.timer = None
        self.closed = False
        self.materials = []
        self.status_image = None

    def run(self):
        self.setup()
        try:
            self.app.run()
        finally:
            self.shutdown()

    def setup(self):
        # An accessory app still needs a main menu for native text editing shortcuts.
        main = A.NSMenu.alloc().init()
        edit_item = A.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("编辑", None, "")
        edit = A.NSMenu.alloc().initWithTitle_("编辑")
        for title, selector, key in (("撤销", "undo:", "z"), ("剪切", "cut:", "x"),
                                      ("复制", "copy:", "c"), ("粘贴", "paste:", "v"),
                                      ("全选", "selectAll:", "a")):
            edit.addItemWithTitle_action_keyEquivalent_(title, selector, key)
        edit_item.setSubmenu_(edit)
        main.addItem_(edit_item)
        self.app.setMainMenu_(main)
        self.status_item = A.NSStatusBar.systemStatusBar().statusItemWithLength_(A.NSVariableStatusItemLength)
        status_button = self.status_item.button()
        status_button.setTarget_(self.actions)
        status_button.setAction_("statusClick:")
        status_button.sendActionOn_(A.NSEventMaskLeftMouseUp | A.NSEventMaskRightMouseUp)
        center = F.NSNotificationCenter.defaultCenter()
        center.addObserver_selector_name_object_(self.actions, "screenChanged:", A.NSApplicationDidChangeScreenParametersNotification, None)
        workspace = A.NSWorkspace.sharedWorkspace()
        workspace.notificationCenter().addObserver_selector_name_object_(
            self.actions, "wake:", A.NSWorkspaceDidWakeNotification, None)
        workspace.notificationCenter().addObserver_selector_name_object_(
            self.actions, "accessibilityChanged:", A.NSWorkspaceAccessibilityDisplayOptionsDidChangeNotification, None)
        self.create_card()
        self.monitor.start()
        self.render(self.monitor.get_state())
        self.timer = F.NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            0.1, self.actions, "tick:", None, True)
        F.NSRunLoop.mainRunLoop().addTimer_forMode_(self.timer, F.NSRunLoopCommonModes)
        if self.state.status == "unconfigured":
            self.open_settings()

    def window(self, title, width, height):
        style = A.NSWindowStyleMaskTitled | A.NSWindowStyleMaskClosable | A.NSWindowStyleMaskResizable
        window = A.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect(0, 0, width, height), style, A.NSBackingStoreBuffered, False)
        window.setTitle_(title)
        window.setReleasedWhenClosed_(False)
        window.setDelegate_(self.actions)
        window.setAppearance_(A.NSAppearance.appearanceNamed_(A.NSAppearanceNameAqua))
        window.setMinSize_(F.NSMakeSize(width, min(height, 360)))
        window.center()
        # Escape is a standard Cancel action; closing hides the reusable window.
        return window

    def activate(self, window):
        self.app.activateIgnoringOtherApps_(True)
        window.makeKeyAndOrderFront_(None)

    def make_menu(self, include_details=True):
        menu = A.NSMenu.alloc().initWithTitle_(TITLE)
        menu.setAutoenablesItems_(False)
        for line in menu_lines(self.state):
            item = menu.addItemWithTitle_action_keyEquivalent_(line, None, "")
            item.setEnabled_(False)
        menu.addItem_(A.NSMenuItem.separatorItem())
        entries = (("立即刷新", "refresh"), ("桌面卡片", "card"), ("显示模式", "display-mode"),
                   ("卡片置顶", "topmost"), ("设置…", "settings"), ("退出", "exit"))
        if include_details:
            entries = (("打开用量详情", "details"),) + entries
        for title, command in entries:
            item = menu.addItemWithTitle_action_keyEquivalent_(title, "menuAction:", "q" if command == "exit" else "")
            item.setTarget_(self.actions)
            item.setRepresentedObject_(command)
            if command == "display-mode":
                modes = A.NSMenu.alloc().initWithTitle_(title)
                modes.setAutoenablesItems_(False)
                for mode_title, mode_command, desktop in (
                    ("悬浮卡片", "floating-mode", False), ("桌面组件", "desktop-mode", True),
                ):
                    mode_item = modes.addItemWithTitle_action_keyEquivalent_(mode_title, "menuAction:", "")
                    mode_item.setTarget_(self.actions)
                    mode_item.setRepresentedObject_(mode_command)
                    mode_item.setState_(A.NSControlStateValueOn if self.preferences.desktop_mode == desktop else A.NSControlStateValueOff)
                item.setSubmenu_(modes)
            if command in ("card", "topmost"):
                checked = self.preferences.visible if command == "card" else self.preferences.topmost
                item.setState_(A.NSControlStateValueOn if checked else A.NSControlStateValueOff)
            if command == "topmost":
                item.setEnabled_(not self.preferences.desktop_mode)
        return menu

    def command(self, command):
        handlers = {"details": self.show_details, "refresh": self.monitor.request_refresh,
                    "card": self.toggle_card, "topmost": self.toggle_topmost,
                    "floating-mode": lambda: self.set_desktop_mode(False),
                    "desktop-mode": lambda: self.set_desktop_mode(True),
                    "settings": self.open_settings, "save": self.save_settings,
                    "check-credentials": self.check_credentials,
                    "credential-help": self.toggle_credential_help,
                    "cancel": lambda: self.settings.orderOut_(None),
                    "close-details": lambda: self.details.orderOut_(None),
                    "exit": lambda: self.app.terminate_(None)}
        if command in handlers:
            handlers[command]()

    def check_credentials(self):
        self.open_settings()
        self.settings.makeFirstResponder_(self.fields["ak"])

    def drain_events(self):
        latest = None
        while True:
            try:
                latest = self.events.get_nowait()
            except queue.Empty:
                break
        if latest is not None:
            self.render(latest)

    def render(self, state):
        self.state = state
        if self.status_item is not None:
            data = F.NSData.dataWithBytes_length_(*self.icon_data())
            image = A.NSImage.alloc().initWithData_(data)
            image.setSize_(F.NSMakeSize(20, 20))
            self.status_image = image
            self.status_item.button().setImage_(image)
            self.status_item.button().setToolTip_(hover_summary(state))
        self.render_card()
        if self.details is not None:
            self.render_details()

    def icon_data(self):
        import io
        output = io.BytesIO()
        make_tray_icon(state_tone(self.state)).save(output, format="PNG")
        data = output.getvalue()
        return data, len(data)

    def show_details(self):
        if self.details is None:
            self.details = self.window(TITLE, 480, 650)
        self.render_details()
        self.activate(self.details)

    def create_details_shell(self):
        root = FlippedView.alloc().initWithFrame_(self.details.contentView().bounds())
        root.setAutoresizingMask_(A.NSViewWidthSizable | A.NSViewHeightSizable)
        root.setWantsLayer_(True)
        root.layer().setBackgroundColor_(A.NSColor.colorWithCalibratedWhite_alpha_(0.96, 1).CGColor())
        self.details.setContentView_(root)
        self.details_root = root
        self.details_header = FlippedView.alloc().initWithFrame_(rect(0, 0, 480, 154))
        root.addSubview_(self.details_header)
        self.header_title = text_label(self.details_header, "火山方舟 · 套餐用量", 24, 14, 432, 20, bold=True)
        self.header_status = text_label(self.details_header, "", 24, 46, 432, 13, bold=True)
        self.header_updated = text_label(self.details_header, "", 24, 72, 432, 11, color=SECONDARY)
        self.header_reason = text_label(self.details_header, "", 24, 94, 432, 12)
        self.header_reason.setMaximumNumberOfLines_(2)
        self.header_reason.setLineBreakMode_(A.NSLineBreakByTruncatingTail)
        self.header_reason.setFrame_(rect(24, 94, 432, 48))
        self.details_scroll = A.NSScrollView.alloc().initWithFrame_(rect(0, 154, 480, 438))
        self.details_scroll.setHasVerticalScroller_(True)
        self.details_scroll.setBorderType_(A.NSNoBorder)
        self.details_scroll.setDrawsBackground_(False)
        root.addSubview_(self.details_scroll)
        self.details_footer = FlippedView.alloc().initWithFrame_(rect(0, 592, 480, 58))
        root.addSubview_(self.details_footer)
        self.primary_button = button(self.details_footer, "立即刷新", 18, 12, 124, self.actions, "refresh", key="\r")
        self.settings_button = button(self.details_footer, "设置…", 150, 12, 100, self.actions, "settings")
        self.close_button = button(self.details_footer, "关闭", 354, 12, 108, self.actions, "close-details", key="\x1b")
        self.primary_button.setNextKeyView_(self.settings_button)
        self.settings_button.setNextKeyView_(self.close_button)
        self.close_button.setNextKeyView_(self.primary_button)
        self.layout_details()

    def layout_details(self):
        if not hasattr(self, "details_root"):
            return
        size = self.details_root.bounds().size
        header_height = 148 if self.header_reason.stringValue() else 104
        self.details_header.setFrame_(rect(0, 0, size.width, header_height))
        self.details_scroll.setFrame_(rect(0, header_height, size.width, max(0, size.height - header_height - 58)))
        self.details_footer.setFrame_(rect(0, size.height - 58, size.width, 58))
        self.close_button.setFrameOrigin_(F.NSMakePoint(size.width - 126, 12))
        for view in (self.header_title, self.header_status, self.header_updated, self.header_reason):
            view.setFrameSize_(F.NSMakeSize(size.width - 48, view.frame().size.height))

    def render_details(self):
        if not hasattr(self, "details_root"):
            self.create_details_shell()
        text, color = status_presentation(self.state)
        self.header_status.setStringValue_(text)
        self.header_status.setTextColor_(color)
        snapshot = self.state.snapshot
        updated = "最近成功更新：" + format_datetime(snapshot.queried_at) if snapshot else "尚无成功查询记录"
        if snapshot and self.state.status == "stale":
            updated = "上次成功结果 · " + updated
        self.header_updated.setStringValue_(updated)
        reason = self.state.error or ("正在获取最新容量…" if self.state.is_refreshing else "")
        self.header_reason.setStringValue_(reason)
        self.header_reason.setTextColor_(ERROR if self.state.status == "error" else WARNING if reason else SECONDARY)
        self.header_reason.setToolTip_(reason or None)
        self.layout_details()
        title, command = primary_action(self.state)
        self.primary_button.setTitle_("正在刷新…" if self.state.is_refreshing and command == "refresh" else title)
        self.primary_button.setIdentifier_(command)
        self.primary_button.setEnabled_(not (self.state.is_refreshing and command == "refresh"))
        # Keep a retry available when credentials are the primary recovery action.
        self.settings_button.setTitle_("重试" if command == "check-credentials" and self.state.status != "unconfigured" else "设置…")
        self.settings_button.setIdentifier_("refresh" if command == "check-credentials" and self.state.status != "unconfigured" else "settings")
        self.settings_button.setEnabled_(not (self.state.is_refreshing and self.settings_button.identifier() == "refresh"))
        self.render_details_document()

    def render_details_document(self):
        if not hasattr(self, "details_scroll"):
            return
        origin = self.details_scroll.contentView().bounds().origin
        viewport_width = self.details_scroll.contentView().bounds().size.width
        content_width = max(1, viewport_width - 24)
        width = max(1, content_width - 48)
        content = FlippedView.alloc().initWithFrame_(rect(0, 0, content_width, 438))
        y = 12
        snapshot = self.state.snapshot
        reason = self.header_reason.stringValue()
        if snapshot:
            y += label(content, snapshot.plan_name or snapshot.plan_type, 24, y, width, 16, bold=True) + 12
            windows = {window.kind: window for window in snapshot.windows}
            for kind in WINDOW_ORDER:
                start_y = y
                y += 8
                window = windows.get(kind)
                y += label(content, WINDOW_LABELS[kind], 24, y, width, 14, bold=True)
                if window is None:
                    y += label(content, "暂无数据", 24, y, width, color=SECONDARY) + 14
                else:
                    y += label(content, f"剩余 {window.remaining_percent:.1f}%", 24, y, width, 18, bold=True) + 2
                    progress = A.NSProgressIndicator.alloc().initWithFrame_(rect(24, y, width, 10))
                    progress.setStyle_(A.NSProgressIndicatorStyleBar)
                    progress.setIndeterminate_(False)
                    progress.setMinValue_(0)
                    progress.setMaxValue_(100)
                    progress.setDoubleValue_(max(0, min(100, window.remaining_percent)))
                    progress.setAccessibilityLabel_(f"{WINDOW_LABELS[kind]}剩余容量")
                    progress.setAccessibilityValueDescription_(f"剩余 {window.remaining_percent:.1f}%")
                    content.addSubview_(progress)
                    y += 19
                    text = f"已用 {window.used_percent:.1f}%"
                    if window.quota is not None:
                        text += f" · AFP {format_number(window.used)} / {format_number(window.quota)} · 剩余 {format_number(window.remaining_absolute)}"
                    y += label(content, text, 24, y, width, 12, color=SECONDARY)
                    y += label(content, "重置时间：" + (format_datetime(window.reset_at) if window.reset_at else "—"), 24, y, width, 11, color=SECONDARY) + 10
                quota_background(content, start_y, max(1, content_width - 24), y - start_y - 4)
                y += 6
        else:
            text = "请先连接账号，填写 AK/SK 以查询套餐容量。" if self.state.status == "unconfigured" else "尚未获取套餐用量，请使用下方操作恢复查询。"
            y += label(content, text, 24, y, width, 14) + 16
        # Long errors remain fully readable without expanding the fixed header.
        if len(reason) > 60 or "\n" in reason:
            y += label(content, "完整错误说明", 24, y, width, 13, bold=True) + 6
            y += label(content, reason, 24, y, width, 12, color=SECONDARY) + 12
        content.setFrameSize_(F.NSMakeSize(content_width, y + 12))
        self.details_scroll.setDocumentView_(content)
        max_y = max(0, content.frame().size.height - self.details_scroll.contentView().bounds().size.height)
        self.details_scroll.contentView().scrollToPoint_(F.NSMakePoint(0, min(origin.y, max_y)))
        self.details_scroll.reflectScrolledClipView_(self.details_scroll.contentView())

    def create_card(self):
        self.card = CardPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect(0, 0, self.preferences.width, self.preferences.height),
            A.NSWindowStyleMaskBorderless | A.NSWindowStyleMaskNonactivatingPanel,
            A.NSBackingStoreBuffered, False)
        self.card.owner = self
        self.card.setReleasedWhenClosed_(False)
        self.card.setOpaque_(False)
        self.card.setBackgroundColor_(A.NSColor.clearColor())
        self.card.setHasShadow_(True)
        self.card.setHidesOnDeactivate_(False)
        self.card.setBecomesKeyOnlyIfNeeded_(False)
        self.card.setContentMinSize_(F.NSMakeSize(CARD_MIN_WIDTH, CARD_MIN_HEIGHT))
        self.card.setAppearance_(A.NSAppearance.appearanceNamed_(A.NSAppearanceNameAqua))
        self.card_view = CardView.alloc().initWithFrame_(rect(0, 0, self.preferences.width, self.preferences.height))
        self.card_view.owner = self
        self.card.setContentView_(self.card_view)
        self.position_card()
        self.sync_card_visibility()

    def layout_card(self):
        if not hasattr(self, "card_values"):
            return
        size = self.card_view.bounds().size
        margin, gutter = 18, 8
        column_width = max(1, (size.width - margin * 2 - gutter * 2) / 3)
        self.card_title.setFrame_(rect(margin, 12, 92, self.card_title.frame().size.height))
        self.card_status.setFrame_(rect(118, 14, max(1, size.width - 136), self.card_status.frame().size.height))
        for index, kind in enumerate(WINDOW_ORDER):
            x = margin + index * (column_width + gutter)
            self.card_titles[kind].setFrame_(rect(x, 46, column_width, self.card_titles[kind].frame().size.height))
            self.card_values[kind].setFrame_(rect(x, 67, column_width, self.card_values[kind].frame().size.height))
        footer_width = max(1, size.width - margin * 2 - 44)
        self.card_updated.setFrame_(rect(margin, size.height - 52, footer_width, self.card_updated.frame().size.height))
        self.card_hint.setFrame_(rect(margin, size.height - 25, footer_width, self.card_hint.frame().size.height))
        self.card_refresh_button.setFrame_(rect(size.width - 44, size.height - 44, 32, 32))

    def render_card(self):
        if self.card is None:
            return
        if not hasattr(self, "card_values"):
            effect = A.NSVisualEffectView.alloc().initWithFrame_(self.card_view.bounds())
            effect.setAutoresizingMask_(A.NSViewWidthSizable | A.NSViewHeightSizable)
            effect.setMaterial_(A.NSVisualEffectMaterialPopover)
            effect.setBlendingMode_(A.NSVisualEffectBlendingModeBehindWindow)
            effect.setWantsLayer_(True)
            effect.layer().setCornerRadius_(16)
            effect.layer().setMasksToBounds_(True)
            self.card_view.addSubview_(effect)
            self.materials = [effect]
            self.card_title = text_label(self.card_view, "火山方舟", 18, 12, 92, 14, bold=True)
            self.card_status = text_label(self.card_view, "", 118, 14, 248, 11)
            self.card_titles, self.card_values = {}, {}
            for i, kind in enumerate(WINDOW_ORDER):
                x = 18 + i * 120
                self.card_titles[kind] = text_label(self.card_view, "", x, 46, 112, 11, color=SECONDARY)
                self.card_values[kind] = text_label(self.card_view, "—", x, 67, 112, 23, bold=True)
                self.card_values[kind].setAccessibilityLabel_(f"{WINDOW_LABELS[kind]}剩余容量")
            self.card_updated = text_label(self.card_view, "", 18, 108, 304, 11, color=SECONDARY)
            self.card_hint = text_label(self.card_view, "拖动移动 · 边缘/角落调整大小", 18, 135, 240, 11, color=SECONDARY)
            self.card_refresh_button = button(
                self.card_view, "", 340, 116, 32, self.actions, "refresh",
                key="\r", view_type=CardRefreshButton,
            )
            self.card_refresh_button.setImage_(A.NSImage.imageWithSystemSymbolName_accessibilityDescription_("arrow.clockwise", "立即刷新"))
            self.card_refresh_button.setImagePosition_(A.NSImageOnly)
            self.card_refresh_button.setAccessibilityLabel_("立即刷新")
            self.card_refresh_button.setToolTip_("立即刷新")
            self.card.setInitialFirstResponder_(self.card_refresh_button)
        text, color = status_presentation(self.state)
        self.card_status.setStringValue_(text)
        self.card_status.setTextColor_(color)
        lowest = lowest_windows(self.state)
        for kind in WINDOW_ORDER:
            self.card_titles[kind].setStringValue_(WINDOW_LABELS[kind] + "剩余" + (" · 最低" if kind in lowest else ""))
            value = compact_remaining(self.state, kind)
            self.card_values[kind].setStringValue_(value)
            self.card_values[kind].setAccessibilityValue_(value)
        snapshot = self.state.snapshot
        updated = ("上次成功：" if self.state.status == "stale" else "更新于：") + format_datetime(snapshot.queried_at) if snapshot else "尚无成功查询记录"
        self.card_updated.setStringValue_(updated)
        refreshing = self.state.is_refreshing
        self.card_refresh_button.setEnabled_(not refreshing)
        self.card_refresh_button.setAccessibilityLabel_("正在刷新" if refreshing else "立即刷新")
        self.card_refresh_button.setToolTip_("正在刷新…" if refreshing else "立即刷新")
        summary = text + "\n" + " · ".join(f"{WINDOW_LABELS[k]}剩余 {compact_remaining(self.state, k)}" for k in WINDOW_ORDER)
        self.card_view.setToolTip_(summary + "\n" + updated + "\n拖动移动；边缘或角落调整大小")
        self.layout_card()
        self.apply_materials()

    def apply_materials(self):
        reduced = A.NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceTransparency()
        for effect in self.materials:
            effect.setState_(A.NSVisualEffectStateInactive if reduced else A.NSVisualEffectStateActive)
            effect.layer().setBackgroundColor_((A.NSColor.windowBackgroundColor() if reduced else A.NSColor.clearColor()).CGColor())

    def position_card(self):
        if not self.card:
            return
        screens = [(s.visibleFrame().origin.x, s.visibleFrame().origin.y,
                    s.visibleFrame().size.width, s.visibleFrame().size.height) for s in A.NSScreen.screens()]
        size = self.card.frame().size
        width, height = fit_card_size(
            self.preferences.x, self.preferences.y, size.width, size.height, screens,
        )
        x, y = clamp_card(self.preferences.x, self.preferences.y, width, height, screens)
        self.card.setFrame_display_(rect(x, y, width, height), True)
        self.preferences = replace(self.preferences, x=x, y=y, width=width, height=height)

    def clamp_and_save_card(self):
        frame = self.card.frame()
        self.preferences = replace(
            self.preferences,
            x=round(frame.origin.x),
            y=round(frame.origin.y),
            width=round(frame.size.width),
            height=round(frame.size.height),
        )
        self.position_card()
        self.save_preferences()

    def save_preferences(self):
        try:
            self.storage.save_widget_preferences(self.preferences)
        except QueryError as exc:
            alert(exc.message)

    def sync_card_visibility(self):
        if self.preferences.desktop_mode:
            level = Quartz.CGWindowLevelForKey(Quartz.kCGDesktopIconWindowLevelKey) + 1
            behavior = (A.NSWindowCollectionBehaviorCanJoinAllSpaces
                        | A.NSWindowCollectionBehaviorStationary
                        | A.NSWindowCollectionBehaviorIgnoresCycle
                        | A.NSWindowCollectionBehaviorFullScreenNone)
        else:
            level = A.NSFloatingWindowLevel if self.preferences.topmost else A.NSNormalWindowLevel
            behavior = A.NSWindowCollectionBehaviorMoveToActiveSpace
        self.card.setLevel_(level)
        self.card.setCollectionBehavior_(behavior)
        if self.preferences.visible:
            self.card.orderFrontRegardless()
        else:
            self.card.orderOut_(None)

    def hide_card(self):
        self.preferences = replace(self.preferences, visible=False)
        self.sync_card_visibility()
        self.save_preferences()

    def toggle_card(self):
        self.preferences = replace(self.preferences, visible=not self.preferences.visible)
        self.sync_card_visibility()
        self.save_preferences()

    def toggle_topmost(self):
        if self.preferences.desktop_mode:
            return
        self.preferences = replace(self.preferences, topmost=not self.preferences.topmost)
        self.sync_card_visibility()
        self.save_preferences()

    def set_desktop_mode(self, desktop_mode):
        self.preferences = replace(self.preferences, desktop_mode=desktop_mode)
        self.sync_card_visibility()
        self.save_preferences()

    def open_settings(self):
        if self.settings is not None and self.settings.isVisible():
            self.activate(self.settings)
            return
        try:
            self.existing = self.storage.load_credentials()
        except QueryError as exc:
            self.existing = None
            alert(exc.message)
        current = self.storage.load_settings()
        if self.settings is None:
            self.settings = self.window("设置 · " + TITLE, 500, 490)
        content = FlippedView.alloc().initWithFrame_(rect(0, 0, 500, 490))
        content.setWantsLayer_(True)
        content.layer().setBackgroundColor_(A.NSColor.colorWithCalibratedWhite_alpha_(0.96, 1).CGColor())
        self.settings.setContentView_(content)
        y = 20
        y += label(content, "连接火山方舟", 24, y, 452, 22, bold=True) + 8
        y += label(content, "使用控制面账号 AK/SK，不是 sk-… 推理 Key。凭据仅存储在 macOS 钥匙串。", 24, y, 452, 12, color=SECONDARY) + 14
        self.help_expanded = False
        self.help_button = button(content, "凭据说明", 18, y, 116, self.actions, "credential-help")
        self.help_button.setImage_(A.NSImage.imageWithSystemSymbolName_accessibilityDescription_("chevron.right", None))
        self.help_button.setImagePosition_(A.NSImageLeft)
        self.help_button.setButtonType_(A.NSButtonTypeOnOff)
        self.help_button.setAccessibilityLabel_("展开凭据说明")
        y += 36
        self.fields = {}
        for key, title, value, secure in (
            ("ak", "AccessKey ID", self.existing.access_key_id if self.existing else "", False),
            ("sk", "Secret AccessKey（已保存，留空保持不变）" if self.existing else "Secret AccessKey（必填）", "", True),
            ("region", "Region", current.region, False),
            ("interval", "刷新间隔（分钟，5–1440）", str(current.refresh_minutes), False),
        ):
            y += label(content, title, 24, y, 452, 12) + 3
            field = (A.NSSecureTextField if secure else A.NSTextField).alloc().initWithFrame_(rect(24, y, 452, 28))
            field.setStringValue_(value)
            field.setFont_(A.NSFont.systemFontOfSize_(14))
            field.setAccessibilityLabel_(title)
            content.addSubview_(field)
            self.fields[key] = field
            y += 43
        self.setting_error = text_label(content, "", 24, y, 452, 12, color=ERROR)
        self.setting_error.setFrameSize_(F.NSMakeSize(452, 38))
        y += 40
        self.help_text = text_label(content,
            "在火山引擎账号的访问密钥管理中获取 AK/SK，并确保有套餐用量查询权限。"
            "它们不是 sk-… 推理 Key；凭据只保存在本机登录钥匙串。\n"
            "Region 默认 cn-beijing，请按使用区域填写。刷新间隔支持 5–1440 分钟。",
            24, y, 452, 12, color=SECONDARY)
        self.help_text.setHidden_(True)
        self.settings_buttons_y = y
        self.cancel_button = button(content, "取消", 252, y, 100, self.actions, "cancel", key="\x1b")
        self.save_button = button(content, "保存设置", 364, y, 112, self.actions, "save", key="\r")
        previous = None
        for field in self.fields.values():
            if previous is not None:
                previous.setNextKeyView_(field)
            previous = field
        previous.setNextKeyView_(self.help_button)
        self.help_button.setNextKeyView_(self.cancel_button)
        self.cancel_button.setNextKeyView_(self.save_button)
        self.save_button.setNextKeyView_(self.fields["ak"])
        self.settings.setContentMinSize_(F.NSMakeSize(500, y + 56))
        self.settings.setContentSize_(F.NSMakeSize(500, y + 56))
        self.activate(self.settings)
        self.settings.makeFirstResponder_(self.fields["sk" if self.existing else "ak"])

    def toggle_credential_help(self):
        self.help_expanded = not self.help_expanded
        self.help_text.setHidden_(not self.help_expanded)
        self.help_button.setState_(A.NSControlStateValueOn if self.help_expanded else A.NSControlStateValueOff)
        self.help_button.setImage_(A.NSImage.imageWithSystemSymbolName_accessibilityDescription_("chevron.down" if self.help_expanded else "chevron.right", None))
        self.help_button.setAccessibilityLabel_("收起凭据说明" if self.help_expanded else "展开凭据说明")
        extra = self.help_text.frame().size.height + 12 if self.help_expanded else 0
        y = self.settings_buttons_y + extra
        self.cancel_button.setFrameOrigin_(F.NSMakePoint(252, y))
        self.save_button.setFrameOrigin_(F.NSMakePoint(364, y))
        self.settings.setContentMinSize_(F.NSMakeSize(500, y + 56))
        self.settings.setContentSize_(F.NSMakeSize(500, y + 56))

    def save_settings(self):
        try:
            settings, credentials = validate_configuration(
                *(str(self.fields[key].stringValue()) for key in ("ak", "sk", "region", "interval")), self.existing)
            self.storage.save_credentials(credentials)
            self.storage.save_settings(settings)
        except FieldValidationError as exc:
            self.setting_error.setStringValue_(exc.message)
            self.settings.makeFirstResponder_(self.fields[exc.field])
            return
        except QueryError as exc:
            self.setting_error.setStringValue_(exc.message)
            return
        self.monitor.update_configuration(settings, credentials)
        self.settings.orderOut_(None)
        self.show_details()

    def shutdown(self):
        if self.closed:
            return
        self.closed = True
        if self.timer:
            self.timer.invalidate()
        self.monitor.on_state = None
        self.monitor.stop()
        F.NSNotificationCenter.defaultCenter().removeObserver_(self.actions)
        A.NSWorkspace.sharedWorkspace().notificationCenter().removeObserver_(self.actions)
        for window in (self.details, self.settings, self.card):
            if window:
                window.orderOut_(None)
        if self.status_item:
            A.NSStatusBar.systemStatusBar().removeStatusItem_(self.status_item)
            self.status_item = None
