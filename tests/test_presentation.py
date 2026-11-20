from dataclasses import replace
from datetime import UTC, datetime, timedelta

from token_plan_monitor.alerts import AlertEvent
from token_plan_monitor.models import (
    MonitorViewState,
    QuotaWindow,
    UsageSnapshot,
    WindowTrend,
)
from token_plan_monitor.presentation import (
    PLAN_LABELS,
    card_title,
    compact_remaining,
    format_datetime,
    history_context_line,
    hover_summary,
    menu_lines,
    state_tone,
    tray_tooltip,
    alert_notification,
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


def test_display_clamps_out_of_range_remaining_but_keeps_reported_used():
    # Overage (used > quota) makes remaining negative; the UI must show 0.0%,
    # while the model keeps the reported value for charts/alerts/trends.
    state = sample_state()
    over = replace(state.snapshot.windows[0], used_percent=103.2)
    assert over.remaining_percent < 0
    state = replace(state, snapshot=replace(state.snapshot, windows=(over,)))
    assert compact_remaining(state, "five_hour") == "0.0%"
    line = menu_lines(state)[1]
    assert "剩余 0.0%" in line
    assert "已用 103.2%" in line
    negative = replace(state.snapshot.windows[0], used_percent=-5.0)
    state = replace(state, snapshot=replace(state.snapshot, windows=(negative,)))
    assert compact_remaining(state, "five_hour") == "100.0%"


def test_history_context_line_labels_data_age_not_failed_attempt():
    # A stale state keeps the previous snapshot: "更新于" must name the data's
    # query time (like the tray menu), not the later failed attempt.
    state = replace(
        sample_state(),
        status="stale",
        last_attempt_at=datetime(2026, 11, 3, 10, 0, tzinfo=UTC),
    )
    line = history_context_line(state)
    assert f"更新于 {format_datetime(state.snapshot.queried_at)}" in line
    # Without a snapshot there is no data age: the attempt time is all there is.
    bare = replace(state, snapshot=None)
    assert f"更新于 {format_datetime(state.last_attempt_at)}" in history_context_line(bare)


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


def test_alert_notification_copy_names_provider_window_and_threshold():
    event = AlertEvent("low_quota", "five_hour", "volcengine", 8.0, 10, "Agent Plan")
    title, body = alert_notification(event, "火山方舟")
    assert title == "火山方舟 · 5 小时额度偏低"
    assert "剩余 8.0%" in body and "阈值 10%" in body
    reset = AlertEvent("reset", "weekly", "bailian", 100.0, 10)
    title, body = alert_notification(reset, "阿里云百炼")
    assert title == "阿里云百炼 · 每周窗口已重置"
    assert "100.0%" in body
    burning = replace(
        event,
        exhausted_at=datetime.now(UTC) + timedelta(hours=1),
        resets_before_exhaustion=False,
    )
    _, body = alert_notification(burning, "火山方舟")
    assert "预计" in body and "耗尽" in body and "早于重置" in body


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


# --- History window copy -----------------------------------------------------

from token_plan_monitor.history_chart import ChartSample
from token_plan_monitor.presentation import (
    HISTORY_EMPTY_NO_DATA,
    HISTORY_EMPTY_UNCONFIGURED,
    HISTORY_RANGE_LABELS,
    HISTORY_THIN,
    history_absolute_header,
    history_axis_time,
    history_chart_accessibility,
    history_chart_title,
    history_context_line,
    history_empty_text,
    history_table_time,
    history_threshold_label,
    history_tooltip_lines,
)

HISTORY_MOMENT = datetime(2026, 11, 16, 8, 30, tzinfo=UTC)


def test_history_tooltip_leads_with_value_and_never_invents_absolutes():
    reported = ChartSample(HISTORY_MOMENT, 42.3, 5000.0, 2115.0, None)
    lines = history_tooltip_lines(reported, "AFP")
    assert lines[0] == "已用 42.3%"
    assert lines[1] == history_table_time(HISTORY_MOMENT)
    assert lines[2] == "AFP 2115 / 5000"
    percent_only = ChartSample(HISTORY_MOMENT, 42.3, None, None, None)
    lines = history_tooltip_lines(percent_only, "AFP")
    assert len(lines) == 2
    assert all("AFP" not in line for line in lines)
    # A half-reported sample is still percent-only: no fabricated amount.
    half = ChartSample(HISTORY_MOMENT, 42.3, 5000.0, None, None)
    assert len(history_tooltip_lines(half, "额度")) == 2


def test_history_axis_and_table_time_formats_follow_the_range():
    assert HISTORY_RANGE_LABELS == {"24h": "24小时", "7d": "7天", "30d": "30天", "90d": "90天"}
    day_axis = history_axis_time(HISTORY_MOMENT, "24h")
    assert ":" in day_axis and "-" not in day_axis
    for key in ("7d", "30d", "90d"):
        label = history_axis_time(HISTORY_MOMENT, key)
        assert "-" in label and ":" not in label
    table = history_table_time(HISTORY_MOMENT)
    assert "-" in table and ":" in table


def test_history_titles_threshold_and_accessibility_copy():
    assert history_chart_title("five_hour") == "5 小时 · 已用%"
    assert history_chart_title("weekly") == "每周 · 已用%"
    assert history_threshold_label(90.0) == "告警线 90%"
    assert history_absolute_header("AFP") == "绝对值（AFP）"
    latest = ChartSample(HISTORY_MOMENT, 28.0, None, None, None)
    label = history_chart_accessibility("five_hour", latest, "24h")
    assert "5 小时" in label and "24小时" in label and "最新已用 28.0%" in label
    assert history_chart_accessibility("weekly", None, "7d").endswith("暂无记录")


def test_history_empty_and_thin_copy_stay_honest():
    ok = sample_state()
    assert history_empty_text(ok, "no_history") == HISTORY_EMPTY_NO_DATA
    assert history_empty_text(ok, "thin") == HISTORY_THIN
    unconfigured = MonitorViewState(status="unconfigured")
    assert history_empty_text(unconfigured, "no_history") == HISTORY_EMPTY_UNCONFIGURED
    assert history_empty_text(unconfigured, None) == HISTORY_EMPTY_UNCONFIGURED


def test_history_context_line_names_provider_plan_account_and_update():
    line = history_context_line(sample_state())
    assert line.startswith("火山方舟 · Agent Plan Large · 当前账号 · 更新于 ")
    bare = MonitorViewState(status="empty", provider="bailian")
    assert history_context_line(bare) == "阿里云百炼 · 当前账号 · 暂无数据"


def test_card_title_names_provider_and_plan_with_fallbacks():
    assert card_title(sample_state()) == "火山方舟 · Agent Plan Large"
    # Legacy cached snapshots may lack plan_name: fall back to PLAN_LABELS.
    unnamed = replace(sample_state(), snapshot=replace(sample_state().snapshot, plan_name=None))
    assert card_title(unnamed) == "火山方舟 · Agent Plan"
    # No snapshot yet (unconfigured/empty/error): provider alone.
    bare = MonitorViewState(status="unconfigured", provider="bailian")
    assert card_title(bare) == "阿里云百炼"
