"""Pure helpers shared by the native shell and offline tests."""
from __future__ import annotations

from ..models import ConfigurationError, Credentials, MonitorViewState, Settings


class FieldValidationError(ConfigurationError):
    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field


def primary_action(state: MonitorViewState) -> tuple[str, str]:
    if state.status == "unconfigured":
        return "连接账号", "check-credentials"
    if state.error_category in ("authentication", "configuration"):
        return "检查凭据", "check-credentials"
    if state.status in ("stale", "error"):
        return "重试", "refresh"
    return "立即刷新", "refresh"


def lowest_windows(state: MonitorViewState) -> frozenset[str]:
    windows = state.snapshot.windows if state.snapshot else ()
    if not windows:
        return frozenset()
    lowest = min(w.remaining_percent for w in windows)
    return frozenset(w.kind for w in windows if w.remaining_percent == lowest)


def validate_configuration(ak: str, sk: str, region: str, interval: str,
                           existing: Credentials | None) -> tuple[Settings, Credentials]:
    if not ak.strip():
        raise FieldValidationError("ak", "请填写 AccessKey ID")
    if not sk.strip() and existing is None:
        raise FieldValidationError("sk", "首次连接请填写 Secret AccessKey")
    try:
        minutes = int(interval.strip())
    except ValueError as exc:
        raise FieldValidationError("interval", "刷新间隔必须是整数分钟") from exc
    if not 5 <= minutes <= 1440:
        raise FieldValidationError("interval", "刷新间隔必须在 5–1440 分钟之间")
    try:
        settings = Settings(region=region, refresh_minutes=minutes)
    except ConfigurationError as exc:
        raise FieldValidationError("region", exc.message) from exc
    credentials = Credentials(ak, sk.strip() or (existing.secret_access_key if existing else ""))
    return settings, credentials


def clamp_card(x: float | None, y: float | None, width: float, height: float,
               screens: list[tuple[float, float, float, float]]) -> tuple[int, int]:
    """Cocoa point coordinates, including displays left of/below the main display."""
    if not screens:
        return int(x or 0), int(y or 0)
    if x is None or y is None:
        sx, sy, sw, sh = screens[0]
        x, y = sx + sw - width - 24, sy + sh - height - 24
    def distance(screen):
        sx, sy, sw, sh = screen
        cx, cy = x + width / 2, y + height / 2
        return max(sx - cx, 0, cx - sx - sw) ** 2 + max(sy - cy, 0, cy - sy - sh) ** 2
    sx, sy, sw, sh = min(screens, key=distance)
    return round(max(sx, min(x, sx + max(0, sw - width)))), round(max(sy, min(y, sy + max(0, sh - height))))
