from datetime import UTC, datetime

from token_plan_monitor.volcengine_signer import (
    EMPTY_BODY_SHA256,
    canonical_query,
    sign_request,
)

NOW = datetime(2024, 6, 21, tzinfo=UTC)


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
