"""Exercise the Windows usage-history window with fake data, no cloud or tray.

Covers the same document rules as the macOS history panel: one chart per
present quota window, cycles that never connect across a reset, range
switching, hover tooltips fed by the shared pure helpers, the 数据列表
disclosure table (absolute column only when absolutes exist), thin/empty
captions, theme redraw and window lifecycle.
"""
import tempfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import ttk

from gui_preview import ARTIFACTS, EmptyKeyring, FakeMonitor
from token_plan_monitor.history import HistoryEntry
from token_plan_monitor.models import (
    MonitorViewState,
    QuotaWindow,
    UsageSnapshot,
    WINDOW_ORDER,
)
from token_plan_monitor.presentation import (
    HISTORY_EMPTY_NO_DATA,
    HISTORY_EMPTY_UNCONFIGURED,
    HISTORY_TABLE_COLLAPSE,
    HISTORY_TABLE_TITLE,
    HISTORY_THIN,
    HISTORY_WINDOW_TITLE,
)
from token_plan_monitor.storage import Storage
from token_plan_monitor.ui import MonitorUI

BASE_TIME = datetime(2026, 11, 16, 8, tzinfo=UTC)
NOW = BASE_TIME + timedelta(hours=24)
ACCOUNT = "a1b2c3d4e5f6"


class Monitor(FakeMonitor):
    def __init__(self):
        super().__init__()
        self.refreshes = 0

    def request_refresh(self):
        self.refreshes += 1


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def pump(app, ms=80):
    app.root.after(ms, app.root.quit)
    app.root.mainloop()


def button(window, text):
    return next(w for w in descendants(window)
                if isinstance(w, ttk.Button) and w.cget("text") == text)


def treeviews(window):
    return [w for w in descendants(window)
            if isinstance(w, ttk.Treeview)]


def table_columns(table):
    # Tk builds differ: cget("columns") is a space string in some, a tuple in
    # this Python/Tk build.
    value = table.cget("columns")
    return tuple(value.split()) if isinstance(value, str) else tuple(value)


def label_texts(window):
    return [w.cget("text") for w in descendants(window)
            if isinstance(w, ttk.Label) and w.cget("text")]


def history_entry(minutes, percents, *, absolutes, five_hour_reset=None):
    windows = []
    for kind, percent in zip(WINDOW_ORDER, percents):
        reset = five_hour_reset if kind == "five_hour" else None
        quota = 1000.0 if absolutes else None
        used = round(percent * 10, 2) if absolutes else None
        windows.append(QuotaWindow(kind, percent, reset, quota, used))
    return HistoryEntry(
        queried_at=BASE_TIME + timedelta(minutes=minutes),
        account=ACCOUNT,
        plan_type="agent_plan" if absolutes else "coding_plan",
        windows=tuple(windows),
    )


def populated_entries(*, absolutes=True):
    """24 hourly samples; five_hour resets halfway, giving two chart cycles."""
    old_reset = BASE_TIME + timedelta(hours=12)
    new_reset = BASE_TIME + timedelta(hours=36)
    entries = []
    for index in range(24):
        minutes = index * 60
        if minutes <= 720:
            five_percent = 10 + index * 4
            reset = old_reset
        else:
            five_percent = 5 + (index - 13) * 2
            reset = new_reset
        weekly = 20 + index
        monthly = 30 + index * 0.4
        entries.append(
            history_entry(
                minutes, (five_percent, weekly, monthly),
                absolutes=absolutes,
                five_hour_reset=reset,
            )
        )
    return entries


def state_with(entries, *, status="ok", plan_type="agent_plan",
               absolutes=True, provider="volcengine"):
    last = entries[-1]
    windows = tuple(
        QuotaWindow(
            w.kind, w.used_percent, w.reset_at,
            w.quota if absolutes else None,
            w.used if absolutes else None,
        )
        for w in last.windows
    )
    snapshot = UsageSnapshot(
        plan_type=plan_type,
        plan_name="Agent Plan Large" if absolutes else "Coding Plan Pro",
        queried_at=NOW,
        windows=windows,
    )
    return MonitorViewState(
        status=status, snapshot=snapshot, history=tuple(entries),
        provider=provider,
    )


def main():
    with tempfile.TemporaryDirectory(dir=ARTIFACTS) as temporary:
        monitor = Monitor()
        app = MonitorUI(monitor, Storage(Path(temporary), EmptyKeyring()))
        callback_errors = []
        app.root.report_callback_exception = lambda *error: callback_errors.append(error)
        try:
            app._create_popup()
            state = state_with(populated_entries())
            app._state = monitor.state = state
            app.open_history()
            pump(app)
            history = app.history
            assert history is not None
            window = history.window
            assert window is not None and window.winfo_exists()
            assert HISTORY_WINDOW_TITLE in window.title()
            assert history.context_label.cget("text")

            # One chart per present window, in WINDOW_ORDER.
            assert tuple(history.charts) == WINDOW_ORDER
            assert tuple(history.geometries) == WINDOW_ORDER
            five = history.charts["five_hour"]
            weekly = history.charts["weekly"]
            five_geo = history.geometries["five_hour"]
            # The reset survived as a path break: two cycles, one hairline,
            # two drawn line/area pairs and no connection across the cut.
            assert len(five_geo.cycles) == 2
            assert len(five_geo.reset_marks) == 1
            assert len(five.find_withtag("series")) == 2
            assert len(five.find_withtag("area")) == 2
            assert len(five.find_withtag("threshold")) == 1
            weekly_geo = history.geometries["weekly"]
            assert len(weekly_geo.cycles) == 1
            assert len(weekly.find_withtag("series")) == 1
            assert weekly_geo.has_absolute is True

            # Range buttons rebuild every chart at the selected range.
            for key in ("7d", "30d", "90d", "24h"):
                history.range_buttons[key].invoke()
                pump(app)
                assert history.range_key == key
                assert tuple(history.charts) == WINDOW_ORDER
                assert all(g.range_key == key
                           for g in history.geometries.values())
            five = history.charts["five_hour"]
            five_geo = history.geometries["five_hour"]

            # Hover: crosshair appears, tooltip text comes from the shared
            # presentation helper and carries the absolute line for this plan.
            assert five.tooltip_lines_at(-1.0) is None
            lines = five.tooltip_lines_at(0.0)
            assert lines is not None and lines[0].startswith("已用 ")
            assert any("AFP" in line for line in lines)
            five.event_generate(
                "<Motion>", x=five._gutter + 10, y=20,
                rootx=five.winfo_rootx() + five._gutter + 10,
                rooty=five.winfo_rooty() + 20,
            )
            assert five._tooltip is not None and five._tooltip.winfo_exists()
            assert five.itemcget("crosshair", "state") == "normal"
            tooltip_text = "|".join(
                label.cget("text") for label in five._tooltip_labels
            )
            assert "已用 " in tooltip_text and "AFP" in tooltip_text
            five.event_generate("<Leave>")
            assert five._tooltip is None
            assert five.itemcget("crosshair", "state") == "hidden"

            # Disclosure table: every bucketed row, newest first, with the
            # absolute column because this plan reports quota and used.
            assert button(window, HISTORY_TABLE_TITLE) is not None
            button(window, HISTORY_TABLE_TITLE).invoke()
            pump(app)
            assert "five_hour" in history.table_expanded
            assert button(window, HISTORY_TABLE_COLLAPSE) is not None
            tables = treeviews(window)
            assert len(tables) == 1
            table = tables[0]
            columns = table_columns(table)
            assert columns == ("time", "percent", "absolute")
            headings = {key: table.heading(key)["text"] for key in columns}
            assert headings["absolute"] == "绝对值（AFP）"
            rows = table.get_children()
            assert len(rows) == len(five_geo.rows)
            first_values = table.item(rows[0])["values"]
            assert str(first_values[1]).endswith("%")
            assert "/" in str(first_values[2])
            # The table keeps the same newest-first order as the geometry.
            assert first_values[1] == f"{five_geo.rows[0].used_percent:.1f}%"
            button(window, HISTORY_TABLE_COLLAPSE).invoke()
            pump(app)
            assert treeviews(window) == []

            # Percent-only plan: no fabricated absolute column or tooltip line.
            percent_state = state_with(
                populated_entries(absolutes=False),
                plan_type="coding_plan", absolutes=False,
            )
            app._render_state(percent_state)
            pump(app)
            assert all(not g.has_absolute
                       for g in history.geometries.values())
            five = history.charts["five_hour"]
            lines = five.tooltip_lines_at(0.0)
            assert len(lines) == 2 and all("AFP" not in line for line in lines)
            button(window, HISTORY_TABLE_TITLE).invoke()
            pump(app)
            table = treeviews(window)[0]
            assert table_columns(table) == ("time", "percent")
            button(window, HISTORY_TABLE_COLLAPSE).invoke()
            pump(app)

            # Thin evidence: the chart stays (endpoint only, no line) and the
            # shared thin caption explains why there is no curve yet.
            thin_entries = (history_entry(
                24 * 60 - 10, (12.0, 21.0, 31.0), absolutes=True,
                five_hour_reset=NOW + timedelta(hours=2),
            ),)
            app._render_state(state_with(thin_entries))
            pump(app)
            assert tuple(history.charts) == WINDOW_ORDER
            thin_chart = history.charts["five_hour"]
            assert history.geometries["five_hour"].empty_reason == "thin"
            assert len(thin_chart.find_withtag("series")) == 0
            assert HISTORY_THIN in label_texts(window)
            # The thin panel still discloses its single table row.
            button(window, HISTORY_TABLE_TITLE).invoke()
            pump(app)
            assert len(treeviews(window)[0].get_children()) == 1
            button(window, HISTORY_TABLE_COLLAPSE).invoke()
            pump(app)

            # A window kind present in the snapshot but without any history
            # renders the no-data caption instead of a fake chart.
            partial_entry = HistoryEntry(
                queried_at=NOW - timedelta(minutes=10),
                account=ACCOUNT, plan_type="agent_plan",
                windows=(
                    QuotaWindow("five_hour", 12.0, NOW + timedelta(hours=2),
                                1000.0, 120.0),
                    QuotaWindow("weekly", 21.0, None, 1000.0, 210.0),
                ),
            )
            no_data_state = replace(
                state_with((partial_entry,)),
                # Monthly stays in the snapshot, so its panel must still appear.
                snapshot=state.snapshot,
            )
            app._render_state(no_data_state)
            pump(app)
            assert "monthly" not in history.charts
            assert "five_hour" in history.charts
            assert HISTORY_EMPTY_NO_DATA in label_texts(window)

            # No snapshot at all: one empty card, zero charts, shared copy.
            app._render_state(MonitorViewState(status="unconfigured"))
            pump(app)
            assert history.charts == {} and history.geometries == {}
            assert HISTORY_EMPTY_UNCONFIGURED in label_texts(window)

            # Back to data, then a theme swap rebuilds the charts in the new
            # palette and keeps the same window (no geometry loss).
            app._render_state(state)
            pump(app)
            geometry_before = history.window._geometry
            app._set_theme("dark", persist=True)
            pump(app)
            assert history.window is window
            assert history.window._geometry is geometry_before
            dark_card = app.colors["card"]
            assert all(chart.cget("bg") == dark_card
                       for chart in history.charts.values())
            app._set_theme("light", persist=True)
            pump(app)
            assert all(chart.cget("bg") == app.colors["card"]
                       for chart in history.charts.values())

            # Opening again while the window exists focuses the same one.
            app.open_history()
            assert app.history is history and history.window is window

            # Footer refresh and the Return shortcut both reach the monitor.
            history.refresh_button.invoke()
            window.event_generate("<Return>")
            assert monitor.refreshes == 2

            # Closing flushes the persisted geometry and drops the controller;
            # shutdown with an open window also destroys it cleanly.
            history.close()
            assert app.history is None
            assert app.storage.load_window_size("history") is not None
            app.open_history()
            pump(app)
            reopened = app.history.window
            assert reopened.winfo_exists()
            app.shutdown()
            try:
                assert not reopened.winfo_exists()
            except tk.TclError:
                # Root destruction tears the Toplevel down with it.
                pass
            assert not callback_errors, callback_errors
            print("PASS: history charts, cycle breaks, ranges, tooltip, "
                  "disclosure table, absolutes gating, thin/empty, theme, "
                  "reuse, refresh, geometry persistence, shutdown")
        finally:
            app.shutdown()


if __name__ == "__main__":
    main()
