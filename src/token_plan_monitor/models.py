from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

SCHEMA_VERSION = 1
WINDOW_ORDER = ("five_hour", "weekly", "monthly")


class QueryError(Exception):
    """An expected failure with a message safe to show in the UI."""

    category = "query"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ConfigurationError(QueryError):
    category = "configuration"


class AuthenticationError(QueryError):
    category = "authentication"


class NetworkError(QueryError):
    category = "network"


class TransientProviderError(QueryError):
    category = "transient_provider"


class ProviderError(QueryError):
    category = "provider"


class ParseError(QueryError):
    category = "parse"


class NoActivePlanError(QueryError):
    category = "no_active_plan"


class CacheError(QueryError):
    category = "cache"


@dataclass(frozen=True)
class Settings:
    region: str = "cn-beijing"
    refresh_minutes: int = 15

    def __post_init__(self) -> None:
        if not isinstance(self.region, str):
            raise ConfigurationError("Region 必须是字符串")
        region = self.region.strip()
        if not re.fullmatch(r"[a-z]{2}-[a-z0-9-]+", region):
            raise ConfigurationError("Region 格式无效，例如 cn-beijing")
        if isinstance(self.refresh_minutes, bool) or not isinstance(
            self.refresh_minutes, int
        ):
            raise ConfigurationError("刷新间隔必须是整数分钟")
        if not 5 <= self.refresh_minutes <= 1440:
            raise ConfigurationError("刷新间隔必须在 5–1440 分钟之间")
        object.__setattr__(self, "region", region)

    def to_dict(self) -> dict[str, Any]:
        return {
            "region": self.region,
            "refresh_minutes": self.refresh_minutes,
        }

    @classmethod
    def from_dict(cls, value: Any) -> Settings:
        if not isinstance(value, dict):
            raise ConfigurationError("配置格式无效")
        return cls(
            region=value.get("region", "cn-beijing"),
            refresh_minutes=value.get("refresh_minutes", 15),
        )


@dataclass(frozen=True)
class WidgetPreferences:
    x: int | None = None
    y: int | None = None
    visible: bool = True
    topmost: bool = True

    def __post_init__(self) -> None:
        for coordinate in (self.x, self.y):
            if coordinate is not None and (
                isinstance(coordinate, bool)
                or not isinstance(coordinate, int)
                or abs(coordinate) > 1_000_000
            ):
                raise ValueError("Widget coordinates must be reasonable integers")
        if type(self.visible) is not bool or type(self.topmost) is not bool:
            raise ValueError("Widget visibility and topmost flags must be booleans")

    def to_dict(self) -> dict[str, Any]:
        return {
            "x": self.x,
            "y": self.y,
            "visible": self.visible,
            "topmost": self.topmost,
        }

    @classmethod
    def from_dict(cls, value: Any) -> WidgetPreferences:
        if not isinstance(value, dict):
            raise ValueError("Widget preferences must be an object")
        return cls(
            x=value.get("x"),
            y=value.get("y"),
            visible=value.get("visible", True),
            topmost=value.get("topmost", True),
        )


@dataclass(frozen=True)
class Credentials:
    access_key_id: str
    secret_access_key: str = field(repr=False)

    def __post_init__(self) -> None:
        ak = self.access_key_id.strip()
        sk = self.secret_access_key.strip()
        if not ak or not sk:
            raise ConfigurationError("AccessKey ID 和 Secret AccessKey 均不能为空")
        object.__setattr__(self, "access_key_id", ak)
        object.__setattr__(self, "secret_access_key", sk)


@dataclass(frozen=True)
class QuotaWindow:
    kind: str
    used_percent: float
    reset_at: datetime | None = None
    quota: float | None = None
    used: float | None = None

    def __post_init__(self) -> None:
        if self.kind not in WINDOW_ORDER:
            raise ValueError(f"Unknown quota window: {self.kind}")
        values = (self.used_percent, self.quota, self.used)
        if any(value is not None and not math.isfinite(value) for value in values):
            raise ValueError("Quota values must be finite")
        if self.reset_at is not None and self.reset_at.tzinfo is None:
            raise ValueError("Reset time must be timezone-aware")

    @property
    def remaining_percent(self) -> float:
        return 100.0 - self.used_percent

    @property
    def remaining_absolute(self) -> float | None:
        if self.quota is None or self.used is None:
            return None
        return self.quota - self.used

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "used_percent": self.used_percent,
            "reset_at": datetime_to_iso(self.reset_at),
            "quota": self.quota,
            "used": self.used,
        }

    @classmethod
    def from_dict(cls, value: Any) -> QuotaWindow:
        if not isinstance(value, dict):
            raise ValueError("Quota window must be an object")
        return cls(
            kind=value["kind"],
            used_percent=_cache_number(value["used_percent"]),
            reset_at=datetime_from_iso(value.get("reset_at")),
            quota=_optional_cache_number(value.get("quota")),
            used=_optional_cache_number(value.get("used")),
        )


@dataclass(frozen=True)
class UsageSnapshot:
    plan_type: str
    queried_at: datetime
    windows: tuple[QuotaWindow, ...]
    plan_name: str | None = None

    def __post_init__(self) -> None:
        if self.plan_type not in ("agent_plan", "coding_plan"):
            raise ValueError(f"Unknown plan type: {self.plan_type}")
        if not isinstance(self.queried_at, datetime) or self.queried_at.tzinfo is None:
            raise ValueError("Query time must be timezone-aware")
        if not self.windows:
            raise ValueError("A snapshot must contain at least one quota window")

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_type": self.plan_type,
            "plan_name": self.plan_name,
            "queried_at": datetime_to_iso(self.queried_at),
            "windows": [window.to_dict() for window in self.windows],
        }

    @classmethod
    def from_dict(cls, value: Any) -> UsageSnapshot:
        if not isinstance(value, dict):
            raise ValueError("Snapshot must be an object")
        windows = value.get("windows")
        if not isinstance(windows, list):
            raise ValueError("Snapshot windows must be a list")
        return cls(
            plan_type=value["plan_type"],
            plan_name=value.get("plan_name"),
            queried_at=datetime_from_iso(value["queried_at"]),
            windows=tuple(QuotaWindow.from_dict(item) for item in windows),
        )


@dataclass(frozen=True)
class MonitorViewState:
    status: str = "empty"
    snapshot: UsageSnapshot | None = None
    is_refreshing: bool = False
    error: str | None = None
    last_attempt_at: datetime | None = None
    revision: int = 0


def datetime_to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def datetime_from_iso(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Datetime must be a non-empty ISO string")
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Datetime must include a timezone")
    return parsed.astimezone(UTC)


def _cache_number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Cached quota value must be numeric")
    return float(value)


def _optional_cache_number(value: Any) -> float | None:
    if value is None:
        return None
    return _cache_number(value)
