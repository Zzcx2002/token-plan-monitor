from dataclasses import replace
from datetime import UTC, datetime

from token_plan_monitor.models import MonitorViewState, QuotaWindow, UsageSnapshot
from token_plan_monitor.presentation import (
    compact_remaining,
    hover_summary,
    menu_lines,
    state_tone,
    tray_tooltip,
    truncate_utf16,
)


def sample_state() -> MonitorViewState:
    return MonitorViewState(
        status="ok",
        snapshot=UsageSnapshot(
            plan_type="agent_plan",
            plan_name="Agent Plan Large",
            queried_at=datetime(2026, 11, 3, 9, 30, tzinfo=UTC),
            windows=(
                QuotaWindow(kind="five_hour", used_percent=24.6),
                QuotaWindow(kind="weekly", used_percent=58.2),
                QuotaWindow(kind="monthly", used_percent=91.4),
            ),
        ),
    )


def test_hover_is_concise_and_menu_has_all_three_windows():
    state = sample_state()
    assert hover_summary(state) == (
        "火山方舟 · 数据正常\n5小时 剩余 75.4% · 每周 剩余 41.8%"
    )
    lines = menu_lines(state)
    assert len(lines) == 4
    assert "5 小时：剩余 75.4% · 已用 24.6%" in lines
    assert "每周：剩余 41.8% · 已用 58.2%" in lines
    assert "每月：剩余 8.6% · 已用 91.4%" in lines
    assert "更新于" in lines[0]
    assert all("AFP" not in line for line in lines)


def test_missing_windows_and_empty_states_are_explicit():
    state = sample_state()
    state = replace(
        state,
        snapshot=replace(state.snapshot, windows=(state.snapshot.windows[0],)),
    )
    assert compact_remaining(state, "five_hour") == "75.4%"
    assert compact_remaining(state, "weekly") == "—"
    assert "每周：暂无数据" in menu_lines(state)

    unconfigured = MonitorViewState(status="unconfigured")
    assert hover_summary(unconfigured) == "火山方舟 · 待配置\n打开设置以查询容量"
    assert menu_lines(unconfigured) == ("状态：待配置",)


def test_refreshing_stale_and_error_states_remain_textual():
    state = sample_state()
    refreshing = replace(state, is_refreshing=True)
    assert "正在刷新" in hover_summary(refreshing)
    assert "75.4%" in hover_summary(refreshing)

    stale = replace(state, status="stale", error="网络超时")
    assert "数据过期" in hover_summary(stale)
    assert state_tone(stale) == "warning"

    error = MonitorViewState(status="error", error="网络超时")
    assert hover_summary(error) == "火山方舟 · 查询失败\n可右键重试或打开详情"
    assert state_tone(error) == "error"


def test_low_quota_and_unconfigured_tones():
    assert state_tone(sample_state()) == "warning"
    assert state_tone(MonitorViewState(status="unconfigured")) == "unconfigured"


def test_native_tooltip_is_bounded_without_splitting_surrogate_pairs():
    assert len(tray_tooltip(sample_state()).encode("utf-16-le")) // 2 <= 127
    assert truncate_utf16("A😀B", max_units=2) == "A"
    assert truncate_utf16("A😀B", max_units=3) == "A😀"
