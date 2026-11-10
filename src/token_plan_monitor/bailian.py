"""Alibaba Cloud Bailian (Model Studio) Token Plan / Coding Plan quota client.

Bailian exposes no public quota OpenAPI. Like the official Bailian CLI, this
mints a console access token from the account AK/SK and then calls the console
gateway. Protocol behavior is adapted from modelstudioai/cli (Apache-2.0).
"""

from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime
from typing import Any, Callable
from urllib.parse import quote, urlencode, urlsplit

import requests

from .aliyun_signer import (
    CONSOLE_GATEWAY_PRODUCT,
    OFFICIAL_HOSTS,
    TOKEN_ACTION,
    TOKEN_API_VERSION,
    TOKEN_PATH,
    console_gateway,
    model_studio_host,
    sign_request,
)
from .models import (
    AuthenticationError,
    Credentials,
    NetworkError,
    NoActivePlanError,
    ParseError,
    ProviderError,
    QuotaWindow,
    Settings,
    TransientProviderError,
    UsageSnapshot,
    parse_reset_time,
)

TOKEN_PLAN_USAGE_API = "zeldaHttp.apikeyMgr./tokenplan/personal/api/v2/usage"
CODING_PLAN_USAGE_API = "zeldaEasy.broadscope-bailian.codingPlan.queryCodingPlanInstanceInfoV2"
# Only the domestic (aliyun.com) console site is supported, so the commodity
# code is fixed; the international site would use sfm_codingplan_public_intl.
CODING_PLAN_COMMODITY_CODE = "sfm_codingplan_public_cn"

# Fixed gateway envelope parameters required by the console bridge.
CORNERSTONE_PARAM = {
    "protocol": "V2",
    "console": "ONE_CONSOLE",
    "productCode": "p_efm",
    "switchUserType": 3,
    "consoleSite": "BAILIAN_ALIYUN",
}

# Token Plan window fields look like per5HourPercentage / per1MonthResetTime.
WINDOW_FIELD_PATTERN = re.compile(r"^per(\d+)(Hour|Day|Week|Month)(Percentage|ResetTime)$")
UNIT_HOURS = {"Hour": 1, "Day": 24, "Week": 24 * 7, "Month": 24 * 30}
# Bailian windows map onto the same three buckets the rest of the app renders.
TOKEN_PLAN_WINDOW_KINDS = {"per5Hour": "five_hour", "per1Week": "weekly", "per1Month": "monthly"}
CODING_PLAN_WINDOWS = (
    ("per5Hour", "five_hour"),
    ("perWeek", "weekly"),
    ("perBillMonth", "monthly"),
)

AUTH_ERROR_PARTS = (
    "notlogined",
    "unauthorized",
    "notauthoris",
    "notauthoriz",
    "forbidden",
    "accessdenied",
    "denied",
    "invalidaccesskey",
    "signaturedoesnotmatch",
    "nopermission",
)

# Bailian resolves the caller into a console "team" (业务空间/组织) before
# routing any quota API, so this class of failure is fixed in the Aliyun
# console rather than in the app's settings dialog.
TEAM_NOT_AUTHORISED_HINT = (
    "阿里云百炼控制台未授权当前身份：请确认主账号已开通百炼（华北2·北京），"
    "并在百炼控制台「账号管理」中为该 RAM 用户分配管理员或订阅套餐权限"
)


class _TokenExpired(Exception):
    """Internal signal: the console token was rejected, so mint a new one."""


class BailianClient:
    """Mirrors VolcengineClient's shape so the monitor can treat them alike."""

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
        # Bearer tokens live in memory only: never cached to disk or logged.
        self._token: str | None = None

    def query(self) -> UsageSnapshot:
        token_plan = parse_token_plan_windows(
            self._console_call(TOKEN_PLAN_USAGE_API, {})
        )
        if token_plan:
            return UsageSnapshot(
                plan_type="token_plan",
                plan_name="Token Plan 个人版",
                queried_at=self.clock(),
                windows=token_plan,
            )

        instance = valid_coding_plan_instance(
            self._console_call(CODING_PLAN_USAGE_API, _coding_plan_request())
        )
        coding_windows = parse_coding_plan_windows(instance) if instance else ()
        if not coding_windows:
            raise NoActivePlanError("未找到有效的百炼 Token Plan 或 Coding Plan 订阅")
        instance_type = instance.get("instanceType") if instance else None
        suffix = instance_type.strip() if isinstance(instance_type, str) else ""
        return UsageSnapshot(
            plan_type="coding_plan",
            plan_name=f"Coding Plan {suffix}".strip(),
            queried_at=self.clock(),
            windows=coding_windows,
        )

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def _console_call(self, api: str, data: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._gateway_call(api, data, self._ensure_token())
        except _TokenExpired:
            pass
        # The cached token was rejected. Mint a fresh one and retry exactly once.
        self._token = None
        try:
            return self._gateway_call(api, data, self._ensure_token())
        except _TokenExpired as exc:
            raise AuthenticationError(
                "阿里云百炼控制台会话无效，请检查 AK/SK 及其权限"
            ) from exc

    def _ensure_token(self) -> str:
        if self._token is None:
            self._token = self._fetch_token()
        return self._token

    def _fetch_token(self) -> str:
        signed = sign_request(
            self.credentials.access_key_id,
            self.credentials.secret_access_key,
            host=model_studio_host(self.settings.region),
            path=TOKEN_PATH,
            action=TOKEN_ACTION,
            version=TOKEN_API_VERSION,
        )
        headers = {**signed.headers, "Accept": "application/json"}
        response = self._request(signed.method, signed.url, headers, signed.body)
        body = self._decode_json(response, "阿里云百炼令牌接口")
        if not isinstance(body, dict):
            raise ParseError("令牌接口响应顶层必须是 JSON 对象")
        _raise_for_pop_status(response.status_code, body)
        token = body.get("cliAccessToken")
        if not isinstance(token, str) or not token.strip():
            raise ParseError("令牌接口未返回有效的访问令牌")
        return token.strip()

    def _gateway_call(
        self, api: str, data: dict[str, Any], token: str
    ) -> dict[str, Any]:
        action, gateway = console_gateway(self.settings.region)
        url = (
            f"{gateway}?action={quote(action, safe='')}"
            f"&product={quote(CONSOLE_GATEWAY_PRODUCT, safe='')}"
            f"&api={quote(api, safe='')}"
        )
        payload = json.dumps(
            {"Api": api, "V": "1.0", "Data": {**data, "cornerstoneParam": CORNERSTONE_PARAM}},
            ensure_ascii=False,
        )
        body = urlencode({"params": payload, "region": self.settings.region}).encode("utf-8")
        headers = {
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Bearer {token}",
        }
        response = self._request("POST", url, headers, body)
        envelope = self._decode_json(response, "阿里云百炼控制台网关")
        if not isinstance(envelope, dict):
            raise ParseError("控制台网关响应顶层必须是 JSON 对象")
        node = envelope.get("data")
        if isinstance(node, dict) and node.get("success") is False:
            code, message = _gateway_error(node)
            # The console reports an expired session as e.g. Console.NotLogined.
            if "notlogined" in code.lower():
                raise _TokenExpired(code)
            detail = _safe_error(code, message)
            lowered = code.lower()
            if "team" in lowered and ("notauthoris" in lowered or "notauthoriz" in lowered):
                raise AuthenticationError(f"{TEAM_NOT_AUTHORISED_HINT}。（{detail}）")
            if _is_auth_error(code) or response.status_code in (401, 403):
                raise AuthenticationError(f"阿里云百炼鉴权失败：{detail}")
            if response.status_code == 429 or response.status_code >= 500:
                raise TransientProviderError(f"阿里云百炼暂时不可用：{detail}")
            raise ProviderError(f"控制台网关错误：{detail}")
        _raise_for_http_status(response.status_code, "阿里云百炼控制台网关")
        return unwrap_console_payload(envelope)

    def _request(
        self, method: str, url: str, headers: dict[str, str], body: bytes
    ) -> Any:
        _reject_unofficial_host(url)
        try:
            return self.session.request(
                method=method,
                url=url,
                headers=headers,
                data=body,
                timeout=self.timeout,
                verify=True,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise NetworkError("无法连接阿里云百炼，请检查网络或代理") from exc

    def _decode_json(self, response: Any, what: str) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            status = response.status_code
            if status in (401, 403):
                raise AuthenticationError(
                    "AK/SK 鉴权失败，请检查凭据、权限和系统时间"
                ) from exc
            if status == 429 or status >= 500:
                raise TransientProviderError(f"阿里云百炼暂时不可用（HTTP {status}）") from exc
            if not 200 <= status < 300:
                raise ProviderError(f"{what}返回非 JSON 响应（HTTP {status}）") from exc
            raise ParseError(f"{what}成功响应不是有效 JSON") from exc


# ----------------------------------------------------------------------
# Parsing (pure functions, unit-tested without any transport)
# ----------------------------------------------------------------------


def unwrap_console_payload(result: Any) -> dict[str, Any]:
    """Extract the business payload from the console gateway envelope.

    Mirrors the CLI's `unwrapResponse`: `data.DataV2.data.data`, falling back to
    `data.DataV2.data`, `data.DataV2`, `data.data`, then `data`.
    """
    if not isinstance(result, dict):
        return {}
    data = result.get("data")
    if not isinstance(data, dict):
        return result
    data_v2 = data.get("DataV2")
    if isinstance(data_v2, dict):
        inner = data_v2.get("data")
        inner_data = inner.get("data") if isinstance(inner, dict) else None
        for candidate in (inner_data, inner, data_v2):
            if isinstance(candidate, dict):
                return candidate
        return {}
    direct = data.get("data")
    return direct if isinstance(direct, dict) else data


def parse_token_plan_windows(payload: Any) -> tuple[QuotaWindow, ...]:
    """Build quota windows from the `per*` fields actually present.

    Windows are discovered dynamically so a metering change upstream cannot
    break parsing; unrecognized windows are ignored rather than fatal.
    """
    if not isinstance(payload, dict):
        raise ParseError("Token Plan 用量响应格式无效")
    discovered: dict[str, dict[str, float]] = {}
    for field, value in payload.items():
        match = WINDOW_FIELD_PATTERN.match(field)
        if match is None:
            continue
        number = _optional_number(value)
        if number is None:
            continue
        count, unit, kind = match.groups()
        key = f"per{count}{unit}"
        window = discovered.setdefault(key, {"hours": float(int(count)) * UNIT_HOURS[unit]})
        window["percentage" if kind == "Percentage" else "reset_time"] = number

    windows: list[QuotaWindow] = []
    seen: set[str] = set()
    for key, window in sorted(discovered.items(), key=lambda item: item[1]["hours"]):
        mapped = TOKEN_PLAN_WINDOW_KINDS.get(key)
        if mapped is None or mapped in seen:
            continue
        percentage = window.get("percentage")
        if percentage is None:
            continue
        seen.add(mapped)
        windows.append(
            QuotaWindow(
                kind=mapped,
                # The API reports a usage ratio in [0, 1], not a percentage.
                used_percent=percentage * 100.0,
                reset_at=parse_reset_time(window.get("reset_time")),
            )
        )
    return tuple(windows)


def valid_coding_plan_instance(payload: Any) -> dict[str, Any] | None:
    """Pick the first VALID instance, mirroring the Coding Plan console."""
    if not isinstance(payload, dict):
        raise ParseError("Coding Plan 用量响应格式无效")
    instances = payload.get("codingPlanInstanceInfos")
    if instances is None:
        return None
    if not isinstance(instances, list):
        raise ParseError("codingPlanInstanceInfos 必须是数组")
    for instance in instances:
        if isinstance(instance, dict) and instance.get("status") == "VALID":
            return instance
    return None


def parse_coding_plan_windows(instance: Any) -> tuple[QuotaWindow, ...]:
    if not isinstance(instance, dict):
        raise ParseError("Coding Plan 实例格式无效")
    quota_info = instance.get("codingPlanQuotaInfo")
    if quota_info is None:
        return ()
    if not isinstance(quota_info, dict):
        raise ParseError("codingPlanQuotaInfo 必须是对象")

    windows: list[QuotaWindow] = []
    for prefix, kind in CODING_PLAN_WINDOWS:
        used = _optional_number(quota_info.get(f"{prefix}UsedQuota"))
        total = _optional_number(quota_info.get(f"{prefix}TotalQuota"))
        # Console rule: a usage rate only exists with a positive total and a
        # used value, so an unlimited/absent window is skipped entirely.
        if used is None or total is None or total <= 0:
            continue
        windows.append(
            QuotaWindow(
                kind=kind,
                used_percent=used / total * 100.0,
                reset_at=parse_reset_time(quota_info.get(f"{prefix}QuotaNextRefreshTime")),
                quota=total,
                used=used,
            )
        )
    return tuple(windows)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _coding_plan_request() -> dict[str, Any]:
    return {
        "queryCodingPlanInstanceInfoRequest": {
            "commodityCode": CODING_PLAN_COMMODITY_CODE,
            "onlyLatestOne": True,
        }
    }


def _reject_unofficial_host(url: str) -> None:
    """Only ever reach the fixed official Alibaba Cloud HTTPS endpoints."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in OFFICIAL_HOSTS:
        raise ProviderError("拒绝访问非官方地址")


def _gateway_error(node: dict[str, Any]) -> tuple[str, str]:
    code = node.get("errorCode")
    message = node.get("errorMsg")
    return (
        code if isinstance(code, str) else json.dumps(code, ensure_ascii=False) if code else "",
        message if isinstance(message, str) else "",
    )


def _raise_for_pop_status(status: int, body: dict[str, Any]) -> None:
    """Map an Alibaba POP error envelope onto this app's error categories."""
    if body.get("Success") is not False and 200 <= status < 300:
        return
    code = body.get("Code") if isinstance(body.get("Code"), str) else ""
    message = body.get("Message") if isinstance(body.get("Message"), str) else ""
    detail = _safe_error(code, message)
    if status in (401, 403) or _is_auth_error(code):
        raise AuthenticationError(f"AK/SK 鉴权失败：{detail}；请检查凭据、权限和系统时间")
    if status == 429 or status >= 500:
        raise TransientProviderError(f"阿里云百炼暂时不可用：{detail}")
    if not 200 <= status < 300 or body.get("Success") is False:
        raise ProviderError(f"阿里云百炼接口错误：{detail}")


def _raise_for_http_status(status: int, what: str) -> None:
    if 200 <= status < 300:
        return
    if status in (401, 403):
        raise AuthenticationError("AK/SK 鉴权失败，请检查凭据、权限和系统时间")
    if status == 429 or status >= 500:
        raise TransientProviderError(f"{what}暂时不可用（HTTP {status}）")
    raise ProviderError(f"{what}请求失败（HTTP {status}）")


def _optional_number(value: Any) -> float | None:
    """Accept only finite numbers; anything else counts as absent."""
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _is_auth_error(code: str) -> bool:
    lowered = code.lower()
    return any(part in lowered for part in AUTH_ERROR_PARTS)


def _safe_error(code: str, message: str) -> str:
    code = re.sub(r"[\x00-\x1f\x7f]", "", code)[:80]
    message = re.sub(r"[\x00-\x1f\x7f]", " ", message)[:240]
    return f"{code}: {message}" if code else message or "未知错误"
