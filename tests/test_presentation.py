from dataclasses import replace
from datetime import UTC, datetime, timedelta

from token_plan_monitor.models import (
    MonitorViewState,
    QuotaWindow,
    UsageSnapshot,
    WindowTrend,
)
from token_plan_monitor.presentation import (
    PLAN_LABELS,
    compact_remaining,
    hover_summary,
    menu_lines,
    state_tone,
    tray_tooltip,
    trend_line,
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


def test_low_quota_tone_follows_the_configured_threshold():
    # sample_state's monthly window has 8.6% remaining.
    state = sample_state()
    assert state_tone(replace(state, alert_threshold_percent=5)) == "ok"
    assert state_tone(replace(state, alert_threshold_percent=50)) == "warning"
    healthy = replace(
        state,
        snapshot=replace(
            state.snapshot, windows=(QuotaWindow(kind="five_hour", used_percent=10),)
        ),
    )
    assert state_tone(healthy) == "ok"
    assert state_tone(replace(healthy, alert_threshold_percent=95)) == "warning"


def test_trend_line_is_honest_about_evidence_and_predictions():
    now = datetime.now(UTC)
    window = QuotaWindow("five_hour", 70, now + timedelta(hours=4))
    assert trend_line(None, window, "AFP") == ""
    thin = WindowTrend(kind="five_hour", sample_count=1, span_seconds=0)
    assert trend_line(thin, window, "AFP") == "记录不足，持续使用后可预测"
    idle = WindowTrend(kind="five_hour", sample_count=3, span_seconds=1800,
                       percent_per_hour=0.0)
    assert trend_line(idle, window, "AFP") == "近期无消耗"
    burning = WindowTrend(
        kind="five_hour", sample_count=4, span_seconds=2700,
        percent_per_hour=20.0, exhausted_at=now + timedelta(hours=1.5),
        resets_before_exhaustion=False, absolute_per_hour=2.5,
    )
    line = trend_line(burning, window, "AFP")
    assert "消耗约 20.0%/小时" in line
    assert "约 2.5 AFP/小时" in line
    assert "预计" in line and "耗尽" in line and "早于" in line
    # Percent-only plans keep the prediction but never gain an absolute.
    percent_only = replace(burning, absolute_per_hour=None)
    line = trend_line(percent_only, window, "Credits")
    assert "%/小时" in line and "预计" in line
    assert "Credits" not in line and "AFP" not in line
    safe = replace(burning, resets_before_exhaustion=True)
    assert trend_line(safe, window, "AFP").endswith("预计重置前不会耗尽")
    no_reset = QuotaWindow("five_hour", 70)
    assert "早于" not in trend_line(replace(burning, resets_before_exhaustion=None),
                                    no_reset, "AFP")


def test_native_tooltip_is_bounded_without_splitting_surrogate_pairs():
    assert len(tray_tooltip(sample_state()).encode("utf-16-le")) // 2 <= 127
    assert truncate_utf16("A😀B", max_units=2) == "A"
    assert truncate_utf16("A😀B", max_units=3) == "A😀"


def test_heading_and_labels_follow_the_active_provider():
    assert PLAN_LABELS["token_plan"] == "Token Plan"
    assert MonitorViewState(provider="bailian").provider_label == "阿里云百炼"
    snapshot = replace(
        sample_state().snapshot, plan_type="token_plan", plan_name="Token Plan 个人版"
    )
    bailian = replace(sample_state(), provider="bailian", snapshot=snapshot)
    assert hover_summary(bailian).startswith("阿里云百炼 · 数据正常\n")
    # The default provider keeps the original branding.
    assert hover_summary(sample_state()).startswith("火山方舟 · 数据正常\n")
