"""History chart rendering: CAShapeLayer + CGPath line/area geometry.

Declarative layer geometry only -- no ``drawRect_``, no ``NSBezierPath`` (the
theme rule, clarified for exactly this module). All Cocoa objects are owned
and mutated on the main thread. The details-style document is rebuilt from
scratch on every render/resize, so chart views are discarded wholesale:
tracking areas re-register through ``updateTrackingAreas`` and implicit path
animations are suppressed at construction, which is what keeps a refresh from
re-playing the curve.

Appearance follows the SurfaceView pattern: every colored layer is registered
with a role, and ``updateLayer`` re-resolves the dynamic NSColor to a CGColor
with the view's effective appearance current.
"""
from __future__ import annotations

import AppKit as A
import Foundation as F
import Quartz
from objc import super

from ..history_chart import (
    PLOT_HEIGHT,
    X_AXIS_HEIGHT,
    Y_AXIS_WIDTH,
    ChartGeometry,
    nearest_sample,
    x_scale,
    y_scale,
)
from ..presentation import (
    HISTORY_RESET_LABEL,
    format_number,
    history_axis_time,
    history_chart_accessibility,
    history_table_time,
    history_threshold_label,
    history_tooltip_lines,
)
from . import theme as T

# Role -> dynamic color. Resolved inside updateLayer, never at build time.
_ROLE_COLORS = {
    "series": T.tone("info"),
    "series-fill": T.tone("info"),
    "area": T.CHART_AREA,
    "grid": T.CHART_GRID,
    "reset": T.CHART_GRID,
    "threshold": T.tone("warning"),
    "crosshair": T.SECONDARY,
    "ring": T.CARD,
}
# Role -> the CGColor attribute it paints.
_ROLE_ATTRS = {
    "series": ("strokeColor",),
    "series-fill": ("backgroundColor",),
    "area": ("fillColor",),
    "grid": ("backgroundColor",),
    "reset": ("backgroundColor",),
    "threshold": ("strokeColor",),
    "crosshair": ("backgroundColor",),
    "ring": ("backgroundColor",),
}


def _rect(x, y, w, h):
    return F.NSMakeRect(x, y, w, h)


def _text_width(text, font):
    return F.NSString.stringWithString_(text).sizeWithAttributes_(
        {A.NSFontAttributeName: font}).width


def _axis_label(parent, text, x, y, width, *, align="left", color=None, name=None):
    """A single-line axis/annotation label. Non-wrapping: the chart measures
    its own geometry, and a wrapped tick would collide with the plot."""
    view = A.NSTextField.labelWithString_(text)
    font = T.label_font(role="caption", numeric=True)
    view.setFont_(font)
    view.setTextColor_(color or T.SECONDARY)
    if align == "right":
        view.setAlignment_(A.NSTextAlignmentRight)
    elif align == "center":
        view.setAlignment_(A.NSTextAlignmentCenter)
    view.setFrame_(_rect(x, y, width, T.role_size("caption") + 6))
    if name is not None:
        view.setIdentifier_(name)
    parent.addSubview_(view)
    return view


class ChartView(A.NSView):
    """One window's used-percent curve over the selected range.

    Frame height is PLOT_HEIGHT + X_AXIS_HEIGHT; the plot origin sits at
    (Y_AXIS_WIDTH, 0) so the y-axis gutter lives inside the same view.
    """

    def isFlipped(self):
        return True

    def wantsUpdateLayer(self):
        return True

    def initWithFrame_(self, frame):
        self = super(ChartView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.setWantsLayer_(True)
        self.geometry = None
        self.unit_label = ""
        self._color_layers = []   # (layer, role) pairs re-resolved on appearance
        self._crosshair = None
        self._tooltip = None
        self._tracking_area = None
        return self

    # --- construction --------------------------------------------------------

    def set_chart(self, geometry: ChartGeometry, unit_label: str) -> None:
        self.geometry = geometry
        self.unit_label = unit_label
        for layer in list(self.layer().sublayers() or []):
            layer.removeFromSuperlayer()
        for view in list(self.subviews()):
            view.removeFromSuperview()
        self._color_layers = []
        self._crosshair = None
        self._tooltip = None
        Quartz.CATransaction.begin()
        Quartz.CATransaction.setDisableActions_(True)
        try:
            self._build_layers()
            self._build_labels()
        finally:
            Quartz.CATransaction.commit()
        self.setAccessibilityElement_(True)
        self.setAccessibilityRole_(A.NSAccessibilityImageRole)
        self.setAccessibilityLabel_(
            history_chart_accessibility(geometry.kind, geometry.latest, geometry.range_key))
        self.setNeedsDisplay_(True)

    def _add_layer(self, layer, role, name):
        layer.setName_(name)
        self.layer().addSublayer_(layer)
        self._color_layers.append((layer, role))
        return layer

    def _plot_width(self):
        return max(0.0, self.frame().size.width - Y_AXIS_WIDTH)

    def _build_layers(self):
        geometry = self.geometry
        plot_width = self._plot_width()
        plot_height = geometry.plot_height
        # Grid: solid hairlines at 0 / 50 / 100, recessive by design.
        for percent, y in geometry.y_ticks:
            grid = Quartz.CALayer.alloc().init()
            grid.setFrame_(_rect(Y_AXIS_WIDTH, y, plot_width, 1))
            self._add_layer(grid, "grid", f"grid-{percent:.0f}")
        # One line + area pair per cycle; the path NEVER crosses a reset.
        for index, cycle in enumerate(geometry.cycles):
            if len(cycle.points) < 2:
                continue
            path = Quartz.CGPathCreateMutable()
            first = cycle.points[0]
            Quartz.CGPathMoveToPoint(path, None, Y_AXIS_WIDTH + first[0], first[1])
            for x, y in cycle.points[1:]:
                Quartz.CGPathAddLineToPoint(path, None, Y_AXIS_WIDTH + x, y)
            area_path = Quartz.CGPathCreateMutableCopy(path)
            last = cycle.points[-1]
            Quartz.CGPathAddLineToPoint(area_path, None, Y_AXIS_WIDTH + last[0], plot_height)
            Quartz.CGPathAddLineToPoint(area_path, None, Y_AXIS_WIDTH + first[0], plot_height)
            Quartz.CGPathCloseSubpath(area_path)
            area = Quartz.CAShapeLayer.alloc().init()
            area.setPath_(area_path)
            area.setFillColor_(T.CHART_AREA.CGColor())
            area.setStrokeColor_(None)
            self._add_layer(area, "area", f"area-{index}")
            line = Quartz.CAShapeLayer.alloc().init()
            line.setPath_(path)
            line.setLineWidth_(2)
            line.setLineJoin_(Quartz.kCALineJoinRound)
            line.setLineCap_(Quartz.kCALineCapRound)
            line.setStrokeColor_(T.tone("info").CGColor())
            line.setFillColor_(None)
            self._add_layer(line, "series", f"series-{index}")
        # Alert threshold: a dashed reference line (dashes read "threshold",
        # which is exactly what it is -- the solid grid never dashes).
        threshold = Quartz.CAShapeLayer.alloc().init()
        threshold_path = Quartz.CGPathCreateMutable()
        Quartz.CGPathMoveToPoint(threshold_path, None, Y_AXIS_WIDTH, geometry.threshold_y)
        Quartz.CGPathAddLineToPoint(threshold_path, None, Y_AXIS_WIDTH + plot_width,
                                    geometry.threshold_y)
        threshold.setPath_(threshold_path)
        threshold.setLineWidth_(1)
        threshold.setLineDashPattern_([5, 4])
        threshold.setStrokeColor_(T.tone("warning").CGColor())
        threshold.setFillColor_(None)
        self._add_layer(threshold, "threshold", "threshold")
        # Reset hairlines, density-gated by the pure module.
        for index, (x, _show) in enumerate(geometry.reset_marks):
            mark = Quartz.CALayer.alloc().init()
            mark.setFrame_(_rect(Y_AXIS_WIDTH + x, 0, 1, plot_height))
            self._add_layer(mark, "reset", f"reset-{index}")
        # Endpoint: >=8px dot with a 2px surface ring, the one direct label.
        if geometry.latest is not None:
            x = Y_AXIS_WIDTH + x_scale(
                geometry.latest.queried_at, geometry.range_start, geometry.range_end, plot_width)
            y = y_scale(geometry.latest.used_percent, plot_height)
            ring = Quartz.CALayer.alloc().init()
            ring.setFrame_(_rect(x - 6, y - 6, 12, 12))
            ring.setCornerRadius_(6)
            self._add_layer(ring, "ring", "endpoint-ring")
            dot = Quartz.CALayer.alloc().init()
            dot.setFrame_(_rect(x - 4, y - 4, 8, 8))
            dot.setCornerRadius_(4)
            self._add_layer(dot, "series-fill", "endpoint")
        # Crosshair: hidden until hover; the whole plot width is the hit target.
        crosshair = Quartz.CALayer.alloc().init()
        crosshair.setFrame_(_rect(Y_AXIS_WIDTH, 0, 1, plot_height))
        crosshair.setHidden_(True)
        self._crosshair = self._add_layer(crosshair, "crosshair", "crosshair")

    def _build_labels(self):
        geometry = self.geometry
        plot_width = self._plot_width()
        plot_height = geometry.plot_height
        view_width = self.frame().size.width
        caption_height = T.role_size("caption") + 6
        # Y ticks right-aligned in the gutter, clamped inside the view.
        for percent, y in geometry.y_ticks:
            label_y = max(0.0, min(y - caption_height / 2, self.frame().size.height - caption_height))
            _axis_label(self, f"{percent:.0f}", 0, label_y, Y_AXIS_WIDTH - 6,
                        align="right", name=f"ytick-{percent:.0f}")
        # X ticks centered on their time, clamped so edge labels never clip.
        for index, (x, moment) in enumerate(geometry.x_ticks):
            text = history_axis_time(moment, geometry.range_key)
            width = _text_width(text, T.label_font(role="caption", numeric=True)) + 4
            center = Y_AXIS_WIDTH + x
            left = max(Y_AXIS_WIDTH, min(center - width / 2, view_width - width))
            _axis_label(self, text, left, plot_height + 2, width,
                        align="center", name=f"xtick-{index}")
        # Reset captions live inside the plot's bottom edge so they never
        # collide with the x-axis band; only shown where spacing allows.
        # Right-aligned LEFT of the hairline: the new cycle's line starts at
        # the mark and rises right, while left of it only the previous
        # cycle's 10% wash reaches the bottom -- text stays off the line.
        for index, (x, show) in enumerate(geometry.reset_marks):
            if not show:
                continue
            width = _text_width(HISTORY_RESET_LABEL, T.label_font(role="caption")) + 4
            left = max(Y_AXIS_WIDTH, Y_AXIS_WIDTH + x - width - 3)
            _axis_label(self, HISTORY_RESET_LABEL, left, plot_height - caption_height - 2,
                        width, align="right", name=f"reset-caption-{index}")
        # Threshold label: text wears a text token, never the series color.
        text = history_threshold_label(geometry.threshold_used_percent)
        font = T.label_font(role="caption", numeric=True)
        width = _text_width(text, font) + 4
        left = max(Y_AXIS_WIDTH, view_width - width - 2)
        top = max(0.0, geometry.threshold_y - caption_height - 1)
        _axis_label(self, text, left, top, width, align="right", name="threshold-label")
        # The single direct value label: the latest sample, beside its dot.
        if geometry.latest is not None:
            text = f"{geometry.latest.used_percent:.1f}%"
            font = T.label_font(role="caption", bold=True, numeric=True)
            width = _text_width(text, font) + 4
            x = Y_AXIS_WIDTH + x_scale(
                geometry.latest.queried_at, geometry.range_start, geometry.range_end, plot_width)
            y = y_scale(geometry.latest.used_percent, plot_height)
            left = x + 8 if x + 8 + width <= view_width else max(Y_AXIS_WIDTH, x - width - 8)
            # Above the dot, never centered on the line it names; a dot at the
            # very top flips the label below instead of clipping it.
            top = y - caption_height - 4
            if top < 0:
                top = min(y + 8, plot_height - caption_height)
            view = _axis_label(self, text, left, top, width, color=T.TEXT, name="endpoint-label")
            view.setFont_(font)

    # --- appearance ----------------------------------------------------------

    def updateLayer(self):
        for layer, role in self._color_layers:
            color = _ROLE_COLORS[role].CGColor()
            for attr in _ROLE_ATTRS[role]:
                if attr == "backgroundColor":
                    layer.setBackgroundColor_(color)
                elif attr == "strokeColor":
                    layer.setStrokeColor_(color)
                else:
                    layer.setFillColor_(color)

    def viewDidChangeEffectiveAppearance(self):
        self.setNeedsDisplay_(True)
        super(ChartView, self).viewDidChangeEffectiveAppearance()

    # --- hover ---------------------------------------------------------------

    def updateTrackingAreas(self):
        if self._tracking_area is not None:
            self.removeTrackingArea_(self._tracking_area)
        self._tracking_area = A.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            self.bounds(),
            A.NSTrackingMouseMoved | A.NSTrackingActiveInKeyWindow | A.NSTrackingInVisibleRect,
            self, None)
        self.addTrackingArea_(self._tracking_area)
        super(ChartView, self).updateTrackingAreas()

    def mouseMoved_(self, event):
        if self.geometry is None:
            return
        point = self.convertPoint_fromView_(event.locationInWindow(), None)
        x_plot = point.x - Y_AXIS_WIDTH
        if 0 <= x_plot <= self._plot_width() and 0 <= point.y <= self.geometry.plot_height:
            self.show_tooltip_at(x_plot)
        else:
            self.hide_tooltip()

    def mouseExited_(self, event):
        self.hide_tooltip()

    def tooltip_lines_at(self, x_plot: float) -> tuple[str, ...] | None:
        """Pure readout for the crosshair position (smoke calls this directly)."""
        if self.geometry is None:
            return None
        sample = nearest_sample(self.geometry, x_plot)
        if sample is None:
            return None
        return history_tooltip_lines(sample, self.unit_label)

    def show_tooltip_at(self, x_plot: float) -> None:
        lines = self.tooltip_lines_at(x_plot)
        if lines is None or self.geometry is None:
            self.hide_tooltip()
            return
        sample = nearest_sample(self.geometry, x_plot)
        plot_width = self._plot_width()
        crosshair_x = Y_AXIS_WIDTH + x_scale(
            sample.queried_at, self.geometry.range_start, self.geometry.range_end, plot_width)
        self._crosshair.setFrame_(_rect(crosshair_x, 0, 1, self.geometry.plot_height))
        self._crosshair.setHidden_(False)
        self._remove_tooltip()
        # Value leads (13 semibold), labels follow (caption) -- the legend's
        # hierarchy inverted, because here the reader wants the number.
        fonts = [T.label_font(13, bold=True, numeric=True)] + [
            T.label_font(role="caption", numeric=True)] * (len(lines) - 1)
        pad_x, pad_y, gap = 8, 6, 2
        width = max(_text_width(text, font) for text, font in zip(lines, fonts)) + 2 * pad_x
        heights = [font.pointSize() + 4 for font in fonts]
        height = sum(heights) + gap * (len(lines) - 1) + 2 * pad_y
        left = crosshair_x + 8
        if left + width > self.frame().size.width - 4:
            left = crosshair_x - width - 8
        left = max(4.0, left)
        panel = T.surface(self, left, 4, width, height, fill="field",
                          corner=T.RADIUS_FIELD, behind=False)
        panel.setIdentifier_("chart-tooltip")
        y = pad_y
        for index, (text, font) in enumerate(zip(lines, fonts)):
            view = A.NSTextField.labelWithString_(text)
            view.setFont_(font)
            view.setTextColor_(T.TEXT if index == 0 else T.SECONDARY)
            view.setFrame_(_rect(pad_x, y, width - 2 * pad_x, heights[index]))
            panel.addSubview_(view)
            y += heights[index] + gap
        self._tooltip = panel

    def hide_tooltip(self) -> None:
        if self._crosshair is not None:
            self._crosshair.setHidden_(True)
        self._remove_tooltip()

    def _remove_tooltip(self):
        if self._tooltip is not None:
            self._tooltip.removeFromSuperview()
            self._tooltip = None

    def chart_layers(self) -> list:
        """Direct sublayers for smoke assertions (by name, never color reads)."""
        return list(self.layer().sublayers() or [])


class HistoryTableDataSource(F.NSObject):
    """Cell-based NSTableView source for the 数据列表 disclosure: the chart's
    table twin, so no value is reachable only through a tooltip."""

    def initWithRows_columns_(self, rows, columns):
        self = super(HistoryTableDataSource, self).init()
        if self is None:
            return None
        self._rows = rows
        self._columns = tuple(columns)
        return self

    def numberOfRowsInTableView_(self, table):
        return len(self._rows)

    def tableView_objectValueForTableColumn_row_(self, table, column, row):
        if row < 0 or row >= len(self._rows):
            return ""
        entry = self._rows[row]
        key = str(column.identifier())
        if key == "time":
            return history_table_time(entry.queried_at)
        if key == "percent":
            return f"{entry.used_percent:.1f}%"
        if key == "absolute":
            if entry.quota is None or entry.used is None:
                return "—"
            return f"{format_number(entry.used)} / {format_number(entry.quota)}"
        return ""