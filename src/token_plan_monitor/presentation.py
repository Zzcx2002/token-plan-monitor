from __future__ import annotations

from datetime import datetime

from .models import MonitorViewState, QuotaWindow, WINDOW_ORDER

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
        window.remaining_percent < 10 for window in state.snapshot.windows
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
