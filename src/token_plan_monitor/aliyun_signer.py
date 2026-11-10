"""Alibaba Cloud OpenAPI signature V3 (ACS3-HMAC-SHA256).

Protocol behavior is adapted from the official Bailian CLI
(modelstudioai/cli, `packages/core/src/client/acs.ts`). It is deliberately not
shared with the Volcengine signer: canonical header selection and key
derivation differ.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote

from .models import ConfigurationError

ALGORITHM = "ACS3-HMAC-SHA256"
CONTENT_TYPE = "application/json"
EMPTY_BODY_SHA256 = hashlib.sha256(b"").hexdigest()

# Model Studio control plane, used to mint a console access token from AK/SK.
MODEL_STUDIO_HOSTS = {
    "cn-beijing": "modelstudio.cn-beijing.aliyuncs.com",
    "ap-southeast-1": "modelstudio.ap-southeast-1.aliyuncs.com",
}
TOKEN_ACTION = "GenerateCLIAccessToken"
TOKEN_API_VERSION = "2026-02-10"
TOKEN_PATH = "/modelstudio/cli/generateAccessToken"

# Console gateway that serves the quota APIs. Only the domestic (aliyun.com)
# site is supported: the gateway parameters below are its BAILIAN_ALIYUN site.
CONSOLE_GATEWAY_HOSTS = {
    "cn-beijing": "bailian-cs.console.aliyun.com",
    "ap-southeast-1": "modelstudio-cs.console.aliyun.com",
}
CONSOLE_GATEWAY_PATH = "/cli/api.json"
CONSOLE_GATEWAY_ACTIONS = {
    "cn-beijing": "BroadScopeAspnGateway",
    "ap-southeast-1": "IntlBroadScopeAspnGateway",
}
CONSOLE_GATEWAY_PRODUCT = "sfm_bailian"

# Every host this application is willing to talk to.
OFFICIAL_HOSTS = frozenset(
    set(MODEL_STUDIO_HOSTS.values()) | set(CONSOLE_GATEWAY_HOSTS.values())
)


@dataclass(frozen=True)
class SignedRequest:
    method: str
    url: str
    headers: dict[str, str]
    body: bytes


def canonical_query(params: dict[str, str] | None) -> str:
    """ACS3 canonical query string: sorted, RFC 3986 encoded, no leading '?'."""
    if not params:
        return ""
    return "&".join(
        f"{_uri_encode(key)}={_uri_encode(value)}"
        for key, value in sorted(params.items())
        if value not in (None, "")
    )


def sign_request(
    access_key_id: str,
    secret_access_key: str,
    *,
    host: str,
    path: str,
    action: str,
    version: str,
    body: bytes = b"",
    query: str = "",
    method: str = "POST",
    security_token: str | None = None,
    now: datetime | None = None,
    nonce: str | None = None,
) -> SignedRequest:
    """Sign one Alibaba Cloud OpenAPI (RPC/ROA) call."""
    if not access_key_id.strip() or not secret_access_key.strip():
        raise ConfigurationError("阿里云百炼 AK/SK 未配置")
    for name, value in (("host", host), ("path", path), ("action", action), ("version", version)):
        if not isinstance(value, str) or not value.strip():
            raise ConfigurationError(f"OpenAPI {name} 不能为空")

    timestamp = now or datetime.now(UTC)
    if timestamp.tzinfo is None:
        raise ValueError("Signing timestamp must be timezone-aware")
    # x-acs-date is ISO 8601 in UTC without fractional seconds, colons kept.
    x_acs_date = timestamp.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    signature_nonce = nonce or str(uuid.uuid4())
    content_hash = hashlib.sha256(body).hexdigest()

    headers = {
        "host": host,
        "content-type": CONTENT_TYPE,
        "x-acs-action": action,
        "x-acs-version": version,
        "x-acs-date": x_acs_date,
        "x-acs-signature-nonce": signature_nonce,
        "x-acs-content-sha256": content_hash,
    }
    if security_token:
        headers["x-acs-security-token"] = security_token

    signed_headers = ";".join(sorted(headers))
    canonical_headers = "".join(f"{key}:{headers[key]}\n" for key in sorted(headers))
    canonical_request = "\n".join(
        (method, path, query, canonical_headers, signed_headers, content_hash)
    )
    string_to_sign = f"{ALGORITHM}\n{_sha256_hex(canonical_request.encode('utf-8'))}"
    signature = hmac.new(
        secret_access_key.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    headers["authorization"] = (
        f"{ALGORITHM} Credential={access_key_id},"
        f"SignedHeaders={signed_headers},Signature={signature}"
    )
    return SignedRequest(
        method=method,
        url=f"https://{host}{path}{f'?{query}' if query else ''}",
        headers=headers,
        body=body,
    )


def model_studio_host(region: str) -> str:
    host = MODEL_STUDIO_HOSTS.get(region)
    if host is None:
        raise ConfigurationError(f"阿里云百炼不支持 {region} 地域")
    return host


def console_gateway(region: str) -> tuple[str, str]:
    """Return (gateway action, gateway URL) for a supported region."""
    host = CONSOLE_GATEWAY_HOSTS.get(region)
    action = CONSOLE_GATEWAY_ACTIONS.get(region)
    if host is None or action is None:
        raise ConfigurationError(f"阿里云百炼不支持 {region} 地域")
    return action, f"https://{host}{CONSOLE_GATEWAY_PATH}"


def _uri_encode(value: str) -> str:
    # quote() already escapes !'()*, matching RFC 3986 strict encoding.
    return quote(value, safe="", encoding="utf-8", errors="strict")


def _sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
