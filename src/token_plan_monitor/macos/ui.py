"""AppKit shell. All Cocoa objects are owned and mutated on the main thread."""
from __future__ import annotations

import logging
import os
import queue
import threading
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import AppKit as A
import Foundation as F
import Quartz  # registers CGColor / CALayer bridge metadata
from objc import python_method, super

from .. import claude_code
from ..assets import make_tray_icon
from ..claude_code import ClaudeConfigError
from ..model_catalog import fetch_model_ids, merge_model_options
from ..models import (
    CARD_MIN_HEIGHT,
    CARD_MIN_WIDTH,
    ClaudeProviderPrefs,
    DEFAULT_PROVIDER,
    PROVIDER_REGIONS,
    QueryError,
    WINDOW_ORDER,
)
from ..history_chart import (
    PLOT_HEIGHT, RANGE_KEYS, X_AXIS_HEIGHT, Y_AXIS_WIDTH, build_chart,
)
from ..presentation import (
    HISTORY_RANGE_LABELS, HISTORY_TABLE_COLLAPSE, HISTORY_TABLE_HEADERS,
    HISTORY_TABLE_TITLE, HISTORY_WINDOW_TITLE, WINDOW_LABELS, card_title,
    compact_remaining, display_remaining, format_datetime, format_number,
    history_absolute_header, history_chart_title, history_context_line,
    history_empty_text, hover_summary, menu_lines, state_tone, status_text,
    trend_line,
)
from ..providers import PROVIDERS, get_provider
from .layout import (
    CUSTOM_MODEL_MARKER,
    FieldValidationError,
    clamp_card,
    fit_card_size,
    lowest_windows,
    model_popup_index,
    model_popup_restore,
    model_popup_value,
    primary_action,
    resize_card_frame,
    resolve_claude_seed,
    validate_claude_configuration,
    validate_configuration,
)
from . import theme as T
from .chart import ChartView, HistoryTableDataSource
from .notifications import MacNotifier

logger = logging.getLogger(__name__)
# Provider-neutral app title; window titles that show quota data are prefixed
# with the active provider instead (see MonitorUI.app_title).
TITLE = "Token Plan 用量监控"
# Text colors are dynamic catalog colors, so labels assigned with these stay
# correct across an appearance switch without being rebuilt. Status *tones*
# cannot be constants (see theme's rule 3), so callers ask theme.tone().
TEXT = T.TEXT
SECONDARY = T.SECONDARY
# Card window level/behaviors, exported so the offline smoke asserts against
# the production values instead of re-inventing its own copy of the bitmasks.
DESKTOP_CARD_LEVEL = Quartz.CGWindowLevelForKey(Quartz.kCGDesktopIconWindowLevelKey) + 1
DESKTOP_CARD_BEHAVIOR = (A.NSWindowCollectionBehaviorCanJoinAllSpaces
                         | A.NSWindowCollectionBehaviorStationary
                         | A.NSWindowCollectionBehaviorIgnoresCycle
                         | A.NSWindowCollectionBehaviorFullScreenNone)
FLOATING_CARD_BEHAVIOR = A.NSWindowCollectionBehaviorMoveToActiveSpace


def status_presentation(state):
    """Status line text plus its tone color (a dynamic color, so it follows the
    system appearance wherever it is assigned)."""
    text = status_text(state)
    low = state.snapshot and any(
        w.remaining_percent < state.alert_threshold_percent for w in state.snapshot.windows
    )
    if low:
        text += " · 额度偏低"
    if state.status == "error":
        tone = "error"
    elif state.status == "stale" or low:
        tone = "warning"
    elif state.is_refreshing:
        tone = "info"
    elif state.status == "ok":
        tone = "ok"
    else:
        return text, SECONDARY
    return text, T.tone(tone)


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


def text_label(parent, text, x, y, width, size=None, *, bold=False, color=None,
               role=None, numeric=False):
    """A wrapping label, self-measured so the y-cursor layout knows its height.

    ``role`` selects a type-ramp entry from :mod:`theme`; ``size`` is still
    accepted because height is computed from the point size and most call sites
    read better written that way. ``bold`` now means *semibold*: true bold is
    heavier than any text on an Apple surface, and that heaviness was a large
    part of why the windows looked generic. ``numeric`` switches to tabular
    figures so percentages stop resizing themselves between refreshes.
    """
    size = T.role_size(role) if role is not None else (
        size if size is not None else T.role_size("body"))
    view = A.NSTextField.wrappingLabelWithString_(text)
    font = T.label_font(size, role, bold, numeric)
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


def label(parent, text, x, y, width, size=13, *, bold=False, color=None,
          role=None, numeric=False):
    return text_label(parent, text, x, y, width, size, bold=bold, color=color,
                      role=role, numeric=numeric).frame().size.height


# Settings window metrics. SETTINGS_WIDTH is also the *minimum* width: every
# wrapped label's height is measured at that width, so the window may grow wider
# but must never shrink below it, or text would clip without a re-measure.
SETTINGS_WIDTH = 500
SETTINGS_MIN_HEIGHT = 480
SETTINGS_MAX_HEIGHT = 640
SETTINGS_FOOTER_HEIGHT = 58
# History window: 600 is also the minimum width -- the 90-day x-axis labels
# are measured at this width, the same rule the settings form follows.
HISTORY_WIDTH = 600
HISTORY_HEIGHT = 640
HISTORY_MIN_HEIGHT = 400
HISTORY_HEADER_HEIGHT = 110
HISTORY_FOOTER_HEIGHT = 58
HISTORY_TABLE_ROW_HEIGHT = 20
HISTORY_TABLE_MAX_HEIGHT = 264
# Two wrapped 12pt lines plus padding: the pinned feedback band's resting size.
SETTINGS_ERROR_HEIGHT = 34


def _stretch(view):
    """Keep both side margins fixed; the view absorbs horizontal growth."""
    view.setAutoresizingMask_(A.NSViewWidthSizable)
    return view


def _pin_left(view):
    """Stay at its x; the flexible margin is on its right."""
    view.setAutoresizingMask_(A.NSViewMaxXMargin)
    return view


def _pin_right(view):
    """Ride the trailing edge; the flexible margin is on its left."""
    view.setAutoresizingMask_(A.NSViewMinXMargin)
    return view


def stretch_label(parent, text, y, width, size=13, *, bold=False, color=None,
                  role=None, numeric=False):
    """`label()` for views that must widen with the window. Returns its height."""
    return _stretch(text_label(parent, text, T.PAGE_INSET, y, width, size,
                               bold=bold, color=color, role=role,
                               numeric=numeric)).frame().size.height


def button(parent, title, x, y, width, target, command, *, key=None, view_type=None,
           bezel="rounded", height=T.BUTTON_HEIGHT):
    """A native push button.

    ``bezel="recessed"`` is the soft gray pill that highlights on hover, which is
    what Apple uses for in-place actions; the default rounded bezel stays for the
    footer's primary/secondary commands. Both are stock bezels, so focus, hover
    and dark mode come free.
    """
    view = (view_type or FocusButton).buttonWithTitle_target_action_(title, target, "menuAction:")
    view.setFrame_(rect(x, y, width, height))
    view.setBezelStyle_(A.NSBezelStyleRecessed if bezel == "recessed"
                        else A.NSBezelStyleRounded)
    view.setIdentifier_(command)
    if key is not None:
        view.setKeyEquivalent_(key)
    parent.addSubview_(view)
    return view


def card_surface(parent, y, width, height, x=T.CARD_INSET):
    """The raised rounded panel a card's content sits on. No stroke by design.

    Depth is color elevation plus a continuous corner (see theme); the 0.5pt
    gray hairline this replaced was the main thing that read as non-native, and
    it could not be made to work in dark mode anyway.
    """
    return T.surface(parent, x, y, width, height, fill="card")


def group_title(parent, text, y, width, note=None):
    """A section title above its card, optionally followed by a description.

    Replaces the 22pt bold shout that used to head each settings block: System
    Settings titles its groups at 15pt semibold and lets the card carry the
    separation.
    """
    height = stretch_label(parent, text, y, width, role="group")
    y += height + T.LABEL_GAP
    if note:
        height = stretch_label(parent, note, y, width, 12, color=SECONDARY)
        y += height + T.ROW_GAP
    return y


def end_group(parent, card_start, y, width):
    """Draw the card behind the rows just laid out, and step past the group.

    The card stretches with the window: the settings window is resizable and the
    rows inside it already do, so a fixed-width card would leave every field
    spilling past its own group -- with no strokes left anywhere, the card edge
    is the only thing that says "these rows belong together".
    """
    _stretch(card_surface(parent, card_start, width - 2 * T.CARD_INSET,
                          y - T.ROW_GAP + T.GUTTER - card_start))
    return y - T.ROW_GAP + T.GUTTER + T.GROUP_GAP


def popup_row(parent, title, y, width, items, index, accessibility=None):
    """Label plus a stock pop-up.

    Pop-ups keep their native bezel -- they already have soft corners and their
    own affordance, and wrapping them in an input well would stack chrome.
    """
    height = stretch_label(parent, title, y, width, 13)
    y += height + T.LABEL_GAP
    popup = _stretch(A.NSPopUpButton.alloc().initWithFrame_pullsDown_(
        rect(T.PAGE_INSET, y, width, T.FIELD_HEIGHT), False))
    popup.addItemsWithTitles_(items)
    popup.selectItemAtIndex_(index)
    popup.setFont_(T.font("body"))
    popup.setAccessibilityLabel_(accessibility or title)
    parent.addSubview_(popup)
    return y + T.FIELD_HEIGHT, popup


AUTO_MODEL_PLACEHOLDER = "不设置（保持现状）"
SUBAGENT_MODEL_PLACEHOLDER = "默认（保持现有值；为空时填入主模型）"


class ModelPopup:
    """One model popup's index<->id convention, in exactly one place.

    The main popup has no leading placeholder (offset 0); auto/subagent lead
    with "不设置"/"默认" (offset 1). All three end with the custom marker whose
    value lives in the paired text field. The index math itself is pure and
    unit-tested in layout (model_popup_index/value/restore); this wrapper only
    wires it to the Cocoa popup and field. The provider popup has a different
    convention and deliberately stays outside this class.
    """

    def __init__(self, popup, field_key, offset=0, placeholder=""):
        self.popup = popup
        self.field_key = field_key
        self.offset = offset
        self.placeholder = placeholder

    @staticmethod
    def titles(options, placeholder=""):
        labels = [option.label for option in options]
        if placeholder:
            labels = [placeholder] + labels
        return labels + [CUSTOM_MODEL_MARKER]

    def value(self, known):
        return model_popup_value(known, self.popup.indexOfSelectedItem(), self.offset)

    def repopulate(self, options, known, model_id, custom, fields):
        """Refresh the items in place, preserving (or demoting) the selection."""
        self.popup.removeAllItems()
        self.popup.addItemsWithTitles_(self.titles(options, self.placeholder))
        index, demoted = model_popup_restore(known, model_id, custom, self.offset)
        if demoted:
            fields[self.field_key].setStringValue_(demoted)
        self.popup.selectItemAtIndex_(index)


def window_scaffold(window, width, header_height, footer_height):
    """The shared window shell: page root + flipped header + borderless scroll
    + seamed footer. Details and history differ only in header contents and
    footer buttons; the scaffolding itself is identical and lives here once.
    Returns (root, header, scroll, footer).
    """
    root = T.SurfaceView.alloc().initWithFrame_(window.contentView().bounds())
    root.setAutoresizingMask_(A.NSViewWidthSizable | A.NSViewHeightSizable)
    root.setWantsLayer_(True)
    root.fill = "page"
    window.setContentView_(root)
    height = window.contentView().bounds().size.height
    header = FlippedView.alloc().initWithFrame_(rect(0, 0, width, header_height))
    root.addSubview_(header)
    scroll = A.NSScrollView.alloc().initWithFrame_(
        rect(0, header_height, width, max(0, height - header_height - footer_height)))
    scroll.setHasVerticalScroller_(True)
    scroll.setBorderType_(A.NSNoBorder)
    scroll.setDrawsBackground_(False)
    root.addSubview_(scroll)
    footer = T.SurfaceView.alloc().initWithFrame_(
        rect(0, height - footer_height, width, footer_height))
    footer.setWantsLayer_(True)
    footer.fill = "page"
    root.addSubview_(footer)
    # The one line the shell still draws: a seam separating the fixed action
    # bar from scrolling content, the way a sheet separates its buttons.
    _stretch(T.surface(footer, 0, 0, width, T.SEAM_HEIGHT, fill="seam"))
    return root, header, scroll, footer


def document_begin(scroll):
    """Start a document rebuild: keep the scroll origin, derive the widths.

    Returns (content, origin, content_width, width): the flipped document view
    (viewport - 24) and the label width (content - 48) every row lays out
    against -- the width contract the smoke asserts.
    """
    origin = scroll.contentView().bounds().origin
    viewport_width = scroll.contentView().bounds().size.width
    content_width = max(1, viewport_width - 24)
    width = max(1, content_width - 48)
    content = FlippedView.alloc().initWithFrame_(rect(0, 0, content_width, 438))
    return content, origin, content_width, width


def document_end(scroll, content, content_width, y, origin):
    """Install the rebuilt document and restore the clamped scroll position."""
    content.setFrameSize_(F.NSMakeSize(content_width, y + 12))
    scroll.setDocumentView_(content)
    max_y = max(0, content.frame().size.height - scroll.contentView().bounds().size.height)
    scroll.contentView().scrollToPoint_(F.NSMakePoint(0, min(origin.y, max_y)))
    scroll.reflectScrolledClipView_(scroll.contentView())


def text_field(parent, value, secure, y, width, label_text):
    """A borderless field sitting in a soft recessed well.

    The native rounded text bezel was tried first and rejected on measurement:
    it is a true pill (radius = half the height), its editable height is pinned
    near 21pt regardless of the frame, and at 13pt Chinese descriptions lose
    their descenders to the clip. A borderless field over a 6pt continuous
    corner well shows no stroke at rest, keeps the row geometry the y-cursor
    layout expects, and still gets the stock field editor, text dragging and the
    visible focus ring the design audit asked to preserve.
    """
    well = _stretch(T.surface(parent, T.PAGE_INSET, y, width, T.FIELD_HEIGHT,
                              fill="field", behind=False, cls=FieldWell))
    well.setAccessibilityElement_(False)
    field = (A.NSSecureTextField if secure else A.NSTextField).alloc().initWithFrame_(
        rect(T.PAGE_INSET + T.WELL_PAD_X, y + 1, width - 2 * T.WELL_PAD_X,
             T.FIELD_HEIGHT - 2))
    field.setStringValue_(value)
    field.setFont_(T.font("body"))
    field.setTextColor_(TEXT)
    field.setBezeled_(False)
    field.setDrawsBackground_(False)
    field.setEditable_(True)
    field.setSelectable_(True)
    # The well carries the focus cue, so the field's own ring would be a second,
    # square outline inside a rounded surface.
    field.setFocusRingType_(A.NSFocusRingTypeNone)
    field.setAccessibilityLabel_(label_text)
    parent.addSubview_(_stretch(field))
    return field, well


def checkbox(parent, title, on, y, width):
    box = A.NSButton.alloc().initWithFrame_(rect(T.PAGE_INSET, y, width, 22))
    box.setButtonType_(A.NSButtonTypeSwitch)
    box.setTitle_(title)
    box.setFont_(T.font("body"))
    box.setState_(A.NSControlStateValueOn if on else A.NSControlStateValueOff)
    box.setAccessibilityLabel_(title)
    parent.addSubview_(box)
    return box


def form_row(parent, title, value, secure, y, width):
    """Label plus input. Returns (y just below the field, field, well).

    The well travels with the field: it is the visible surface a row has, so
    geometry checks (and the user's eye) land on it rather than on the
    transparent text view inset inside it.
    """
    height = stretch_label(parent, title, y, width, 13)
    y += height + T.LABEL_GAP
    field, well = text_field(parent, value, secure, y, width, title)
    return y + T.FIELD_HEIGHT, field, well


class FlippedView(A.NSView):
    def isFlipped(self):
        return True


class FieldWell(T.SurfaceView):
    """The rounded well behind a borderless field. It owns the focus cue.

    A borderless field has no bezel to light up, and the stock focus ring only
    exists on a key window on a real display -- it is invisible to
    ``cacheDisplayInRect`` (the v0.1.0 baseline captured none either, so nothing
    here is being given up). The ring therefore lives on the well, appears
    exactly while its field is first responder and nowhere else, which is the
    same rule macOS uses, and is assertable by the smoke run.

    Neither editing notifications nor the field's own responder callbacks can
    drive this: ``NSControlTextDidBeginEditing`` never fires for a programmatic
    ``makeFirstResponder_`` (which is how the form focuses a field on open and
    after every validation failure), and a text field never sees
    ``resignFirstResponder`` because the *shared field editor* is the first
    responder, not the field -- a version wired that way measured three wells
    lit at once. So ``MonitorUI.refresh_field_focus`` compares each field's
    editor against the window's first responder, and requires the window to be
    key: AppKit leaves the editor in place when focus moves to another app, and
    a bright ring on an inactive window is not how macOS draws focus.
    """

    focused = False

    @python_method
    def set_focused(self, focused):
        if self.focused != focused:
            self.focused = focused
            self.setNeedsDisplay_(True)

    def updateLayer(self):
        T.SurfaceView.updateLayer(self)
        layer = self.layer()
        layer.setBorderWidth_(2.0 if self.focused else 0.0)
        layer.setBorderColor_((T.ACCENT if self.focused else A.NSColor.clearColor()).CGColor())


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
        local_point = self.convertPoint_fromView_(point, self.superview())
        for child in reversed(list(self.subviews())):
            if not isinstance(child, A.NSButton) or child.isHidden():
                continue
            hit_frame = child.hitFrame() if isinstance(child, CardRefreshButton) else child.frame()
            if F.NSPointInRect(local_point, hit_frame):
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
            # popUpStatusItemMenu_ is deprecated since 10.10; the context-menu
            # popup (same pattern as the card's right-click) keeps the exact
            # UX: left click opens details, right/ctrl click opens the menu.
            # Assigning statusItem.menu instead would open the menu on LEFT
            # click too, which is not this app's interaction.
            A.NSMenu.popUpContextMenu_withEvent_forView_(
                self.owner.make_menu(), event, self.owner.status_item.button())
        else:
            self.owner.show_details()

    def menuAction_(self, sender):
        command = sender.representedObject() if isinstance(sender, A.NSMenuItem) else sender.identifier()
        self.owner.command(str(command))

    def windowWillStartLiveResize_(self, notification):
        # A drag posts windowDidResize_ on every tick; rebuilding the document
        # each time (all labels re-measured, up to 3 fresh ChartViews) is a
        # redraw storm. During the drag only geometry is laid out.
        self.owner._live_resizing = True

    def windowDidEndLiveResize_(self, notification):
        self.owner._live_resizing = False
        # One rebuild at the final width.
        self.windowDidResize_(notification)

    def windowDidResize_(self, notification):
        live = getattr(self.owner, "_live_resizing", False)
        if notification.object() == self.owner.details:
            self.owner.layout_details()
            if not live:
                self.owner.render_details_document()
        elif notification.object() == getattr(self.owner, "settings", None):
            # Never rebuild the settings form here: that would discard typing.
            self.owner.layout_settings()
        elif notification.object() == getattr(self.owner, "history", None):
            # Safe to rebuild wholesale: the history window holds no typed input.
            self.owner.layout_history()
            if not live:
                self.owner.render_history_document()

    def tick_(self, timer):
        self.owner.drain_events()
        self.owner.flush_settings_rebuild()
        self.owner.drain_model_catalog()

    def wake_(self, notification):
        self.owner.monitor.refresh_if_due()
        self.owner.position_card()

    def screenChanged_(self, notification):
        self.owner.position_card()

    def accessibilityChanged_(self, notification):
        self.owner.apply_materials()

    def focusChanged_(self, notification):
        # This observer sees every window's updates (details, card, alerts); the
        # ring only exists in the settings form, so nothing else pays for it.
        if self.owner.settings is not None and self.owner.settings.isVisible():
            self.owner.refresh_field_focus()

    def themeChanged_(self, notification):
        # Surfaces and text re-resolve themselves; only the reduce-transparency
        # fallback holds a frozen CGColor, and this re-takes it.
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
        self.history = None
        self.history_range = "24h"
        # Which charts have their 数据列表 twin expanded; survives document
        # rebuilds because it lives on the shell, not the discarded views.
        self.history_table_expanded = set()
        self._history_charts = {}
        self._history_geometries = {}
        self._history_table_sources = []
        self.timer = None
        self.closed = False
        # Set between windowWillStartLiveResize_ and windowDidEndLiveResize_;
        # resize ticks during a drag lay out geometry only.
        self._live_resizing = False
        self.materials = []
        self.status_image = None
        # tone -> NSImage. state_tone has only four discrete values, so the
        # Pillow draw + PNG encode + NSImage decode runs at most four times
        # instead of on every render.
        self._icon_cache = {}
        self.provider_popup = None
        self.settings_provider = DEFAULT_PROVIDER
        self._settings_dirty = False
        # Claude Code hookup: the model popup is read on apply, never observed.
        self.model_popup = None
        self.auto_popup = None
        self.subagent_popup = None
        # field key -> ModelPopup wrapper; populated by build_settings.
        self._model_popups = {}
        self.apply_claude_button = None
        self.inference_key = None
        self._applying_claude = False
        # Live model catalogue: the effective options the popups were built from,
        # a background-fetch result queue drained on the main tick, and per-provider
        # status copy. The fetch never blocks the dialog; failure keeps the static
        # catalogue. _claude_options is the single source for index<->id mapping so
        # build and read can never disagree after an in-place refresh.
        self._claude_options = ()
        self._catalog_events = queue.SimpleQueue()
        self._catalog_inflight = set()
        self._catalog_status = {}
        self.model_refresh_button = None
        self.model_status_label = None
        # Injectable transport so the offline smoke can stub the network call.
        self._fetch_model_ids = fetch_model_ids
        # Offline smoke hook; production leaves this None and shows a real alert.
        self._confirm_override = None
        # Offline smoke hook for the write target: (settings.json, .claude.json).
        self.claude_paths_override = None
        # Per-provider Claude form memory so switching providers never discards
        # what was typed. Non-secret fields also persist to claude.json; the
        # key drafts stay in memory only and are cleared on shutdown.
        self._claude_drafts = {}
        self._claude_key_drafts = {}
        # Quota alert delivery. The bundle guard inside MacNotifier makes this
        # safe in source runs and preview (it reports "unavailable" there);
        # clicks route back to the details window on the main thread.
        self.notifier = MacNotifier(on_click=self.show_details)
        if getattr(self.monitor, "notifier", None) is None:
            self.monitor.notifier = self.notifier.notify

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
        # Focus can arrive by click, Tab or a programmatic makeFirstResponder_;
        # the window posts an update after all of them, so it is the one signal
        # that keeps exactly one input ring lit.
        center.addObserver_selector_name_object_(self.actions, "focusChanged:", A.NSWindowDidUpdateNotification, None)
        A.NSDistributedNotificationCenter.defaultCenter().addObserver_selector_name_object_(
            self.actions, "themeChanged:", "AppleInterfaceThemeChangedNotification", None)
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
        # No setAppearance_ here on purpose: the shell's colors are dynamic
        # catalog or provider-backed pairs, so following the system is what
        # makes them resolve right. Pinning Aqua was the old forced-light rule.
        window.setMinSize_(F.NSMakeSize(width, min(height, 360)))
        window.center()
        # Escape is a standard Cancel action; closing hides the reusable window.
        return window

    def activate(self, window):
        self.app.activateIgnoringOtherApps_(True)
        window.makeKeyAndOrderFront_(None)

    @property
    def provider_spec(self):
        """Spec of the provider the current state was fetched from."""
        return get_provider(self.state.provider)

    def app_title(self):
        return f"{self.state.provider_label}用量监控"

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
            entries = (("打开用量详情", "details"), ("用量历史", "history")) + entries
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
                    "provider": self.provider_changed,
                    "apply-claude": self.apply_claude_config,
                    "refresh-models": self.refresh_models,
                    "cancel": lambda: self.settings.orderOut_(None),
                    "close-details": lambda: self.details.orderOut_(None),
                    "history": self.show_history,
                    "history-range": lambda: self.set_history_range(
                        RANGE_KEYS[self.history_range_control.selectedSegment()]),
                    "close-history": lambda: self.history.orderOut_(None),
                    "exit": lambda: self.app.terminate_(None)}
        if command.startswith("history-table:"):
            self.toggle_history_table(command.split(":", 1)[1])
        elif command in handlers:
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
            tone = state_tone(state)
            image = self._icon_cache.get(tone)
            if image is None:
                data = F.NSData.dataWithBytes_length_(*self.icon_data())
                image = A.NSImage.alloc().initWithData_(data)
                image.setSize_(F.NSMakeSize(20, 20))
                self._icon_cache[tone] = image
            if image is not self.status_image:
                self.status_image = image
                self.status_item.button().setImage_(image)
            self.status_item.button().setToolTip_(hover_summary(state))
        # Hidden windows must not render: an orderOut_'d details window used
        # to rebuild every NSTextField + measurement on each refresh, and the
        # card repainted even while hidden. The show paths (show_details,
        # show_history, sync_card_visibility) render explicitly before
        # ordering front, so nothing stale is ever presented.
        if self.card is not None and self.card.isVisible():
            self.render_card()
        if self.details is not None and self.details.isVisible():
            self.render_details()
        if self.history is not None and self.history.isVisible():
            self.render_history()

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
        root, header, scroll, footer = window_scaffold(self.details, 480, 154, 58)
        self.details_root = root
        self.details_header = header
        self.details_scroll = scroll
        self.details_footer = footer
        # Provider-dependent; filled in by render_details on every state change.
        self.header_title = text_label(self.details_header, "", T.PAGE_INSET, 14, 432,
                                       role="title", bold=True)
        self.header_status = text_label(self.details_header, "", T.PAGE_INSET, 46, 432,
                                        role="status", bold=True)
        self.header_updated = text_label(self.details_header, "", T.PAGE_INSET, 72, 432,
                                         role="caption", color=SECONDARY, numeric=True)
        self.header_reason = text_label(self.details_header, "", T.PAGE_INSET, 94, 432,
                                        role="detail")
        self.header_reason.setMaximumNumberOfLines_(2)
        self.header_reason.setLineBreakMode_(A.NSLineBreakByTruncatingTail)
        self.header_reason.setFrame_(rect(T.PAGE_INSET, 94, 480 - 2 * T.PAGE_INSET, 48))
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
            view.setFrameSize_(F.NSMakeSize(size.width - 2 * T.PAGE_INSET,
                                            view.frame().size.height))

    def render_details(self):
        if not hasattr(self, "details_root"):
            self.create_details_shell()
        self.details.setTitle_(self.app_title())
        self.header_title.setStringValue_(f"{self.state.provider_label} · 套餐用量")
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
        self.header_reason.setTextColor_(
            T.tone("error") if self.state.status == "error"
            else T.tone("warning") if reason else SECONDARY)
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
        content, origin, content_width, width = document_begin(self.details_scroll)
        y = 12
        snapshot = self.state.snapshot
        reason = self.header_reason.stringValue()
        if snapshot:
            y += label(content, snapshot.plan_name or snapshot.plan_type, T.PAGE_INSET, y,
                       width, role="plan", bold=True) + 12
            windows = {window.kind: window for window in snapshot.windows}
            for kind in WINDOW_ORDER:
                start_y = y
                y += 8
                window = windows.get(kind)
                y += label(content, WINDOW_LABELS[kind], T.PAGE_INSET + T.GUTTER, y, width,
                           role="kind", bold=True)
                if window is None:
                    y += label(content, "暂无数据", T.PAGE_INSET + T.GUTTER, y, width,
                               role="body", color=SECONDARY) + 14
                else:
                    y += label(content, f"剩余 {display_remaining(window):.1f}%",
                               T.PAGE_INSET + T.GUTTER, y, width, role="metric", bold=True,
                               numeric=True) + 2
                    progress = A.NSProgressIndicator.alloc().initWithFrame_(
                        rect(T.PAGE_INSET + T.GUTTER, y, width, 10))
                    progress.setStyle_(A.NSProgressIndicatorStyleBar)
                    # No threaded animation here on purpose: the document view is
                    # rebuilt on every render (and every resize), so an animating
                    # bar would re-slide from zero each time instead of easing to
                    # a new value. Small control size only slims the track.
                    progress.setControlSize_(A.NSControlSizeSmall)
                    progress.setIndeterminate_(False)
                    progress.setMinValue_(0)
                    progress.setMaxValue_(100)
                    progress.setDoubleValue_(display_remaining(window))
                    progress.setAccessibilityLabel_(f"{WINDOW_LABELS[kind]}剩余容量")
                    progress.setAccessibilityValueDescription_(f"剩余 {display_remaining(window):.1f}%")
                    content.addSubview_(progress)
                    y += 19
                    text = f"已用 {window.used_percent:.1f}%"
                    if window.quota is not None:
                        unit = self.provider_spec.unit_label
                        text += (f" · {unit} {format_number(window.used)} / "
                                 f"{format_number(window.quota)} · 剩余 "
                                 f"{format_number(window.remaining_absolute)}")
                    y += label(content, text, T.PAGE_INSET + T.GUTTER, y, width,
                               role="detail", color=SECONDARY, numeric=True)
                    y += label(content, "重置时间：" + (format_datetime(window.reset_at) if window.reset_at else "—"),
                               T.PAGE_INSET + T.GUTTER, y, width, role="caption", color=SECONDARY,
                               numeric=True)
                    trend = next((item for item in self.state.trends if item.kind == kind), None)
                    trend_text = trend_line(trend, window, self.provider_spec.unit_label)
                    if trend_text:
                        # Exhaustion before the reset is the one trend outcome
                        # worth a warning color; everything else stays quiet.
                        exhausting = (
                            trend is not None
                            and trend.exhausted_at is not None
                            and trend.resets_before_exhaustion is False
                        )
                        y += 2
                        y += label(content, trend_text, T.PAGE_INSET + T.GUTTER, y, width,
                                   role="caption",
                                   color=T.tone("warning") if exhausting else SECONDARY,
                                   numeric=True)
                    y += 10
                card_surface(content, start_y, max(1, content_width - 24), y - start_y - 4,
                       x=T.PAGE_INSET)
                y += 6
        else:
            # The empty window used to be one line of text floating in space,
            # which read as a broken layout. Carded like the data states, it is
            # the same "connect to see numbers" affordance the cards carry.
            start_y = y
            y += T.GUTTER
            text = "请先连接账号，填写 AK/SK 以查询套餐容量。" if self.state.status == "unconfigured" else "尚未获取套餐用量，请使用下方操作恢复查询。"
            y += label(content, text, T.PAGE_INSET + T.GUTTER, y, width,
                       role="body") + T.GUTTER
            card_surface(content, start_y, max(1, content_width - 24), y - T.GUTTER - start_y,
                       x=T.PAGE_INSET)
            y += T.GROUP_GAP
        # Long errors remain fully readable without expanding the fixed header.
        if len(reason) > 60 or "\n" in reason:
            start_y = y
            y += T.GUTTER
            y += label(content, "完整错误说明", T.PAGE_INSET + T.GUTTER, y, width,
                       role="kind", bold=True) + T.LABEL_GAP
            y += label(content, reason, T.PAGE_INSET + T.GUTTER, y, width,
                       role="detail", color=SECONDARY) + T.GUTTER
            card_surface(content, start_y, max(1, content_width - 24),
                         y - T.GUTTER - start_y, x=T.PAGE_INSET)
            y += T.GROUP_GAP
        document_end(self.details_scroll, content, content_width, y, origin)

    # --- History window ------------------------------------------------------

    def show_history(self):
        if self.history is None:
            self.history = self.window(
                f"{self.state.provider_label} · {HISTORY_WINDOW_TITLE}",
                HISTORY_WIDTH, HISTORY_HEIGHT)
            self.history.setContentMinSize_(
                F.NSMakeSize(HISTORY_WIDTH, HISTORY_MIN_HEIGHT))
        self.render_history()
        self.activate(self.history)

    def create_history_shell(self):
        root, header, scroll, footer = window_scaffold(
            self.history, HISTORY_WIDTH, HISTORY_HEADER_HEIGHT, HISTORY_FOOTER_HEIGHT)
        self.history_root = root
        self.history_header = header
        self.history_scroll = scroll
        self.history_footer = footer
        self.history_title = text_label(
            self.history_header, HISTORY_WINDOW_TITLE, T.PAGE_INSET, 14,
            HISTORY_WIDTH - 2 * T.PAGE_INSET, role="title", bold=True)
        self.history_context = text_label(
            self.history_header, "", T.PAGE_INSET, 46,
            HISTORY_WIDTH - 2 * T.PAGE_INSET, role="caption", color=SECONDARY,
            numeric=True)
        # One range selector above everything it scopes, pinned in the header
        # so it stays reachable while the charts scroll.
        self.history_range_control = A.NSSegmentedControl.alloc().initWithFrame_(
            rect(T.PAGE_INSET, 72, 320, 28))
        self.history_range_control.setSegmentCount_(len(RANGE_KEYS))
        for index, key in enumerate(RANGE_KEYS):
            self.history_range_control.setLabel_forSegment_(HISTORY_RANGE_LABELS[key], index)
        self.history_range_control.setSelectedSegment_(RANGE_KEYS.index(self.history_range))
        self.history_range_control.setTarget_(self.actions)
        self.history_range_control.setAction_("menuAction:")
        self.history_range_control.setIdentifier_("history-range")
        self.history_header.addSubview_(self.history_range_control)
        self.history_refresh_button = button(
            self.history_footer, "立即刷新", 18, 12, 124, self.actions, "refresh", key="\r")
        self.history_close_button = button(
            self.history_footer, "关闭", HISTORY_WIDTH - 126, 12, 108,
            self.actions, "close-history", key="\x1b")
        self.history_refresh_button.setNextKeyView_(self.history_close_button)
        self.history_close_button.setNextKeyView_(self.history_refresh_button)
        self.layout_history()

    def layout_history(self):
        if not hasattr(self, "history_root"):
            return
        size = self.history_root.bounds().size
        self.history_header.setFrame_(rect(0, 0, size.width, HISTORY_HEADER_HEIGHT))
        self.history_scroll.setFrame_(rect(
            0, HISTORY_HEADER_HEIGHT, size.width,
            max(0, size.height - HISTORY_HEADER_HEIGHT - HISTORY_FOOTER_HEIGHT)))
        self.history_footer.setFrame_(
            rect(0, size.height - HISTORY_FOOTER_HEIGHT, size.width, HISTORY_FOOTER_HEIGHT))
        self.history_close_button.setFrameOrigin_(F.NSMakePoint(size.width - 126, 12))
        for view in (self.history_title, self.history_context):
            view.setFrameSize_(F.NSMakeSize(
                size.width - 2 * T.PAGE_INSET, view.frame().size.height))

    def render_history(self):
        if not hasattr(self, "history_root"):
            self.create_history_shell()
        self.history.setTitle_(f"{self.state.provider_label} · {HISTORY_WINDOW_TITLE}")
        self.history_context.setStringValue_(history_context_line(self.state))
        self.history_refresh_button.setTitle_(
            "正在刷新…" if self.state.is_refreshing else "立即刷新")
        self.history_refresh_button.setEnabled_(not self.state.is_refreshing)
        self.layout_history()
        self.render_history_document()

    def set_history_range(self, range_key):
        if range_key not in RANGE_KEYS:
            return
        self.history_range = range_key
        self.render_history_document()

    def toggle_history_table(self, kind):
        if kind in self.history_table_expanded:
            self.history_table_expanded.discard(kind)
        else:
            self.history_table_expanded.add(kind)
        self.render_history_document()

    def render_history_document(self):
        if not hasattr(self, "history_scroll"):
            return
        content, origin, content_width, width = document_begin(self.history_scroll)
        y = 12
        snapshot = self.state.snapshot
        # Deterministic between refreshes, honest right edge when stale.
        now = self.state.last_attempt_at or (
            snapshot.queried_at if snapshot is not None else None) or datetime.now(UTC)
        self._history_charts = {}
        self._history_geometries = {}
        self._history_table_sources = []
        if snapshot is None:
            start_y = y
            y += T.GUTTER
            y += label(content, history_empty_text(self.state, "no_history"),
                       T.PAGE_INSET + T.GUTTER, y, width, role="body") + T.GUTTER
            card_surface(content, start_y, max(1, content_width - 24),
                         y - T.GUTTER - start_y, x=T.PAGE_INSET)
            y += T.GROUP_GAP
        else:
            present = {window.kind for window in snapshot.windows}
            for kind in WINDOW_ORDER:
                if kind not in present:
                    continue
                geometry = build_chart(
                    self.state.history, kind, now=now, range_key=self.history_range,
                    plot_width=max(1.0, width - Y_AXIS_WIDTH),
                    alert_threshold_percent=self.state.alert_threshold_percent)
                self._history_geometries[kind] = geometry
                start_y = y
                y += 8
                y += label(content, history_chart_title(kind),
                           T.PAGE_INSET + T.GUTTER, y, width, role="kind", bold=True)
                y += T.LABEL_GAP
                if geometry.empty_reason == "no_history":
                    y += label(content, history_empty_text(self.state, "no_history"),
                               T.PAGE_INSET + T.GUTTER, y, width,
                               role="body", color=SECONDARY) + 14
                else:
                    chart = ChartView.alloc().initWithFrame_(rect(
                        T.PAGE_INSET + T.GUTTER, y, width, PLOT_HEIGHT + X_AXIS_HEIGHT))
                    chart.set_chart(geometry, self.provider_spec.unit_label)
                    chart.setIdentifier_(f"history-chart-{kind}")
                    content.addSubview_(chart)
                    self._history_charts[kind] = chart
                    y += PLOT_HEIGHT + X_AXIS_HEIGHT
                    if geometry.empty_reason == "thin":
                        y += T.LABEL_GAP
                        y += label(content, history_empty_text(self.state, "thin"),
                                   T.PAGE_INSET + T.GUTTER, y, width,
                                   role="caption", color=SECONDARY)
                    y += T.LABEL_GAP
                    expanded = kind in self.history_table_expanded
                    y += button(
                        content,
                        HISTORY_TABLE_COLLAPSE if expanded else HISTORY_TABLE_TITLE,
                        T.PAGE_INSET + T.GUTTER, y, 150, self.actions,
                        f"history-table:{kind}", bezel="recessed", height=22).frame().size.height
                    y += T.LABEL_GAP
                    if expanded:
                        y += self._history_table(content, geometry, kind, y, width)
                    y += 10
                card_surface(content, start_y, max(1, content_width - 24),
                             y - start_y - 4, x=T.PAGE_INSET)
                y += 6
        document_end(self.history_scroll, content, content_width, y, origin)

    def _history_table(self, content, geometry, kind, y, width):
        """The chart's table twin: every bucketed sample, newest first, so no
        value is reachable only through a tooltip. Scrolls instead of capping
        rows -- a capped table would gate the very values it exists to free."""
        unit = self.provider_spec.unit_label
        columns = [("time", HISTORY_TABLE_HEADERS["time"], 110.0),
                   ("percent", HISTORY_TABLE_HEADERS["percent"], 70.0)]
        if geometry.has_absolute:
            columns.append(("absolute", history_absolute_header(unit),
                            max(80.0, width - 180.0)))
        row_count = len(geometry.rows)
        table_height = max(1, row_count) * HISTORY_TABLE_ROW_HEIGHT
        scroll_height = min(HISTORY_TABLE_MAX_HEIGHT,
                            table_height + 24)  # + header band
        scroll = A.NSScrollView.alloc().initWithFrame_(
            rect(T.PAGE_INSET + T.GUTTER, y, width, scroll_height))
        scroll.setHasVerticalScroller_(True)
        scroll.setBorderType_(A.NSNoBorder)
        scroll.setDrawsBackground_(False)
        table = A.NSTableView.alloc().initWithFrame_(rect(0, 0, width, table_height))
        table.setIdentifier_(f"history-table-{kind}")
        table.setRowHeight_(HISTORY_TABLE_ROW_HEIGHT)
        table.setUsesAlternatingRowBackgroundColors_(True)
        header = A.NSTableHeaderView.alloc().initWithFrame_(rect(0, 0, width, 24))
        table.setHeaderView_(header)
        for key, title, column_width in columns:
            column = A.NSTableColumn.alloc().initWithIdentifier_(key)
            column.setTitle_(title)
            column.setWidth_(column_width)
            column.setMinWidth_(40)
            column.setDataCell_(A.NSTextFieldCell.alloc().init())
            table.addTableColumn_(column)
        source = HistoryTableDataSource.alloc().initWithRows_columns_(
            geometry.rows, tuple(key for key, _, _ in columns))
        # NSTableView does not retain its data source; the shell keeps it alive
        # for as long as the document view lives.
        self._history_table_sources.append(source)
        table.setDataSource_(source)
        scroll.setDocumentView_(table)
        content.addSubview_(scroll)
        return scroll_height

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
        # Title left, status right (matching the Windows card): the combined
        # "provider · plan" title takes the space left of the status text and
        # truncates with an ellipsis instead of wrapping past its 19pt frame.
        # cellSizeForBounds includes the text cell's horizontal insets; the raw
        # attributed-string width would wrap the status onto a clipped 2nd line.
        status_width = max(1, self.card_status.cell().cellSizeForBounds_(
            F.NSMakeRect(0, 0, 10000, 10000)).width + 2)
        status_x = max(margin + 1, size.width - margin - status_width)
        self.card_status.setFrame_(rect(status_x, 14, status_width, self.card_status.frame().size.height))
        self.card_title.setFrame_(rect(margin, 12, max(1, status_x - 10 - margin), self.card_title.frame().size.height))
        for index, kind in enumerate(WINDOW_ORDER):
            x = margin + index * (column_width + gutter)
            self.card_titles[kind].setFrame_(rect(x, 46, column_width, self.card_titles[kind].frame().size.height))
            self.card_values[kind].setFrame_(rect(x, 67, column_width, self.card_values[kind].frame().size.height))
        footer_width = max(1, size.width - margin * 2 - 44)
        self.card_updated.setFrame_(rect(margin, size.height - 25, footer_width, self.card_updated.frame().size.height))
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
            effect.layer().setCornerCurve_(T.CORNER_CURVE)
            effect.layer().setMasksToBounds_(True)
            self.card_view.addSubview_(effect)
            self.materials = [effect]
            self.card_title = text_label(self.card_view, "", 18, 12, 92,
                                          role="kind", bold=True)
            self.card_title.setMaximumNumberOfLines_(1)
            self.card_title.setLineBreakMode_(A.NSLineBreakByTruncatingTail)
            self.card_status = text_label(self.card_view, "", 118, 14, 248, role="caption")
            self.card_titles, self.card_values = {}, {}
            for i, kind in enumerate(WINDOW_ORDER):
                x = 18 + i * 120
                self.card_titles[kind] = text_label(self.card_view, "", x, 46, 112,
                                                     role="caption", color=SECONDARY)
                # 22pt to match the details window's metric role. Both fit the
                # 89.3pt column a minimum-width card gives ("100.0%" measures
                # 82.2pt here vs 83.1pt at the old 23pt bold); the change of face
                # -- tabular, rounded -- is the point, not the extra 1pt.
                self.card_values[kind] = text_label(self.card_view, "—", x, 67, 112,
                                                    role="metric_large", bold=True,
                                                    numeric=True)
                self.card_values[kind].setAccessibilityLabel_(f"{WINDOW_LABELS[kind]}剩余容量")
            self.card_updated = text_label(self.card_view, "", 18, 108, 304,
                                            role="caption", color=SECONDARY, numeric=True)
            self.card_refresh_button = button(
                self.card_view, "", 340, 116, 32, self.actions, "refresh",
                key="\r", view_type=CardRefreshButton,
            )
            self.card_refresh_button.setImage_(A.NSImage.imageWithSystemSymbolName_accessibilityDescription_("arrow.clockwise", "立即刷新"))
            self.card_refresh_button.setImagePosition_(A.NSImageOnly)
            self.card_refresh_button.setAccessibilityLabel_("立即刷新")
            self.card_refresh_button.setToolTip_("立即刷新")
            self.card.setInitialFirstResponder_(self.card_refresh_button)
        self.card_title.setStringValue_(card_title(self.state))
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
            color = A.NSColor.windowBackgroundColor() if reduced else A.NSColor.clearColor()
            # A CGColor is a frozen snapshot, and this one sits under a material
            # that AppKit no longer tints: capture it inside the view's own
            # appearance or a light-mode white box survives the switch to dark.
            captured = []
            effect.effectiveAppearance().performAsCurrentDrawingAppearance_(
                lambda: captured.append(color.CGColor()))
            effect.layer().setBackgroundColor_(captured[0])

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
            level = DESKTOP_CARD_LEVEL
            behavior = DESKTOP_CARD_BEHAVIOR
        else:
            level = A.NSFloatingWindowLevel if self.preferences.topmost else A.NSNormalWindowLevel
            behavior = FLOATING_CARD_BEHAVIOR
        self.card.setLevel_(level)
        self.card.setCollectionBehavior_(behavior)
        if self.preferences.visible:
            self.card.orderFrontRegardless()
            # Catch up on any state published while the card was hidden.
            self.render_card()
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
        current = self.storage.load_settings()
        if self.settings is None:
            self.settings = self.window("设置 · " + TITLE, 500, 490)
        self._settings_dirty = False
        self.build_settings(current.provider, current.region,
                            str(current.refresh_minutes), **self.claude_seed(current.provider))
        self.activate(self.settings)
        self._maybe_auto_fetch(current.provider)

    def claude_seed(self, provider):
        """Initial Claude section values for one provider: draft → prefs → live.

        The layering itself is the pure `resolve_claude_seed` (unit-tested);
        this only gathers the three sources. Falling back to the live file
        rather than to blank is what keeps a provider switch from wiping the
        base URL and model the user can see they had.
        """
        try:
            prefs = self.storage.load_claude_prefs(provider)
        except QueryError:
            prefs = ClaudeProviderPrefs()
        live_base_url = live_model = live_auto_model = live_subagent_model = ""
        try:
            document, _ = claude_code.read_document_at(self.resolve_claude_paths()[0])
        except (ClaudeConfigError, QueryError):
            document = None
        env = document.get("env") if isinstance(document, dict) else None
        if isinstance(env, dict):
            # Only the non-secret fields are echoed back; the token in the
            # live file is deliberately never surfaced into a text field.
            live_base_url = str(env.get("ANTHROPIC_BASE_URL") or "").strip()
            live_model = str(env.get("ANTHROPIC_MODEL") or "").strip()
            live_auto_model = str(env.get(claude_code.ENV_AUTO_MODE) or "").strip()
            live_subagent_model = str(env.get(claude_code.ENV_SUBAGENT) or "").strip()
        return resolve_claude_seed(get_provider(provider), self._claude_drafts.get(provider),
                                   prefs, live_base_url, live_model, live_auto_model,
                                   model_options=self._effective_claude_options(provider),
                                   live_subagent_model=live_subagent_model)

    def _effective_claude_options(self, provider):
        """Static catalogue merged with any cached live models for this provider.

        The cache is the source of truth for what was fetched; an empty cache
        (offline / first run / fetch failed) keeps the static catalogue intact.
        """
        spec = get_provider(provider)
        try:
            fetched = self.storage.load_model_catalog(provider)
        except QueryError:
            fetched = ()
        if fetched:
            return merge_model_options(spec.claude_models, fetched)
        return spec.claude_models

    def stored_inference_key(self, provider):
        """Best-known inference key for "leave blank to keep" semantics."""
        return self._claude_key_drafts.get(provider) or self.inference_key

    def stash_claude_draft(self, provider):
        """Remember this provider's Claude form so switching cannot lose it.

        The three non-secret fields go to claude.json; the inference key stays
        in memory only, for as long as this process shows the dialog.
        """
        if self.model_popup is None:
            return
        *_, key_text, prefs = self.claude_form_values()
        self._claude_drafts[provider] = prefs
        if key_text.strip():
            self._claude_key_drafts[provider] = key_text.strip()
        try:
            self.storage.save_claude_prefs(prefs, provider)
        except QueryError:
            pass

    def resolve_claude_paths(self):
        """Resolve both target paths from the real environment at call time.

        `claude_paths_override` exists so the offline smoke can point the whole
        write at a temp directory instead of a real `~/.claude`.
        """
        if self.claude_paths_override is not None:
            return self.claude_paths_override
        environ, home = dict(os.environ), Path.home()
        return (claude_code.resolve_settings_path(environ, home),
                claude_code.resolve_onboarding_path(environ, home))

    def provider_changed(self):
        """Record the popup selection; the rebuild happens on the next tick.

        Never tear down the view hierarchy from inside the popup's own action
        dispatch: the control is still tracking the mouse when it fires.
        """
        self._settings_dirty = True

    def flush_settings_rebuild(self):
        if not self._settings_dirty:
            return
        # The confirm alert spins a nested modal loop, so the 0.1s tick keeps
        # firing while apply_claude_config still holds references to self.fields.
        # Rebuilding now would detach those views mid-write; keep the flag raised.
        if self._applying_claude:
            return
        self._settings_dirty = False
        if self.settings is None or not self.settings.isVisible():
            return
        index = self.provider_popup.indexOfSelectedItem()
        if not 0 <= index < len(PROVIDERS):
            return
        provider = PROVIDERS[index].id
        if provider == self.settings_provider:
            return
        spec = get_provider(provider)
        # Carry over what the user already typed, except a region this provider
        # does not serve.
        region = str(self.fields["region"].stringValue()).strip()
        allowed = PROVIDER_REGIONS.get(provider)
        if allowed is not None and region not in allowed:
            region = spec.default_region
        interval = str(self.fields["interval"].stringValue()).strip() or "15"
        # Alert choices are provider-independent: carry the typed values across
        # the rebuild exactly like region and interval.
        alert_enabled = self.alert_enabled_button.state() == A.NSControlStateValueOn
        alert_threshold = str(self.fields["alert_threshold"].stringValue()).strip() or "10"
        alert_reset = self.alert_reset_button.state() == A.NSControlStateValueOn
        # Stash the outgoing provider's Claude form *before* rebuilding, or the
        # rebuild discards whatever was typed. The new provider is then seeded
        # from its own draft/prefs, falling back to the live config.
        self.stash_claude_draft(self.settings_provider)
        self.build_settings(provider, region, interval,
                            alert_enabled=alert_enabled, alert_threshold=alert_threshold,
                            alert_reset=alert_reset, **self.claude_seed(provider))
        self._maybe_auto_fetch(provider)

    def _alert_status_copy(self):
        status = self.notifier.authorization_status()
        if status == "authorized":
            return "系统通知已授权。", SECONDARY
        if getattr(self.notifier, "legacy_dead", False):
            # The compatibility fallback was removed by the system; without it
            # a denied/unavailable UN path cannot deliver at all. Do not fall
            # through to the "not packaged as .app" copy, which would mislead.
            return "系统通知组件不可用：告警可能无法送达。", T.tone("warning")
        if status == "legacy":
            return "系统通知可用（兼容模式）。", SECONDARY
        if status == "denied":
            return "系统通知被拒绝：请在 系统设置 → 通知 中允许本应用。", T.tone("warning")
        if status == "not_determined":
            return "首次告警时会请求系统通知权限。", SECONDARY
        return "当前运行方式不支持系统通知（打包为 .app 后可用）。", SECONDARY

    def _build_alert_group(self, body, y, field_width, width, enabled, threshold, reset):
        """The alert group as its own builder: build_settings stays a sequence
        of group calls instead of growing another inline form section."""
        y = group_title(body, "低额度告警", y, field_width,
                        "任一窗口剩余低于阈值时发送一次系统通知；剩余回到阈值以上或窗口重置后自动重新启用。")
        card_start = y
        y += T.GUTTER
        self.alert_enabled_button = checkbox(body, "启用系统通知", enabled, y, field_width)
        y += 22 + T.ROW_GAP
        y, self.fields["alert_threshold"], self.field_wells["alert_threshold"] = form_row(
            body, "低额度阈值（%，1–99）", threshold, False, y, field_width)
        y += T.ROW_GAP
        self.alert_reset_button = checkbox(body, "窗口重置时提醒", reset, y, field_width)
        y += 22 + T.ROW_GAP
        text, color = self._alert_status_copy()
        self.alert_status_label = _stretch(text_label(
            body, text, T.PAGE_INSET, y, field_width, 12, color=color))
        self.alert_status_label.setAccessibilityLabel_("系统通知状态")
        y += self.alert_status_label.frame().size.height + T.ROW_GAP
        return end_group(body, card_start, y, width)

    def _build_credentials_group(self, body, y, field_width, width, spec, region, interval):
        """The monitor-credentials group as its own builder (see _build_alert_group)."""
        y = group_title(body, f"连接{spec.label}", y, field_width,
                        spec.credential_note.format(store=self.storage.credential_store_name))
        card_start = y
        y += T.GUTTER
        self.help_expanded = False
        self.help_button = _pin_left(button(body, "凭据说明", T.PAGE_INSET, y, 116,
                                            self.actions, "credential-help",
                                            bezel="recessed", height=22))
        self.help_button.setImage_(A.NSImage.imageWithSystemSymbolName_accessibilityDescription_("chevron.right", None))
        self.help_button.setImagePosition_(A.NSImageLeft)
        self.help_button.setButtonType_(A.NSButtonTypeOnOff)
        self.help_button.setAccessibilityLabel_("展开凭据说明")
        y += 22 + T.ROW_GAP
        self.fields = {}
        self.field_wells = {}
        ak_label = spec.access_key_label
        sk_label = spec.secret_key_label
        for key, title, value, secure in (
            ("ak", ak_label, self.existing.access_key_id if self.existing else "", False),
            ("sk", f"{sk_label}（已保存，留空保持不变）" if self.existing else f"{sk_label}（必填）", "", True),
            ("region", "Region", region, False),
            ("interval", "刷新间隔（分钟，5–1440）", interval, False),
        ):
            y, self.fields[key], self.field_wells[key] = form_row(
                body, title, value, secure, y, field_width)
            y += T.ROW_GAP
        return end_group(body, card_start, y, width)

    def _build_claude_group(self, body, y, field_width, width, provider, spec,
                            claude_model, claude_custom, claude_base_url,
                            claude_auto_model, claude_auto_custom,
                            claude_subagent_model, claude_subagent_custom):
        """The Claude Code hookup group as its own builder."""
        y = group_title(body, "Claude Code 接入", y, field_width,
                        "选择模型后点击下方按钮，把服务商接入信息写入 Claude Code 本地配置。")
        card_start = y
        y += T.GUTTER
        # The effective catalogue is the static list merged with any cached live
        # models, so a plan's newly shipped models appear without a hand edit.
        options = self._effective_claude_options(provider)
        self._claude_options = options
        known = [option.id for option in options]
        # Refresh row: a manual trigger plus live status. Auto-fetch also runs on
        # open / provider switch, but the button lets the user force a re-pull
        # (e.g. right after the vendor ships a model) without reopening the dialog.
        self.model_refresh_button = _pin_left(button(
            body, "刷新模型列表", T.PAGE_INSET, y, 130,
            self.actions, "refresh-models", bezel="recessed", height=22))
        self.model_refresh_button.setAccessibilityLabel_("在线刷新模型列表")
        status_text, status_tone = self._catalog_status_for(provider)
        status_color = SECONDARY if status_tone == "neutral" else T.tone(status_tone)
        self.model_status_label = _stretch(text_label(
            body, status_text, T.PAGE_INSET + 140, y + 3, field_width - 140, 12,
            color=status_color))
        self.model_status_label.setAccessibilityLabel_("模型列表状态")
        y += 22 + T.ROW_GAP
        # Build keeps the original main-popup convention: an id outside the
        # catalogue (or empty) lands on the custom marker, not on item 0.
        index = model_popup_index(known, claude_model, "", empty_index=len(known))
        y, model_popup = popup_row(
            body, "模型", y, field_width, ModelPopup.titles(options), index)
        model_popup.setAccessibilityLabel_("模型")
        self.model_popup = model_popup
        self._model_popups["model"] = ModelPopup(model_popup, "model")
        y += T.ROW_GAP
        y, self.fields["model"], self.field_wells["model"] = form_row(
            body, "自定义模型 ID（在此填写时优先于上方选择）", claude_custom, False,
            y, field_width)
        y += T.ROW_GAP
        # Optional auto mode classifier. "不设置" leaves CLAUDE_CODE_AUTO_MODE_MODEL
        # entirely alone -- not written, not backfilled, not removed.
        auto_index = model_popup_index(known, claude_auto_model, claude_auto_custom, offset=1)
        y, auto_popup = popup_row(
            body, "Auto mode 分类模型（可选）", y, field_width,
            ModelPopup.titles(options, AUTO_MODEL_PLACEHOLDER), auto_index)
        auto_popup.setAccessibilityLabel_("Auto mode 分类模型")
        self.auto_popup = auto_popup
        self._model_popups["auto_model"] = ModelPopup(
            auto_popup, "auto_model", offset=1, placeholder=AUTO_MODEL_PLACEHOLDER)
        y += T.ROW_GAP
        y += stretch_label(
            body,
            "Auto mode 用该模型判断每条命令是否安全。建议选轻量模型：分类模型偏重时判断可能超时，工具调用会被拦下重试。",
            y, field_width, 12, color=T.tone("info")) + T.ROW_GAP
        y, self.fields["auto_model"], self.field_wells["auto_model"] = form_row(
            body, "Auto mode 自定义模型 ID（优先于上方选择；留空且选「不设置」时不写入）",
            claude_auto_custom, False, y, field_width)
        y += T.ROW_GAP
        # Optional explicit subagent model. Unlike auto mode, "default" keeps the
        # tier rule: an existing CLAUDE_CODE_SUBAGENT_MODEL is preserved and an
        # absent one is backfilled with the main model; a choice overrides both.
        subagent_index = model_popup_index(
            known, claude_subagent_model, claude_subagent_custom, offset=1)
        y, subagent_popup = popup_row(
            body, "Subagent 模型（可选）", y, field_width,
            ModelPopup.titles(options, SUBAGENT_MODEL_PLACEHOLDER), subagent_index)
        subagent_popup.setAccessibilityLabel_("Subagent 模型")
        self.subagent_popup = subagent_popup
        self._model_popups["subagent_model"] = ModelPopup(
            subagent_popup, "subagent_model", offset=1,
            placeholder=SUBAGENT_MODEL_PLACEHOLDER)
        y += T.ROW_GAP
        y, self.fields["subagent_model"], self.field_wells["subagent_model"] = form_row(
            body, "Subagent 自定义模型 ID（优先于上方选择；留空且选「默认」时沿用现有规则）",
            claude_subagent_custom, False, y, field_width)
        y += T.ROW_GAP
        # A remembered key changes the label but is never echoed into the field.
        key_title = ("推理 API Key（已保存，留空保持不变）" if self.stored_inference_key(provider)
                     else f"推理 API Key（必填{spec.inference_key_hint}）")
        for key, title, value, secure in (
            ("base_url", "Anthropic Base URL（留空使用服务商默认地址）", claude_base_url, False),
            ("api_key", key_title, "", True),
        ):
            y, self.fields[key], self.field_wells[key] = form_row(
                body, title, value, secure, y, field_width)
            y += T.ROW_GAP
        if not spec.claude_base_url_verified:
            y += stretch_label(body,
                               "⚠ 该服务商的 Anthropic 兼容端点未经验证，请填写服务商提供的地址并自行确认。",
                               y, field_width, 12,
                               color=T.tone("warning")) + T.ROW_GAP
        self.apply_claude_button = _pin_left(button(body, "写入 Claude 配置", T.PAGE_INSET, y, 160,
                                                    self.actions, "apply-claude"))
        self.apply_claude_button.setAccessibilityLabel_("写入 Claude Code 配置")
        y += T.BUTTON_HEIGHT + T.ROW_GAP
        return end_group(body, card_start, y, width)

    def build_settings(self, provider, region, interval, claude_model="",
                       claude_custom="", claude_base_url="",
                       claude_auto_model="", claude_auto_custom="",
                       claude_subagent_model="", claude_subagent_custom="",
                       alert_enabled=None, alert_threshold=None, alert_reset=None):
        """(Re)create the settings form for one provider."""
        self.settings_provider = provider
        stored_alert = self.storage.load_settings().alert
        if alert_enabled is None:
            alert_enabled = stored_alert.enabled
        if alert_threshold is None:
            alert_threshold = str(stored_alert.threshold_percent)
        if alert_reset is None:
            alert_reset = stored_alert.notify_on_reset
        spec = get_provider(provider)
        try:
            self.existing = self.storage.load_credentials(provider)
        except QueryError as exc:
            self.existing = None
            alert(exc.message)
        try:
            self.inference_key = self.storage.load_inference_key(provider)
        except QueryError as exc:
            self.inference_key = None
            alert(exc.message)
        self.settings.setTitle_(f"设置 · {spec.label}用量监控")
        # Keep the width the user dragged the window to. Every wrapped label here
        # is measured at this width and the window may only grow wider, so a
        # wider form is safe -- and rebuilding at 500pt on every provider switch
        # threw away the layout they chose, now more obvious because the group
        # cards follow the width too.
        width = SETTINGS_WIDTH
        if self.settings is not None:
            width = max(width, round(self.settings.contentView().bounds().size.width))
        field_width = width - 2 * T.PAGE_INSET
        root = T.SurfaceView.alloc().initWithFrame_(rect(0, 0, width, SETTINGS_MIN_HEIGHT))
        root.setWantsLayer_(True)
        root.fill = "page"
        root.setAutoresizingMask_(A.NSViewWidthSizable | A.NSViewHeightSizable)
        self.settings.setContentView_(root)
        self.settings_root = root

        # Pinned header: the provider choice stays reachable while the form scrolls.
        y = T.ROW_GAP
        y += stretch_label(root, "服务商", y, field_width, 13) + T.LABEL_GAP
        popup = _stretch(A.NSPopUpButton.alloc().initWithFrame_pullsDown_(
            rect(T.PAGE_INSET, y, field_width, T.FIELD_HEIGHT), False))
        popup.addItemsWithTitles_([item.label for item in PROVIDERS])
        popup.selectItemAtIndex_([item.id for item in PROVIDERS].index(provider))
        popup.setFont_(T.font("body"))
        popup.setAccessibilityLabel_("服务商")
        popup.setTarget_(self.actions)
        popup.setAction_("menuAction:")
        popup.setIdentifier_("provider")
        root.addSubview_(popup)
        self.provider_popup = popup
        y += T.FIELD_HEIGHT + T.LABEL_GAP
        # Validation and write results live in the pinned header, not at the end
        # of the scrollable document. Down there they were invisible: the window
        # is capped at 640pt, focusing the offending field scrolls that field
        # into view rather than the message, and the smoke run could not tell --
        # it reads stringValue(), which answers whether the view exists, not
        # whether the user can see it.
        self.setting_error = _stretch(text_label(root, "", T.PAGE_INSET, y, field_width,
                                                 role="detail", color=T.tone("error")))
        self.settings_error_top = y
        self.setting_error.setHidden_(True)
        self.settings_header_height = y + SETTINGS_ERROR_HEIGHT + T.GROUP_GAP

        self.settings_scroll = A.NSScrollView.alloc().initWithFrame_(rect(0, 0, width, 100))
        self.settings_scroll.setHasVerticalScroller_(True)
        self.settings_scroll.setBorderType_(A.NSNoBorder)
        self.settings_scroll.setDrawsBackground_(False)
        self.settings_scroll.setAutoresizingMask_(A.NSViewWidthSizable | A.NSViewHeightSizable)
        root.addSubview_(self.settings_scroll)
        body = FlippedView.alloc().initWithFrame_(rect(0, 0, width, 100))
        body.setAutoresizingMask_(A.NSViewWidthSizable)
        self.settings_scroll.setDocumentView_(body)
        self.settings_body = body

        self.settings_footer = T.SurfaceView.alloc().initWithFrame_(
            rect(0, 0, width, SETTINGS_FOOTER_HEIGHT))
        self.settings_footer.setWantsLayer_(True)
        self.settings_footer.fill = "page"
        self.settings_footer.setAutoresizingMask_(A.NSViewWidthSizable)
        _stretch(T.surface(self.settings_footer, 0, 0, width, T.SEAM_HEIGHT, fill="seam"))
        root.addSubview_(self.settings_footer)

        y = T.ROW_GAP
        # Group cards instead of one long flat column: the eye needs the
        # surfaces to know where "connect to the provider" stops and "wire up
        # Claude Code" starts. Each group is its own builder.
        y = self._build_credentials_group(body, y, field_width, width, spec, region, interval)
        y = self._build_alert_group(body, y, field_width, width,
                                    alert_enabled, alert_threshold, alert_reset)

        y = self._build_claude_group(
            body, y, field_width, width, provider, spec,
            claude_model, claude_custom, claude_base_url,
            claude_auto_model, claude_auto_custom,
            claude_subagent_model, claude_subagent_custom)

        # The credential walkthrough stays at the document's end, so expanding it
        # only grows the scrollable height -- no window resize and no buttons to
        # shove about. Feedback moved up into the pinned header above.
        self.help_text = _stretch(text_label(body, spec.help_text, y, field_width, 12, color=SECONDARY))
        self._settings_help_height = self.help_text.frame().size.height + 12
        self.help_text.setHidden_(True)
        self.settings_body.setFrameSize_(F.NSMakeSize(width, max(1, y)))

        self.cancel_button = _pin_right(button(self.settings_footer, "取消", width - 248, 12, 100,
                                               self.actions, "cancel", key="\x1b"))
        self.save_button = _pin_right(button(self.settings_footer, "保存设置", width - 136, 12, 112,
                                             self.actions, "save", key="\r"))
        # Explicit order: the Claude fields sit between the monitor settings and
        # the help toggle, and the write button carries no key equivalent so an
        # accidental Enter can never trigger an external config change.
        ordered = [self.provider_popup, self.fields["ak"], self.fields["sk"], self.fields["region"],
                   self.fields["interval"],
                   self.alert_enabled_button, self.fields["alert_threshold"],
                   self.alert_reset_button, self.model_refresh_button,
                   self.model_popup, self.fields["model"],
                   self.auto_popup, self.fields["auto_model"],
                   self.subagent_popup, self.fields["subagent_model"],
                   self.fields["base_url"], self.fields["api_key"], self.help_button,
                   self.cancel_button, self.apply_claude_button, self.save_button]
        for current, following in zip(ordered, ordered[1:] + [self.provider_popup]):
            current.setNextKeyView_(following)
        self._settings_body_height = y
        self.settings.setContentMinSize_(F.NSMakeSize(SETTINGS_WIDTH, SETTINGS_MIN_HEIGHT))
        self.settings.setContentSize_(F.NSMakeSize(
            width, min(SETTINGS_MAX_HEIGHT,
                       max(SETTINGS_MIN_HEIGHT,
                           y + self.settings_header_height + SETTINGS_FOOTER_HEIGHT + 16))))
        self.layout_settings()
        self.settings.makeFirstResponder_(self.fields["sk" if self.existing else "ak"])
        self.refresh_field_focus()

    def refresh_field_focus(self):
        """Light the ring on the row actually being edited, and only that one.

        Two conditions, matching how macOS draws focus: the field must own the
        shared field editor (``currentEditor`` is non-nil only then, so this is
        an identity test against the window's first responder rather than a guess
        about how focus moved), and the window must be key. Without the second,
        the ring stayed lit on a bright blue outline after the user clicked into
        another application, because AppKit keeps the first responder in place.
        """
        wells = getattr(self, "field_wells", None)
        if not wells or self.settings is None:
            return
        responder = self.settings.firstResponder() if self.settings.isKeyWindow() else None
        # The nil check matters: before the form has installed its field editor
        # (and while it is being torn down) firstResponder is nil, and every
        # field's currentEditor is nil too -- nil is nil, so all rows would light.
        lit = responder is not None
        for key, field in self.fields.items():
            wells[key].set_focused(lit and responder is field.currentEditor())

    def layout_settings(self):
        """Follow the window's size without rebuilding, which would lose input.

        Width is carried by autoresizing masks; this only resizes the scroll
        document and re-pins the footer. The window may grow wider but never
        narrower than SETTINGS_WIDTH: label heights were all measured at that
        width, so shrinking would clip wrapped text.
        """
        # The views are built early in build_settings but the two height fields
        # only near its end, so guard on all of them: a resize arriving before the
        # first build completes must be a no-op, not an AttributeError.
        if not all(hasattr(self, name) for name in (
                "settings_root", "settings_scroll", "settings_footer", "settings_body",
                "settings_header_height", "_settings_body_height", "_settings_help_height")):
            return
        bounds = self.settings_root.bounds()
        footer_top = bounds.size.height - SETTINGS_FOOTER_HEIGHT
        self.settings_footer.setFrame_(rect(0, footer_top, bounds.size.width, SETTINGS_FOOTER_HEIGHT))
        self.settings_scroll.setFrame_(rect(
            0, self.settings_header_height, bounds.size.width,
            max(0, footer_top - self.settings_header_height)))
        clip_width = self.settings_scroll.contentView().bounds().size.width
        self.settings_body.setFrameSize_(F.NSMakeSize(
            max(1, clip_width), self._current_body_height()))

    def _current_body_height(self):
        extra = self._settings_help_height if self.help_expanded else 0
        return max(1, self._settings_body_height + extra)

    def toggle_credential_help(self):
        self.help_expanded = not self.help_expanded
        self.help_text.setHidden_(not self.help_expanded)
        self.help_button.setState_(A.NSControlStateValueOn if self.help_expanded else A.NSControlStateValueOff)
        self.help_button.setImage_(A.NSImage.imageWithSystemSymbolName_accessibilityDescription_("chevron.down" if self.help_expanded else "chevron.right", None))
        self.help_button.setAccessibilityLabel_("收起凭据说明" if self.help_expanded else "展开凭据说明")
        # The footer is pinned and the body scrolls, so expanding the help text
        # just grows the document — no window resize, no buttons to reposition.
        self.layout_settings()

    def save_settings(self):
        try:
            settings, credentials = validate_configuration(
                self.settings_provider,
                *(str(self.fields[key].stringValue()) for key in ("ak", "sk", "region", "interval")),
                self.existing,
                alert_enabled=self.alert_enabled_button.state() == A.NSControlStateValueOn,
                alert_threshold=str(self.fields["alert_threshold"].stringValue()),
                alert_reset=self.alert_reset_button.state() == A.NSControlStateValueOn)
            self.storage.save_credentials(credentials, settings.provider)
            self.storage.save_settings(settings)
        except FieldValidationError as exc:
            # Route through show_form_message: writing the label directly leaves
            # whatever tone the last message set, so an error after a successful
            # Claude write rendered in success green.
            self.show_form_message(exc.message, "error")
            self.settings.makeFirstResponder_(self.fields[exc.field])
            # Focus was moved by code, which posts no editing notification: the
            # ring has to follow it now rather than at the next window update.
            self.refresh_field_focus()
            return
        except QueryError as exc:
            self.show_form_message(exc.message, "error")
            return
        self.monitor.update_configuration(settings, credentials)
        if settings.alert.enabled:
            # Ask while the user is looking at the dialog, not mid-alert.
            self.notifier.request_authorization()
        # Remember whatever Claude choices are typed (the key stays in memory).
        self.stash_claude_draft(settings.provider)
        self.settings.orderOut_(None)
        self.show_details()

    def claude_form_values(self):
        """Snapshot the Claude section: (popup id, custom, base url, key, prefs).

        prefs deliberately exclude the inference key — that is vault-only.
        """
        known = [option.id for option in self._claude_options]
        # Placeholder and custom-marker selections read as ""; the custom text
        # field wins over the popup (see ClaudeProviderPrefs below).
        popup = self._model_popups["model"].value(known)
        custom = str(self.fields["model"].stringValue()).strip()
        auto_popup = self._model_popups["auto_model"].value(known)
        auto_custom = str(self.fields["auto_model"].stringValue()).strip()
        subagent_popup = self._model_popups["subagent_model"].value(known)
        subagent_custom = str(self.fields["subagent_model"].stringValue()).strip()
        base_url = str(self.fields["base_url"].stringValue()).strip()
        key = str(self.fields["api_key"].stringValue())
        prefs = ClaudeProviderPrefs(
            model="" if custom else popup, custom_model=custom, base_url=base_url,
            auto_mode_model="" if auto_custom else auto_popup,
            auto_mode_custom_model=auto_custom,
            subagent_model="" if subagent_custom else subagent_popup,
            subagent_custom_model=subagent_custom)
        return popup, custom, base_url, key, prefs

    # --- Live model catalogue -------------------------------------------------
    # Fetching runs on a daemon thread and only ever hands results back through
    # _catalog_events; every Cocoa mutation happens in drain_model_catalog on the
    # main tick. A failure keeps the static catalogue, so the dialog never blocks
    # on the network and never loses its model list.

    def _catalog_status_for(self, provider):
        if provider in self._catalog_status:
            return self._catalog_status[provider]
        try:
            fetched = self.storage.load_model_catalog(provider)
        except QueryError:
            fetched = ()
        if fetched:
            return (f"已缓存 {len(fetched)} 个在线模型", "neutral")
        return ("使用内置模型清单，可在线刷新", "neutral")

    def _set_catalog_status(self, provider, text, tone):
        self._catalog_status[provider] = (text, tone)
        if (self.settings is not None and self.settings.isVisible()
                and self.settings_provider == provider
                and self.model_status_label is not None):
            color = SECONDARY if tone == "neutral" else T.tone(tone)
            self.model_status_label.setTextColor_(color)
            self.model_status_label.setStringValue_(text)

    def _catalog_fetch_target(self, provider):
        """Resolve (base_url, key) for a fetch from the live form, then defaults."""
        spec = get_provider(provider)
        base_url = key = ""
        on_this_provider = (self.settings is not None
                            and self.settings_provider == provider)
        if on_this_provider and "base_url" in self.fields:
            base_url = str(self.fields["base_url"].stringValue()).strip()
            key = str(self.fields["api_key"].stringValue()).strip()
        if not base_url:
            region = ""
            if on_this_provider and "region" in self.fields:
                region = str(self.fields["region"].stringValue()).strip()
            base_url = spec.default_claude_base_url(region or spec.default_region)
        if not key:
            key = self.stored_inference_key(provider) or ""
        return base_url, key

    def _maybe_auto_fetch(self, provider):
        """Auto-pull on open / provider switch, only when a key and URL exist."""
        if provider in self._catalog_inflight:
            return
        base_url, key = self._catalog_fetch_target(provider)
        if not key or claude_code.ANTHROPIC_PATH not in base_url:
            return
        self._start_model_fetch(provider, base_url, key)

    def refresh_models(self):
        """Manual refresh button: force a re-pull with the current form values."""
        provider = self.settings_provider
        if provider in self._catalog_inflight:
            return
        base_url, key = self._catalog_fetch_target(provider)
        if not key:
            self._set_catalog_status(provider, "请先填写推理 API Key", "warning")
            return
        if claude_code.ANTHROPIC_PATH not in base_url:
            self._set_catalog_status(provider, "该 Base URL 不支持在线获取模型", "warning")
            return
        self._start_model_fetch(provider, base_url, key)

    def _start_model_fetch(self, provider, base_url, key):
        self._catalog_inflight.add(provider)
        self._set_catalog_status(provider, "正在在线获取模型列表…", "neutral")
        threading.Thread(
            target=self._fetch_models_worker,
            args=(provider, base_url, key),
            name=f"model-catalog-{provider}",
            daemon=True,
        ).start()

    def _fetch_models_worker(self, provider, base_url, key):
        try:
            ids = self._fetch_model_ids(base_url, key)
            self._catalog_events.put((provider, ids, None))
        except QueryError as exc:
            self._catalog_events.put((provider, None, exc.message))
        except Exception:
            logger.exception("Unexpected model catalogue fetch failure")
            self._catalog_events.put((provider, None, "模型列表获取失败"))

    def drain_model_catalog(self):
        while True:
            try:
                provider, ids, error = self._catalog_events.get_nowait()
            except queue.Empty:
                return
            self._catalog_inflight.discard(provider)
            if error is not None:
                self._set_catalog_status(
                    provider, f"在线获取失败：{error}，已用内置清单", "warning")
                continue
            try:
                self.storage.save_model_catalog(provider, ids)
            except QueryError:
                pass
            self._set_catalog_status(
                provider, f"已在线更新模型列表（{len(ids)} 个）", "ok")
            if (self.settings is not None and self.settings.isVisible()
                    and self.settings_provider == provider
                    and not self._applying_claude):
                options = merge_model_options(get_provider(provider).claude_models, ids)
                self._apply_catalog_options(options)

    def _apply_catalog_options(self, options):
        """Rebuild just the model popups in place, preserving the selection.

        A full form rebuild would discard typing in the credential fields and
        bounce focus, so only the popups are touched. A selected model the plan
        no longer offers is demoted to the custom field rather than lost.
        """
        if (self.model_popup is None or self.auto_popup is None
                or self.subagent_popup is None):
            return
        popup, custom, _base, _key, prefs = self.claude_form_values()
        known = [option.id for option in options]
        self._model_popups["model"].repopulate(options, known, popup, custom, self.fields)
        self._model_popups["auto_model"].repopulate(
            options, known,
            prefs.auto_mode_custom_model or prefs.auto_mode_model,
            prefs.auto_mode_custom_model, self.fields)
        self._model_popups["subagent_model"].repopulate(
            options, known,
            prefs.subagent_custom_model or prefs.subagent_model,
            prefs.subagent_custom_model, self.fields)
        self._claude_options = options

    def show_form_message(self, text, tone="error"):
        """Inline form feedback, tinted by tone role.

        ``tone`` names a theme role ("error"/"ok"/"warning") or "neutral"; each
        is a dynamic color, so it stays correct across an appearance switch.
        """
        color = SECONDARY if tone == "neutral" else T.tone(tone)
        self.setting_error.setTextColor_(color)
        self.setting_error.setStringValue_(text)
        self.setting_error.setHidden_(not text)
        if not text:
            self._set_error_band(SETTINGS_ERROR_HEIGHT)
            return
        bounds = self.setting_error.attributedStringValue().boundingRectWithSize_options_(
            F.NSMakeSize(self.setting_error.frame().size.width, 100000),
            A.NSStringDrawingUsesLineFragmentOrigin | A.NSStringDrawingUsesFontLeading)
        self._set_error_band(max(SETTINGS_ERROR_HEIGHT, round(bounds.size.height) + 6))

    def _set_error_band(self, height):
        """Size the pinned feedback band and let the scroll view follow it.

        Two lines are reserved because most messages are one; a wrapped Claude
        write result is longer and must not clip, and growing the band is safer
        than clamping the text -- nothing in the form is rebuilt, so typed input
        survives the reflow.
        """
        if self.setting_error.frame().size.height == height:
            return
        self.setting_error.setFrameSize_(
            F.NSMakeSize(self.setting_error.frame().size.width, height))
        self.settings_header_height = self.settings_error_top + height + T.GROUP_GAP
        self.layout_settings()

    def confirm_claude_write(self, lines):
        if self._confirm_override is not None:
            return bool(self._confirm_override)
        box = A.NSAlert.alloc().init()
        box.setMessageText_("写入 Claude Code 配置？")
        box.setInformativeText_("\n".join(lines))
        box.addButtonWithTitle_("确认写入")
        box.addButtonWithTitle_("取消")
        box.setAlertStyle_(A.NSAlertStyleWarning)
        return box.runModal() == A.NSAlertFirstButtonReturn

    def apply_claude_config(self):
        """Validate, confirm, then write the provider + model into Claude Code."""
        if self._applying_claude:
            return
        self._applying_claude = True
        try:
            provider = self.settings_provider
            popup, custom, base_url, key_text, prefs = self.claude_form_values()
            region = str(self.fields["region"].stringValue())
            existing_key = self.stored_inference_key(provider)
            settings_path, onboarding_path = self.resolve_claude_paths()
            try:
                request = validate_claude_configuration(
                    provider, region, popup, custom, base_url, key_text, existing_key,
                    prefs.auto_mode_model, prefs.auto_mode_custom_model,
                    prefs.subagent_model, prefs.subagent_custom_model)
            except FieldValidationError as exc:
                self.show_form_message(exc.message, "error")
                self.settings.makeFirstResponder_(self.fields[exc.field])
                self.refresh_field_focus()
                return
            except QueryError as exc:
                self.show_form_message(exc.message, "error")
                return
            try:
                document, had_comments = claude_code.read_document_at(settings_path)
            except ClaudeConfigError as exc:
                self.show_form_message(
                    f"{exc.message}；原始文件未被修改，请先修复后重试。", "error")
                return
            patch = claude_code.compute_env_patch(request, document)
            changes = claude_code.describe_changes(
                request, patch, document, settings_path, had_comments)
            if not self.confirm_claude_write(changes):
                self.show_form_message("已取消，未改动 Claude 配置。", "neutral")
                return
            try:
                result = claude_code.apply_request(request, settings_path)
                # The vault now holds exactly the key that was written into the
                # config, whether it came from the field or from a draft.
                self.storage.save_inference_key(request.api_key, provider)
                self.inference_key = request.api_key
                self._claude_key_drafts.pop(provider, None)
                self.fields["api_key"].setStringValue_("")
                applied = ClaudeProviderPrefs(model=request.model, custom_model="",
                                              base_url=request.base_url)
                # Drop the session draft: the file we just wrote is now reality,
                # and reality must outrank the cache on the next open (a later
                # hand-edit of the endpoint has to win, not be silently reverted).
                self._claude_drafts.pop(provider, None)
                self.storage.save_claude_prefs(applied, provider)
            except QueryError as exc:  # covers ClaudeConfigError, CacheError, keychain failures
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
            self._applying_claude = False

    def shutdown(self):
        if self.closed:
            return
        self.closed = True
        # Never leave a drafted inference key reachable past this process.
        self._claude_key_drafts.clear()
        self._claude_drafts.clear()
        if self.timer:
            self.timer.invalidate()
        self.monitor.on_state = None
        self.monitor.stop()
        F.NSNotificationCenter.defaultCenter().removeObserver_(self.actions)
        A.NSWorkspace.sharedWorkspace().notificationCenter().removeObserver_(self.actions)
        A.NSDistributedNotificationCenter.defaultCenter().removeObserver_(self.actions)
        for window in (self.details, self.settings, self.card, self.history):
            if window:
                window.orderOut_(None)
        if self.status_item:
            A.NSStatusBar.systemStatusBar().removeStatusItem_(self.status_item)
            self.status_item = None
