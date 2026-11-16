from __future__ import annotations

from datetime import datetime

from .alerts import AlertEvent
from .history_chart import ChartSample
from .models import MonitorViewState, QuotaWindow, WindowTrend, WINDOW_ORDER
from .trends import MIN_RATE_PERCENT_PER_HOUR

WINDOW_LABELS = {"five_hour": "5 小时", "weekly": "每周", "monthly": "每月"}
STATUS_LABELS = {
    "empty": "暂无数据",
    "unconfigured": "待配置",
    "ok": "数据正常",
    "stale": "数据过期",
    "error": "查询失败",
}
PLAN_LABELS = {
    "agent_plan": "Agent Plan",
    "coding_plan": "Coding Plan",
    "token_plan": "Token Plan",
}


def status_text(state: MonitorViewState) -> str:
    if state.is_refreshing:
        return "正在刷新…"
    return STATUS_LABELS.get(state.status, state.status)


def state_tone(state: MonitorViewState) -> str:
    if state.status == "unconfigured":
        return "unconfigured"
    if state.status == "error":
        return "error"
    if state.status == "stale":
        return "warning"
    if state.snapshot and any(
        window.remaining_percent < state.alert_threshold_percent
        for window in state.snapshot.windows
    ):
        return "warning"
    return "ok"


def _windows(state: MonitorViewState) -> dict[str, QuotaWindow]:
    if state.snapshot is None:
        return {}
    return {window.kind: window for window in state.snapshot.windows}


def compact_remaining(state: MonitorViewState, kind: str) -> str:
    window = _windows(state).get(kind)
    return f"{window.remaining_percent:.1f}%" if window is not None else "—"


def hover_summary(state: MonitorViewState) -> str:
    heading = f"{state.provider_label} · {status_text(state)}"
    if state.snapshot is not None:
        five_hour = compact_remaining(state, "five_hour")
        weekly = compact_remaining(state, "weekly")
        return f"{heading}\n5小时 剩余 {five_hour} · 每周 剩余 {weekly}"
    if state.status == "unconfigured":
        detail = "打开设置以查询容量"
    elif state.is_refreshing:
        detail = "正在获取最新容量"
    elif state.status == "error":
        detail = "可右键重试或打开详情"
    else:
        detail = "尚未获取容量数据"
    return f"{heading}\n{detail}"


def truncate_utf16(text: str, max_units: int = 127) -> str:
    encoded = text.encode("utf-16-le")
    if len(encoded) <= max_units * 2:
        return text
    return encoded[: max_units * 2].decode("utf-16-le", errors="ignore")


def tray_tooltip(state: MonitorViewState) -> str:
    return truncate_utf16(hover_summary(state))


def menu_context_line(state: MonitorViewState) -> str:
    if state.snapshot is None:
        return f"状态：{status_text(state)}"
    updated = format_datetime(state.snapshot.queried_at)
    return f"{status_text(state)} · 更新于 {updated}"


def menu_window_line(state: MonitorViewState, kind: str) -> str:
    label = WINDOW_LABELS[kind]
    window = _windows(state).get(kind)
    if window is None:
        return f"{label}：暂无数据"
    return (
        f"{label}：剩余 {window.remaining_percent:.1f}% · "
        f"已用 {window.used_percent:.1f}%"
    )


def menu_lines(state: MonitorViewState) -> tuple[str, ...]:
    if state.snapshot is None:
        return (menu_context_line(state),)
    return (menu_context_line(state),) + tuple(
        menu_window_line(state, kind) for kind in WINDOW_ORDER
    )


def format_datetime(value: datetime) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def format_number(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _short_time(value: datetime) -> str:
    local = value.astimezone()
    if local.date() != datetime.now().astimezone().date():
        return local.strftime("%m-%d %H:%M")
    return local.strftime("%H:%M")


def alert_notification(event: AlertEvent, provider_name: str) -> tuple[str, str]:
    """Title and body for a system notification; the single copy source for
    every platform's notifier."""
    label = WINDOW_LABELS.get(event.kind, event.kind)
    if event.category == "reset":
        title = f"{provider_name} · {label}窗口已重置"
        body = f"剩余回到 {event.remaining_percent:.1f}%，新周期开始。"
    else:
        title = f"{provider_name} · {label}额度偏低"
        body = f"剩余 {event.remaining_percent:.1f}%，低于阈值 {event.threshold_percent}%。"
    if event.exhausted_at is not None:
        body += f" 预计 {_short_time(event.exhausted_at)} 耗尽"
        if event.resets_before_exhaustion is False:
            body += "（早于重置）"
        body += "。"
    return title, body


def trend_line(trend: WindowTrend | None, window: QuotaWindow, unit_label: str) -> str:
    """One honest caption line per window. Empty string hides the line (no
    history at all); thin evidence and idle consumption say so in words, and
    every prediction is marked 预计/约 -- never a fabricated absolute."""
    if trend is None:
        return ""
    if trend.percent_per_hour is None:
        return "记录不足，持续使用后可预测"
    if trend.percent_per_hour <= MIN_RATE_PERCENT_PER_HOUR:
        return "近期无消耗"
    rate_text = f"消耗约 {trend.percent_per_hour:.1f}%/小时"
    if trend.absolute_per_hour is not None:
        rate_text += f"（约 {format_number(trend.absolute_per_hour)} {unit_label}/小时）"
    if trend.exhausted_at is None:
        return rate_text
    if trend.resets_before_exhaustion:
        return rate_text + " · 预计重置前不会耗尽"
    line = rate_text + f" · 预计 {_short_time(trend.exhausted_at)} 耗尽"
    if window.reset_at is not None:
        line += f"（早于 {_short_time(window.reset_at)} 重置）"
    return line


# --- History window ----------------------------------------------------------
# Single copy source for the 用量历史 window, same rules as trend_line: values
# lead, predictions stay marked, and percent-only plans never gain a
# fabricated absolute amount.

HISTORY_WINDOW_TITLE = "用量历史"
HISTORY_RANGE_LABELS = {"24h": "24小时", "7d": "7天", "30d": "30天", "90d": "90天"}
HISTORY_EMPTY_UNCONFIGURED = "请先连接账号，成功查询后此处展示用量曲线。"
HISTORY_EMPTY_NO_DATA = "尚无历史记录：成功查询一次后开始记录。"
HISTORY_THIN = "记录不足，持续使用后此处显示曲线。"
HISTORY_THRESHOLD_LABEL = "告警线"
HISTORY_RESET_LABEL = "重置"
HISTORY_TABLE_TITLE = "数据列表"
HISTORY_TABLE_COLLAPSE = "收起数据列表"
HISTORY_TABLE_HEADERS = {"time": "时间", "percent": "已用%", "absolute": "绝对值"}


def history_chart_title(kind: str) -> str:
    return f"{WINDOW_LABELS[kind]} · 已用%"


def history_context_line(state: MonitorViewState) -> str:
    parts = [state.provider_label]
    if state.snapshot is not None:
        parts.append(
            state.snapshot.plan_name
            or PLAN_LABELS.get(state.snapshot.plan_type, state.snapshot.plan_type)
        )
    parts.append("当前账号")
    updated = state.last_attempt_at or (
        state.snapshot.queried_at if state.snapshot is not None else None
    )
    parts.append(f"更新于 {format_datetime(updated)}" if updated else status_text(state))
    return " · ".join(parts)


def history_empty_text(state: MonitorViewState, empty_reason: str | None) -> str:
    if state.status == "unconfigured":
        return HISTORY_EMPTY_UNCONFIGURED
    if empty_reason == "thin":
        return HISTORY_THIN
    return HISTORY_EMPTY_NO_DATA


def history_axis_time(value: datetime, range_key: str) -> str:
    local = value.astimezone()
    return local.strftime("%H:%M" if range_key == "24h" else "%m-%d")


def history_table_time(value: datetime) -> str:
    return value.astimezone().strftime("%m-%d %H:%M")


def history_threshold_label(used_percent: float) -> str:
    return f"{HISTORY_THRESHOLD_LABEL} {used_percent:.0f}%"


def history_absolute_header(unit_label: str) -> str:
    return f"{HISTORY_TABLE_HEADERS['absolute']}（{unit_label}）"


def history_tooltip_lines(sample: ChartSample, unit_label: str) -> tuple[str, ...]:
    """Value leads, label follows; the absolute line appears ONLY when this
    sample reported both quota and used -- never a fabricated amount."""
    lines = [
        f"已用 {sample.used_percent:.1f}%",
        history_table_time(sample.queried_at),
    ]
    if sample.quota is not None and sample.used is not None:
        lines.append(
            f"{unit_label} {format_number(sample.used)} / {format_number(sample.quota)}"
        )
    return tuple(lines)


def history_chart_accessibility(
    kind: str, latest: ChartSample | None, range_key: str
) -> str:
    label = (
        f"{WINDOW_LABELS[kind]}已用百分比曲线，范围{HISTORY_RANGE_LABELS[range_key]}"
    )
    if latest is None:
        return label + "，暂无记录"
    return label + f"，最新已用 {latest.used_percent:.1f}%"
