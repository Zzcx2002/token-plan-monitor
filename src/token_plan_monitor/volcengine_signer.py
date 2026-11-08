"""Volcengine OpenAPI signature V4.

Protocol behavior is adapted from CC Switch's coding_plan.rs. It is deliberately
not replaced with an AWS SigV4 signer: header order and key derivation differ.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote

from .models import ConfigurationError

OPENAPI_HOST = "open.volcengineapi.com"
API_VERSION = "2024-01-01"
SERVICE = "ark"
CONTENT_TYPE = "application/json; charset=utf-8"
SIGNED_HEADERS = "host;x-date;x-content-sha256;content-type"
EMPTY_BODY_SHA256 = hashlib.sha256(b"").hexdigest()


@dataclass(frozen=True)
class SignedRequest:
    method: str
    url: str
    headers: dict[str, str]
    body: bytes


def canonical_query(action: str, region: str) -> str:
    pairs = sorted(
        (("Action", action), ("Region", region), ("Version", API_VERSION)),
        key=lambda pair: pair[0],
    )
    return "&".join(
        f"{_uri_encode(key)}={_uri_encode(value)}" for key, value in pairs
    )


def sign_request(
    access_key_id: str,
    secret_access_key: str,
    action: str,
    region: str,
    now: datetime | None = None,
) -> SignedRequest:
    if not access_key_id.strip() or not secret_access_key.strip():
        raise ConfigurationError("火山方舟 AK/SK 未配置")
    if not action.strip() or not region.strip():
        raise ConfigurationError("OpenAPI Action 和 Region 不能为空")

    timestamp = now or datetime.now(UTC)
    if timestamp.tzinfo is None:
        raise ValueError("Signing timestamp must be timezone-aware")
    timestamp = timestamp.astimezone(UTC)

    query = canonical_query(action, region)
    body = b""
    x_date = timestamp.strftime("%Y%m%dT%H%M%SZ")
    short_date = timestamp.strftime("%Y%m%d")
    content_hash = hashlib.sha256(body).hexdigest()

    canonical_headers = (
        f"host:{OPENAPI_HOST}\n"
        f"x-date:{x_date}\n"
        f"x-content-sha256:{content_hash}\n"
        f"content-type:{CONTENT_TYPE}\n"
    )
    canonical_request = (
        f"POST\n/\n{query}\n{canonical_headers}\n"
        f"{SIGNED_HEADERS}\n{content_hash}"
    )
    credential_scope = f"{short_date}/{region}/{SERVICE}/request"
    string_to_sign = (
        f"HMAC-SHA256\n{x_date}\n{credential_scope}\n"
        f"{_sha256_hex(canonical_request.encode('utf-8'))}"
    )

    k_date = _hmac(secret_access_key.encode("utf-8"), short_date.encode("ascii"))
    k_region = _hmac(k_date, region.encode("utf-8"))
    k_service = _hmac(k_region, SERVICE.encode("ascii"))
    k_signing = _hmac(k_service, b"request")
    signature = hmac.new(
        k_signing, string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    authorization = (
        f"HMAC-SHA256 Credential={access_key_id}/{credential_scope}, "
        f"SignedHeaders={SIGNED_HEADERS}, Signature={signature}"
    )
    headers = {
        "Host": OPENAPI_HOST,
        "X-Date": x_date,
        "X-Content-Sha256": content_hash,
        "Content-Type": CONTENT_TYPE,
        "Authorization": authorization,
    }
    return SignedRequest(
        method="POST",
        url=f"https://{OPENAPI_HOST}/?{query}",
        headers=headers,
        body=body,
    )


def _uri_encode(value: str) -> str:
    return quote(value, safe="-_.~", encoding="utf-8", errors="strict")


def _sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hmac(key: bytes, value: bytes) -> bytes:
    return hmac.new(key, value, hashlib.sha256).digest()
