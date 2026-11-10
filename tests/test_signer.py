from datetime import UTC, datetime

import pytest

from token_plan_monitor.aliyun_signer import (
    EMPTY_BODY_SHA256 as ALIYUN_EMPTY_BODY_SHA256,
    canonical_query as aliyun_canonical_query,
    sign_request as aliyun_sign_request,
)
from token_plan_monitor.models import ConfigurationError
from token_plan_monitor.volcengine_signer import (
    EMPTY_BODY_SHA256,
    canonical_query,
    sign_request,
)

NOW = datetime(2024, 6, 21, tzinfo=UTC)
ALIYUN_NOW = datetime(2026, 6, 21, tzinfo=UTC)
ALIYUN_HOST = "modelstudio.cn-beijing.aliyuncs.com"
ALIYUN_PATH = "/modelstudio/cli/generateAccessToken"


def test_canonical_query_is_sorted_and_encoded():
    assert canonical_query("GetAFPUsage", "cn-beijing") == (
        "Action=GetAFPUsage&Region=cn-beijing&Version=2024-01-01"
    )
    assert canonical_query("Get AFP/测试", "cn-beijing").startswith(
        "Action=Get%20AFP%2F%E6%B5%8B%E8%AF%95&"
    )


def test_signature_matches_fixed_regression_vector():
    request = sign_request("AKLTtest", "secretkey", "GetAFPUsage", "cn-beijing", NOW)
    assert request.method == "POST"
    assert request.body == b""
    assert request.url == (
        "https://open.volcengineapi.com/?Action=GetAFPUsage&"
        "Region=cn-beijing&Version=2024-01-01"
    )
    assert request.headers["X-Date"] == "20240621T000000Z"
    assert request.headers["X-Content-Sha256"] == EMPTY_BODY_SHA256
    assert request.headers["Authorization"] == (
        "HMAC-SHA256 Credential=AKLTtest/20240621/cn-beijing/ark/request, "
        "SignedHeaders=host;x-date;x-content-sha256;content-type, "
        "Signature=4ceb5e7c3c834fe8604cccf04eeec5ab09045d2e5a36723921f8cea691b0a186"
    )


def test_signature_is_deterministic_and_input_sensitive():
    first = sign_request("AKLTtest", "secretkey", "GetAFPUsage", "cn-beijing", NOW)
    second = sign_request("AKLTtest", "secretkey", "GetAFPUsage", "cn-beijing", NOW)
    changed = sign_request(
        "AKLTtest", "secretkey", "GetCodingPlanUsage", "cn-beijing", NOW
    )
    assert first == second
    assert first.headers["Authorization"] != changed.headers["Authorization"]


def test_acs3_signature_matches_independent_regression_vector():
    request = aliyun_sign_request(
        "LTAItest",
        "secretkey",
        host=ALIYUN_HOST,
        path=ALIYUN_PATH,
        action="GenerateCLIAccessToken",
        version="2026-02-10",
        now=ALIYUN_NOW,
        nonce="fixed-nonce",
    )
    assert request.method == "POST"
    assert request.body == b""
    assert request.url == f"https://{ALIYUN_HOST}{ALIYUN_PATH}"
    assert request.headers["x-acs-date"] == "2026-06-21T00:00:00Z"
    assert request.headers["x-acs-content-sha256"] == ALIYUN_EMPTY_BODY_SHA256
    assert request.headers["authorization"] == (
        "ACS3-HMAC-SHA256 Credential=LTAItest,"
        "SignedHeaders=content-type;host;x-acs-action;x-acs-content-sha256;"
        "x-acs-date;x-acs-signature-nonce;x-acs-version,"
        "Signature=efa2bd7298633679c4e8c6da2291872d8cda5393d30af5a922d2f4cf8b07be5e"
    )


def test_acs3_empty_body_hash_is_the_well_known_digest():
    assert ALIYUN_EMPTY_BODY_SHA256 == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


def test_acs3_canonical_query_sorts_and_rfc3986_encodes():
    assert aliyun_canonical_query({"b": "2", "a": "1"}) == "a=1&b=2"
    assert aliyun_canonical_query({"Model": "get it!'()*"}) == "Model=get%20it%21%27%28%29%2A"
    assert aliyun_canonical_query({}) == ""
    assert aliyun_canonical_query({"Skip": ""}) == ""


def test_acs3_requires_credentials():
    with pytest.raises(ConfigurationError):
        aliyun_sign_request("", "sk", host=ALIYUN_HOST, path=ALIYUN_PATH,
                            action="A", version="1")


def test_acs3_signature_covers_the_body():
    fixed = dict(host=ALIYUN_HOST, path=ALIYUN_PATH, action="A", version="1",
                 now=ALIYUN_NOW, nonce="n")
    empty = aliyun_sign_request("ak", "sk", **fixed)
    with_body = aliyun_sign_request("ak", "sk", body=b'{"a":1}', **fixed)
    assert empty.headers["authorization"] != with_body.headers["authorization"]
    assert with_body.headers["x-acs-content-sha256"] != ALIYUN_EMPTY_BODY_SHA256
