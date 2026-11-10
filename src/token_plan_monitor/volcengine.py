from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from typing import Any, Callable

import requests

from .models import (
    AuthenticationError,
    Credentials,
    NetworkError,
    NoActivePlanError,
    ParseError,
    ProviderError,
    QueryError,
    QuotaWindow,
    Settings,
    TransientProviderError,
    UsageSnapshot,
    parse_reset_time,
)
from .volcengine_signer import OPENAPI_HOST, sign_request

AGENT_WINDOWS = (
    ("AFPFiveHour", "five_hour"),
    ("AFPWeekly", "weekly"),
    ("AFPMonthly", "monthly"),
)
WINDOW_ALIASES = {
    "session": "five_hour",
    "5h": "five_hour",
    "fivehour": "five_hour",
    "five_hour": "five_hour",
    "rolling_5h": "five_hour",
    "weekly": "weekly",
    "week": "weekly",
    "7d": "weekly",
    "monthly": "monthly",
    "month": "monthly",
}
AUTH_ERROR_PARTS = (
    "auth",
    "signature",
    "accessdenied",
    "denied",
    "unauthorized",
    "forbidden",
    "credential",
    "token",
)


class VolcengineClient:
    def __init__(
        self,
        settings: Settings,
        credentials: Credentials,
        session: Any = None,
        timeout: float = 15,
        clock: Callable[[], datetime] | None = None,
    ):
        self.settings = settings
        self.credentials = credentials
        self.session = session or requests.Session()
        self.timeout = timeout
        self.clock = clock or (lambda: datetime.now(UTC))

    def query(self) -> UsageSnapshot:
        agent_body = self._call("GetAFPUsage")
        agent_windows = parse_agent_windows(_result(agent_body))
        if agent_windows:
            result = _result(agent_body)
            plan_type = result.get("PlanType")
            suffix = plan_type.strip() if isinstance(plan_type, str) else ""
            plan_name = f"Agent Plan {suffix}".strip()
            return UsageSnapshot(
                plan_type="agent_plan",
                plan_name=plan_name,
                queried_at=self.clock(),
                windows=agent_windows,
            )

        coding_body = self._call("GetCodingPlanUsage")
        coding_windows = parse_coding_windows(_result(coding_body))
        if not coding_windows:
            raise NoActivePlanError("未找到有效的 Agent Plan 或 Coding Plan 套餐")
        return UsageSnapshot(
            plan_type="coding_plan",
            plan_name="Coding Plan",
            queried_at=self.clock(),
            windows=coding_windows,
        )

    def _call(self, action: str) -> dict[str, Any]:
        signed = sign_request(
            self.credentials.access_key_id,
            self.credentials.secret_access_key,
            action,
            self.settings.region,
        )
        if not signed.url.startswith(f"https://{OPENAPI_HOST}/"):
            raise ProviderError("拒绝访问非官方 OpenAPI 地址")
        try:
            response = self.session.request(
                method=signed.method,
                url=signed.url,
                headers=signed.headers,
                data=signed.body,
                timeout=self.timeout,
                verify=True,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise NetworkError("无法连接火山方舟 OpenAPI，请检查网络或代理") from exc

        status = response.status_code
        try:
            body = response.json()
        except (ValueError, requests.exceptions.JSONDecodeError) as exc:
            if status == 401 or status == 403:
                raise AuthenticationError("AK/SK 鉴权失败，请检查凭据和系统时间") from exc
            if status == 429 or status >= 500:
                raise TransientProviderError(f"火山方舟暂时不可用（HTTP {status}）") from exc
            if not 200 <= status < 300:
                raise ProviderError(f"OpenAPI 返回非 JSON 响应（HTTP {status}）") from exc
            raise ParseError("OpenAPI 成功响应不是有效 JSON") from exc

        if not isinstance(body, dict):
            raise ParseError("OpenAPI 响应顶层必须是 JSON 对象")
        error = _response_error(body)
        if error is not None:
            code, message = error
            detail = _safe_error(code, message)
            if status in (401, 403) or _is_auth_error(code):
                raise AuthenticationError(f"AK/SK 鉴权失败：{detail}；请检查凭据和系统时间")
            if status == 429 or status >= 500:
                raise TransientProviderError(f"火山方舟暂时不可用：{detail}")
            raise ProviderError(f"OpenAPI 错误：{detail}")

        if status in (401, 403):
            raise AuthenticationError("AK/SK 鉴权失败，请检查凭据和系统时间")
        if status == 429 or status >= 500:
            raise TransientProviderError(f"火山方舟暂时不可用（HTTP {status}）")
        if not 200 <= status < 300:
            raise ProviderError(f"OpenAPI 请求失败（HTTP {status}）")
        return body


def parse_agent_windows(result: Any) -> tuple[QuotaWindow, ...]:
    if not isinstance(result, dict):
        raise ParseError("GetAFPUsage.Result 格式无效")
    windows: list[QuotaWindow] = []
    for key, kind in AGENT_WINDOWS:
        if key not in result:
            continue
        value = result[key]
        if not isinstance(value, dict):
            raise ParseError(f"{key} 格式无效")
        quota = _number(value.get("Quota", 0), f"{key}.Quota")
        if quota <= 0:
            continue
        used = _number(value.get("Used", 0), f"{key}.Used")
        reset_at = parse_reset_time(value.get("ResetTime"))
        windows.append(
            QuotaWindow(
                kind=kind,
                used_percent=used / quota * 100.0,
                reset_at=reset_at,
                quota=quota,
                used=used,
            )
        )
    return tuple(windows)


def parse_coding_windows(result: Any) -> tuple[QuotaWindow, ...]:
    if not isinstance(result, dict):
        raise ParseError("GetCodingPlanUsage.Result 格式无效")
    items: list[Any] | None = None
    for key in ("QuotaUsage", "Usages", "Details"):
        if key not in result:
            continue
        candidate = result[key]
        if not isinstance(candidate, list):
            raise ParseError(f"{key} 必须是数组")
        items = candidate
        break
    if items is None:
        return ()

    windows: list[QuotaWindow] = []
    for item in items:
        if not isinstance(item, dict):
            raise ParseError("Coding Plan 用量项格式无效")
        label = _first(item, ("Level", "Type", "Period", "Label", "Window"))
        if not isinstance(label, str):
            continue
        kind = WINDOW_ALIASES.get(label.lower())
        if kind is None:
            continue
        raw_percent = _first(item, ("Percent", "UsedPercent", "UsagePercent"), 0)
        percent = _number(raw_percent, f"{kind}.Percent")
        reset_value = item.get("ResetTimestamp", item.get("ResetTime"))
        windows.append(
            QuotaWindow(
                kind=kind,
                used_percent=percent,
                reset_at=parse_reset_time(reset_value),
            )
        )
    return tuple(windows)


def _result(body: dict[str, Any]) -> Any:
    return body.get("Result", body)


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ParseError(f"{field} 必须是数字")
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError as exc:
            raise ParseError(f"{field} 必须是数字") from exc
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ParseError(f"{field} 必须是有限数字")
    return float(value)


def _first(value: dict[str, Any], keys: tuple[str, ...], default: Any = None) -> Any:
    for key in keys:
        if key in value and value[key] is not None:
            return value[key]
    return default


def _response_error(body: dict[str, Any]) -> tuple[str, str] | None:
    metadata = body.get("ResponseMetadata")
    error = metadata.get("Error") if isinstance(metadata, dict) else None
    if error is None:
        error = body.get("Error")
    if not isinstance(error, dict):
        return None
    code = error.get("Code") if isinstance(error.get("Code"), str) else ""
    message = error.get("Message") if isinstance(error.get("Message"), str) else ""
    if not code and not message:
        return None
    return code, message


def _is_auth_error(code: str) -> bool:
    lowered = code.lower()
    return any(part in lowered for part in AUTH_ERROR_PARTS)


def _safe_error(code: str, message: str) -> str:
    code = re.sub(r"[\x00-\x1f\x7f]", "", code)[:80]
    message = re.sub(r"[\x00-\x1f\x7f]", " ", message)[:240]
    return f"{code}: {message}" if code else message or "未知错误"
