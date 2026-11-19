"""Usage-history window for the Windows Tk shell.

The Tk counterpart of the macOS history panel. Every rule stays in the shared
pure modules: :mod:`history_chart` turns the raw ``HistoryEntry`` rows into
draw-ready geometry (cycle breaks, buckets, ticks, threshold) and
:mod:`presentation` owns all user-facing copy. This module only paints the
geometry on a ``tk.Canvas`` and wires the window, range buttons and the
disclosure tables.

Honesty rules inherited from the chart backend:

* The line never connects across a reset -- every cycle is its own segment.
* Percent-only plans never gain a fabricated absolute column or tooltip line.
* Thin or missing evidence renders the shared empty captions, never a curve
  built from a single point.
"""
from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from datetime import UTC, datetime
from tkinter import ttk
from typing import Mapping

from .appearance import OUTER_PADDING, RADIUS, SECTION_GAP
from .history_chart import (
    PLOT_HEIGHT,
    X_AXIS_HEIGHT,
    Y_AXIS_WIDTH,
    RANGE_KEYS,
    ChartGeometry,
    build_chart,
    nearest_sample,
    x_scale,
    y_scale,
)
from .models import MonitorViewState, WINDOW_ORDER
from .presentation import (
    HISTORY_RANGE_LABELS,
    HISTORY_RESET_LABEL,
    HISTORY_TABLE_COLLAPSE,
    HISTORY_TABLE_HEADERS,
    HISTORY_TABLE_TITLE,
    HISTORY_WINDOW_TITLE,
    format_number,
    history_absolute_header,
    history_axis_time,
    history_chart_title,
    history_context_line,
    history_empty_text,
    history_table_time,
    history_threshold_label,
    history_tooltip_lines,
)
from .providers import get_provider
from .ui_graphics import (
    RoundedPanel,
    ScrollableBody,
    dpi_scale,
    install_window_effects,
    window_work_area,
)
from .window_chrome import WindowChrome
from .window_geometry import WindowGeometry

#: RoundedPanel reserves inset max(14, radius//2) plus ipadx 12 on each side,
#: so a panel eats a fixed amount of the document's width at any DPI.
_PANEL_CHROME_X = 2 * (RADIUS["panel"] + 12)
#: Fallback plot width before the first geometry pass has measured the doc.
_FALLBACK_PLOT_WIDTH = 360
#: Default window height, matching the macOS history panel; taller documents
#: scroll inside the body instead of growing the window to the screen.
_DEFAULT_HEIGHT = 640
_MIN_BODY_HEIGHT = 220

#: Chart color role -> Tk theme color key. The Tk palette has no dedicated
#: chart tokens, so the roles map onto the existing semantic colors.
_ROLE_KEYS = {
    "series": "accent",
    "area": "info_surface",
    "grid": "border",
    "reset": "border",
    "threshold": "warning",
    "crosshair": "muted",
    "ring": "card",
}


def flatten(points: list[tuple[float, float]]) -> list[float]:
    return [coordinate for point in points for coordinate in point]


class HistoryChart(tk.Canvas):
    """One window's used-percent curve, painted from a ``ChartGeometry``.

    The plot origin sits at ``(gutter, 0)`` so the y-axis labels live inside
    the same canvas, mirroring the macOS ``ChartView`` frame layout. The hover
    tooltip is a private override-redirect Toplevel: ``ui_graphics.Tooltip``
    is an enter/leave widget with static text, while chart hover needs
    per-position content following the crosshair, the same split macOS uses.
    """

    def __init__(self, master: tk.Misc, colors: Mapping[str, str], font_family: str):
        self.scale = dpi_scale(master)
        self.colors = colors
        self.font_family = font_family
        self.caption_font = tkfont.Font(family=font_family, size=8)
        self.endpoint_font = tkfont.Font(family=font_family, size=8, weight="bold")
        self.geometry: ChartGeometry | None = None
        self.unit_label = ""
        self._gutter = round(Y_AXIS_WIDTH * self.scale)
        self._tooltip: tk.Toplevel | None = None
        self._tooltip_holder: tk.Frame | None = None
        self._tooltip_labels: list[tk.Label] = []
        plot_height = round(PLOT_HEIGHT * self.scale)
        super().__init__(
            master,
            height=plot_height + round(X_AXIS_HEIGHT * self.scale),
            bg=colors["card"], highlightthickness=0, borderwidth=0,
        )
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda event: self.hide_tooltip())

    # ------------------------------------------------------------- rendering
    def set_chart(self, geometry: ChartGeometry, unit_label: str) -> None:
        self.geometry = geometry
        self.unit_label = unit_label
        self.hide_tooltip()
        self._draw()

    def set_theme(self, colors: Mapping[str, str]) -> None:
        self.colors = colors
        self.configure(bg=colors["card"])
        self._draw()

    def _draw(self) -> None:
        geometry = self.geometry
        if geometry is None:
            return
        c, s = self.colors, self.scale
        gutter = self._gutter
        plot_width, plot_height = geometry.plot_width, geometry.plot_height
        self.delete("all")
        self.configure(width=max(1, round(gutter + plot_width)))

        def role(name: str) -> str:
            return c[_ROLE_KEYS[name]]

        for percent, y in geometry.y_ticks:
            self.create_line(
                gutter, y, gutter + plot_width, y, fill=role("grid"), tags="grid",
            )
            self.create_text(
                gutter - 6 * s, y, text=f"{percent:.0f}", anchor="e",
                font=self.caption_font, fill=c["muted"], tags="ytick",
            )
        # One line + area pair per cycle; a reset is always a broken path.
        for index, cycle in enumerate(geometry.cycles):
            if len(cycle.points) < 2:
                continue
            points = [(gutter + x, y) for x, y in cycle.points]
            first, last = points[0], points[-1]
            area_points = points + [
                (last[0], plot_height), (first[0], plot_height),
            ]
            self.create_polygon(
                flatten(area_points), fill=role("area"), outline="", tags="area",
            )
            self.create_line(
                flatten(points), fill=role("series"),
                width=max(1, round(2 * s)), capstyle="round",
                joinstyle="round", tags="series",
            )
        # Dashes read "reference line"; the solid grid never dashes.
        self.create_line(
            gutter, geometry.threshold_y, gutter + plot_width, geometry.threshold_y,
            fill=role("threshold"), width=max(1, round(s)),
            dash=(round(5 * s), round(4 * s)), tags="threshold",
        )
        for x, show_caption in geometry.reset_marks:
            self.create_line(
                gutter + x, 0, gutter + x, plot_height,
                fill=role("reset"), width=max(1, round(s)), tags="reset",
            )
            if show_caption:
                # Right-aligned LEFT of the hairline so the new cycle's line
                # never crosses the caption.
                self.create_text(
                    gutter + x - 3 * s, plot_height - 13 * s,
                    text=HISTORY_RESET_LABEL, anchor="e",
                    font=self.caption_font, fill=c["muted"], tags="reset-caption",
                )
        for x, moment in geometry.x_ticks:
            self.create_text(
                gutter + x, plot_height + 3 * s,
                text=history_axis_time(moment, geometry.range_key),
                anchor="n", font=self.caption_font, fill=c["muted"], tags="xtick",
            )
        self.create_text(
            gutter + plot_width, max(0, geometry.threshold_y - 14 * s),
            text=history_threshold_label(geometry.threshold_used_percent),
            anchor="ne", font=self.caption_font, fill=role("threshold"),
            tags="threshold-label",
        )
        if geometry.latest is not None:
            cx = gutter + x_scale(
                geometry.latest.queried_at, geometry.range_start,
                geometry.range_end, plot_width,
            )
            cy = y_scale(geometry.latest.used_percent, plot_height)
            self.create_oval(
                cx - 6 * s, cy - 6 * s, cx + 6 * s, cy + 6 * s,
                fill=role("ring"), outline="", tags="endpoint-ring",
            )
            self.create_oval(
                cx - 4 * s, cy - 4 * s, cx + 4 * s, cy + 4 * s,
                fill=role("series"), outline="", tags="endpoint",
            )
            text = f"{geometry.latest.used_percent:.1f}%"
            left = cx + 8 * s
            anchor = "w"
            if left + self.endpoint_font.measure(text) > gutter + plot_width:
                left, anchor = cx - 8 * s, "e"
            top = cy - 16 * s
            if top < 0:
                top = min(cy + 8 * s, plot_height - 12 * s)
            self.create_text(
                left, top, text=text, anchor=anchor, font=self.endpoint_font,
                fill=c["text"], tags="endpoint-label",
            )
        # The crosshair stays hidden until the hover lands on a real sample.
        self.create_line(
            0, 0, 0, plot_height, fill=role("crosshair"),
            width=max(1, round(s)), state="hidden", tags="crosshair",
        )

    # ----------------------------------------------------------------- hover
    def tooltip_lines_at(self, x_plot: float) -> tuple[str, ...] | None:
        """Pure readout for the crosshair position (smokes call this directly)."""
        if self.geometry is None:
            return None
        sample = nearest_sample(self.geometry, x_plot)
        if sample is None:
            return None
        return history_tooltip_lines(sample, self.unit_label)

    def _on_motion(self, event: tk.Event) -> None:
        geometry = self.geometry
        if geometry is None:
            return
        x_plot = event.x - self._gutter
        sample = nearest_sample(geometry, x_plot)
        if sample is None or not (0 <= event.y <= geometry.plot_height):
            self.hide_tooltip()
            return
        crosshair_x = self._gutter + x_scale(
            sample.queried_at, geometry.range_start, geometry.range_end,
            geometry.plot_width,
        )
        self.coords(
            "crosshair", crosshair_x, 0, crosshair_x, geometry.plot_height,
        )
        self.itemconfigure("crosshair", state="normal")
        lines = history_tooltip_lines(sample, self.unit_label)
        self._show_tooltip(lines, crosshair_x, event.y_root)

    def _show_tooltip(
        self, lines: tuple[str, ...], anchor_x: int, pointer_y: int
    ) -> None:
        c = self.colors
        if self._tooltip is None:
            tip = tk.Toplevel(self)
            tip.overrideredirect(True)
            tip.attributes("-topmost", True)
            holder = tk.Frame(
                tip, bg=c["card"], highlightthickness=1,
                highlightbackground=c["border"], highlightcolor=c["border"],
            )
            holder.pack(padx=8, pady=5)
            self._tooltip = tip
            self._tooltip_holder = holder
            self._tooltip_labels = []
        # The line count only changes between percent-only and absolute plans;
        # rebuild the labels then, otherwise just re-point the text in place.
        if len(self._tooltip_labels) != len(lines):
            for label in self._tooltip_labels:
                label.destroy()
            self._tooltip_labels = []
            for index, line in enumerate(lines):
                label = tk.Label(
                    self._tooltip_holder, text=line, justify="left",
                    padx=2, pady=1, bg=c["card"],
                    fg=c["text"] if index == 0 else c["muted"],
                    font=(self.font_family, 9,
                          "bold" if index == 0 else "normal"),
                )
                label.pack(anchor="w")
                self._tooltip_labels.append(label)
        else:
            for label, line in zip(self._tooltip_labels, lines):
                label.configure(text=line)
        self._tooltip.update_idletasks()
        width = self._tooltip.winfo_reqwidth()
        x = anchor_x + 12
        screen_width = self.winfo_screenwidth()
        if x + width > screen_width - 8:
            x = max(8, anchor_x - width - 12)
        y = max(8, pointer_y - self._tooltip.winfo_reqheight() - 10)
        self._tooltip.geometry(f"+{int(x)}+{int(y)}")
        self._tooltip.deiconify()

    def hide_tooltip(self) -> None:
        if self._tooltip is not None:
            self._tooltip.destroy()
            self._tooltip = None
            self._tooltip_holder = None
            self._tooltip_labels = []
        if self.geometry is not None:
            self.itemconfigure("crosshair", state="hidden")


class HistoryWindow:
    """Frameless ``Toplevel`` carrying the charts and disclosure tables."""

    def __init__(self, app):
        self.app = app
        self.window: tk.Toplevel | None = None
        self.body: ScrollableBody | None = None
        self.doc: ttk.Frame | None = None
        self.header: ttk.Frame | None = None
        self.footer: ttk.Frame | None = None
        self.context_label: ttk.Label | None = None
        self.refresh_button: ttk.Button | None = None
        self.range_var: tk.StringVar | None = None
        self.range_buttons: dict[str, ttk.Radiobutton] = {}
        self.state: MonitorViewState = MonitorViewState()
        self.range_key = RANGE_KEYS[0]
        self.table_expanded: set[str] = set()
        self.charts: dict[str, HistoryChart] = {}
        self.geometries: dict[str, ChartGeometry] = {}
        self._laid_out = False
        self._last_plot_width = 0.0
        self._resize_after: str | None = None
        self._scroll_after: str | None = None
        self._scroll_fraction = 0.0

    # ------------------------------------------------------------ lifecycle
    def open(self, state: MonitorViewState) -> tk.Toplevel:
        app = self.app
        self.state = state
        window = tk.Toplevel(app.root)
        window.configure(background=app.colors["surface"])
        window.resizable(True, True)
        window.title(HISTORY_WINDOW_TITLE)
        app._apply_icon(window)
        install_window_effects(window, app.colors["surface"], material=app.material)
        self.window = window
        window._on_dpi_changed = app._on_dpi_changed
        window._geometry = WindowGeometry(window, app.storage, "history", (480, 420))
        window._chrome = WindowChrome(
            window, HISTORY_WINDOW_TITLE, app.colors, app.font_family, self.close
        )
        window.protocol("WM_DELETE_WINDOW", self.close)
        window.bind("<Escape>", lambda event: self.close())
        window.bind("<Return>", lambda event: app._request_refresh())

        self.header = ttk.Frame(window, style="Card.TFrame")
        self.header.pack(
            fill="x", padx=app.px(OUTER_PADDING), pady=(app.px(12), 0),
        )
        ttk.Label(
            self.header, text=HISTORY_WINDOW_TITLE, style="Title.TLabel",
        ).pack(anchor="w")
        self.context_label = ttk.Label(self.header, style="Muted.TLabel")
        self.context_label.pack(anchor="w", pady=(4, 8))
        range_row = ttk.Frame(self.header, style="Card.TFrame")
        range_row.pack(anchor="w")
        self.range_var = tk.StringVar(window, self.range_key)
        for key in RANGE_KEYS:
            button = ttk.Radiobutton(
                range_row, text=HISTORY_RANGE_LABELS[key], value=key,
                variable=self.range_var, style="Theme.TRadiobutton",
                command=self._on_range_change,
            )
            button.pack(side="left", padx=2)
            self.range_buttons[key] = button

        self.body = ScrollableBody(window, app.colors)
        self.body.pack(fill="both", expand=True)
        self.doc = ttk.Frame(self.body.content, style="Card.TFrame")
        self.doc.pack(fill="x")
        self.body.canvas.bind("<Configure>", self._on_canvas_resize, add=True)

        self.footer = ttk.Frame(
            window,
            padding=(app.px(OUTER_PADDING), 0, app.px(OUTER_PADDING), app.px(12)),
        )
        self.footer.pack(fill="x")
        self.refresh_button = ttk.Button(
            self.footer, text="立即刷新", style="Primary.TButton",
            command=app._request_refresh,
        )
        self.refresh_button.pack(side="right")
        ttk.Button(
            self.footer, text="关闭", style="Secondary.TButton",
            command=self.close,
        ).pack(side="right", padx=(0, 8))
        window.after_idle(self._first_layout)
        return window

    def _first_layout(self) -> None:
        if self._laid_out or self.window is None:
            return
        self._laid_out = True
        self._fit()
        self.render(self.state)
        # A tray dispatch opens from behind other windows; raise explicitly
        # like the popup does instead of relying on implicit Toplevel focus.
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()

    def focus(self) -> None:
        if self.window is not None and self.window.winfo_exists():
            self.window.deiconify()
            self.window.lift()
            self.window.focus_force()

    def close(self) -> None:
        app = self.app
        window = self.window
        if window is not None and window.winfo_exists():
            if self._resize_after is not None:
                window.after_cancel(self._resize_after)
            if self._scroll_after is not None:
                window.after_cancel(self._scroll_after)
            window._geometry.flush()
            window.destroy()
        self.window = None
        self.body = None
        self.doc = None
        self.charts = {}
        self.geometries = {}
        app.history = None

    # ---------------------------------------------------------------- layout
    def _fit(self) -> None:
        window = self.window
        if window is None or window._geometry.initialized:
            return
        window.update_idletasks()
        scale = dpi_scale(window)
        width = max(
            round(480 * scale),
            self.header.winfo_reqwidth(),
            self.doc.winfo_reqwidth(),
        )
        left, top, right, bottom = window_work_area(window)
        width = min(width, right - left - 40)
        fixed = (
            self.footer.winfo_reqheight()
            + self.header.winfo_reqheight()
            + window._chrome.header.winfo_reqheight()
            + self.app.px(8)
        )
        work_area_limit = max(_MIN_BODY_HEIGHT, bottom - top - 70 - fixed)
        default_limit = max(_MIN_BODY_HEIGHT, round(_DEFAULT_HEIGHT * scale) - fixed)
        height = self.body.fit(min(work_area_limit, default_limit))
        window._geometry.fit((width, height + fixed))

    def _on_canvas_resize(self, event: tk.Event) -> None:
        if not self._laid_out or self.window is None:
            return
        plot_width = self._plot_width()
        if plot_width != self._last_plot_width:
            if self._resize_after is not None:
                self.window.after_cancel(self._resize_after)
            self._resize_after = self.window.after_idle(self._rebuild_document)

    def _on_range_change(self) -> None:
        self.range_key = self.range_var.get()
        self._rebuild_document()

    def _plot_width(self) -> float:
        if self.window is None or self.doc is None:
            return float(_FALLBACK_PLOT_WIDTH)
        s = dpi_scale(self.window)
        available = self.doc.winfo_width()
        if available <= 1:
            available = round((_FALLBACK_PLOT_WIDTH + _PANEL_CHROME_X) * s)
        gutter = round(Y_AXIS_WIDTH * s)
        return max(1.0, available - _PANEL_CHROME_X * s - gutter)

    # ---------------------------------------------------------------- render
    def render(self, state: MonitorViewState) -> None:
        self.state = state
        if not self._laid_out or self.window is None:
            return
        self.window.title(f"{state.provider_label} · {HISTORY_WINDOW_TITLE}")
        if self.context_label is not None:
            self.context_label.configure(text=history_context_line(state))
        if self.refresh_button is not None:
            self.refresh_button.configure(
                text="正在刷新…" if state.is_refreshing else "立即刷新",
                state="disabled" if state.is_refreshing else "normal",
            )
        self._rebuild_document()

    def _rebuild_document(self) -> None:
        if self.doc is None or self.window is None or not self._laid_out:
            return
        self._resize_after = None
        self._scroll_fraction = self.body.canvas.yview()[0]
        for child in self.doc.winfo_children():
            child.destroy()
        self.charts = {}
        self.geometries = {}
        self._configure_treeview_style()
        state = self.state
        snapshot = state.snapshot
        if snapshot is None:
            self._empty_panel(history_empty_text(state, "no_history"))
            self._restore_scroll()
            return
        now = (
            state.last_attempt_at
            or snapshot.queried_at
            or datetime.now(UTC)
        )
        unit_label = get_provider(state.provider).unit_label
        present = {window.kind for window in snapshot.windows}
        plot_width = self._plot_width()
        self._last_plot_width = plot_width
        plot_height = round(PLOT_HEIGHT * dpi_scale(self.window))
        for kind in WINDOW_ORDER:
            if kind not in present:
                continue
            geometry = build_chart(
                state.history, kind, now=now, range_key=self.range_key,
                plot_width=plot_width, plot_height=plot_height,
                alert_threshold_percent=state.alert_threshold_percent,
            )
            self.geometries[kind] = geometry
            self._kind_panel(kind, geometry, unit_label)
        self._restore_scroll()

    def _empty_panel(self, text: str) -> None:
        app = self.app
        panel = RoundedPanel(
            self.doc, radius=RADIUS["panel"], fill=app.colors["card"],
            outline=app.colors["border"], background=app.colors["surface"],
            padding=(12, 10),
        )
        panel.pack(fill="x", pady=(0, SECTION_GAP))
        ttk.Label(
            panel.content, text=text, style="CardMuted.TLabel",
            wraplength=round(380 * dpi_scale(self.window)), justify="left",
        ).pack(anchor="w")

    def _kind_panel(
        self, kind: str, geometry: ChartGeometry, unit_label: str
    ) -> None:
        app = self.app
        panel = RoundedPanel(
            self.doc, radius=RADIUS["panel"], fill=app.colors["card"],
            outline=app.colors["border"], background=app.colors["surface"],
            padding=(12, 10),
        )
        panel.pack(fill="x", pady=(0, SECTION_GAP))
        content = panel.content
        ttk.Label(
            content, text=history_chart_title(kind), style="Section.TLabel",
        ).pack(anchor="w", pady=(0, 8))
        if geometry.empty_reason == "no_history":
            ttk.Label(
                content,
                text=history_empty_text(self.state, "no_history"),
                style="CardMuted.TLabel",
            ).pack(anchor="w")
            return
        chart = HistoryChart(content, app.colors, app.font_family)
        chart.pack(anchor="w", fill="x")
        chart.set_chart(geometry, unit_label)
        self.charts[kind] = chart
        if geometry.empty_reason == "thin":
            ttk.Label(
                content, text=history_empty_text(self.state, "thin"),
                style="CardMuted.TLabel",
            ).pack(anchor="w", pady=(6, 0))
        expanded = kind in self.table_expanded
        ttk.Button(
            content,
            text=HISTORY_TABLE_COLLAPSE if expanded else HISTORY_TABLE_TITLE,
            style="Secondary.TButton",
            command=lambda chosen=kind: self._toggle_table(chosen),
        ).pack(anchor="w", pady=(8, 0))
        if expanded:
            self._build_table(content, geometry, unit_label)

    def _toggle_table(self, kind: str) -> None:
        if kind in self.table_expanded:
            self.table_expanded.discard(kind)
        else:
            self.table_expanded.add(kind)
        self._rebuild_document()

    def _build_table(
        self, parent: tk.Misc, geometry: ChartGeometry, unit_label: str
    ) -> None:
        columns = ["time", "percent"]
        titles = {
            "time": HISTORY_TABLE_HEADERS["time"],
            "percent": HISTORY_TABLE_HEADERS["percent"],
        }
        if geometry.has_absolute:
            columns.append("absolute")
            titles["absolute"] = history_absolute_header(unit_label)
        holder = ttk.Frame(parent, style="Card.TFrame")
        holder.pack(anchor="w", fill="x", pady=(8, 0))
        # The viewport caps at twelve rows but the Treeview keeps every row
        # reachable by scrolling -- the table exists so no value is gated.
        table = ttk.Treeview(
            holder, columns=columns, show="headings",
            height=min(12, max(1, len(geometry.rows))),
            style="History.Treeview",
        )
        for key in columns:
            table.heading(key, text=titles[key])
            table.column(key, width=140 if key == "time" else 100, anchor="w")
        for row in geometry.rows:
            values = [
                history_table_time(row.queried_at),
                f"{row.used_percent:.1f}%",
            ]
            if "absolute" in columns:
                if row.quota is None or row.used is None:
                    values.append("—")
                else:
                    values.append(
                        f"{format_number(row.used)} / {format_number(row.quota)}"
                    )
            table.insert("", "end", values=values)
        scrollbar = ttk.Scrollbar(holder, orient="vertical", command=table.yview,
                                  style="Vertical.TScrollbar")
        table.configure(yscrollcommand=scrollbar.set)
        table.pack(side="left", fill="x", expand=True)
        if len(geometry.rows) > 12:
            scrollbar.pack(side="right", fill="y")

    def _configure_treeview_style(self) -> None:
        colors = self.app.colors
        style = ttk.Style(self.window)
        style.configure(
            "History.Treeview",
            background=colors["card"], fieldbackground=colors["card"],
            foreground=colors["text"], bordercolor=colors["border"],
            rowheight=max(20, round(20 * dpi_scale(self.window))),
        )
        style.configure(
            "History.Treeview.Heading",
            background=colors["hover"], foreground=colors["text"],
            fieldbackground=colors["hover"],
        )
        style.map(
            "History.Treeview",
            background=[("selected", colors["info_surface"])],
            foreground=[("selected", colors["text"])],
        )

    def _restore_scroll(self) -> None:
        if self.window is None:
            return
        fraction = self._scroll_fraction

        def apply() -> None:
            self._scroll_after = None
            if self.body is not None and fraction:
                self.body.canvas.yview_moveto(fraction)

        self._scroll_after = self.window.after_idle(apply)
