import json
from datetime import UTC, datetime
from urllib.parse import parse_qs, parse_qsl, urlsplit

import pytest
import requests

from token_plan_monitor.aliyun_signer import EMPTY_BODY_SHA256
from token_plan_monitor.bailian import (
    CODING_PLAN_USAGE_API,
    TOKEN_PLAN_USAGE_API,
    BailianClient,
    _reject_unofficial_host,
    parse_coding_plan_windows,
    parse_token_plan_windows,
    unwrap_console_payload,
    valid_coding_plan_instance,
)
from token_plan_monitor.models import (
    AuthenticationError,
    Credentials,
    NetworkError,
    NoActivePlanError,
    ParseError,
    ProviderError,
    QuotaWindow,
    Settings,
    TransientProviderError,
)

NOW = datetime(2026, 11, 2, tzinfo=UTC)
RESET_MS = 1_785_868_560_000
RESET_AT = datetime.fromtimestamp(RESET_MS / 1000, UTC)


class FakeResponse:
    def __init__(self, body, status=200):
        self.body = body
        self.status_code = status

    def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


class FakeSession:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def request(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def make_client(*outcomes):
    session = FakeSession(*outcomes)
    client = BailianClient(
        Settings(provider="bailian"),
        Credentials("LTAItest", "secret"),
        session=session,
        clock=lambda: NOW,
    )
    return client, session


def token_response(token="token-1"):
    return FakeResponse({"cliAccessToken": token, "Success": True})


def gateway(payload):
    """Success envelope: business payload nested under data.DataV2.data.data."""
    return FakeResponse({
        "code": "200",
        "data": {"success": True, "DataV2": {"data": {"data": payload}}},
        "httpStatusCode": "200",
    })


def gateway_failure(error_code, message="denied", status=200):
    return FakeResponse({
        "code": "200",
        "data": {"success": False, "errorCode": error_code, "errorMsg": message},
    }, status=status)


def coding_payload():
    return {
        "codingPlanInstanceInfos": [
            {"status": "EXPIRED", "instanceType": "Old"},
            {
                "status": "VALID",
                "instanceType": "Standard",
                "codingPlanQuotaInfo": {
                    "per5HourUsedQuota": 28,
                    "per5HourTotalQuota": 100,
                    "per5HourQuotaNextRefreshTime": RESET_MS,
                    "perWeekUsedQuota": 0,
                    "perWeekTotalQuota": 0,
                    "perBillMonthUsedQuota": 810,
                    "perBillMonthTotalQuota": 1000,
                },
            },
        ]
    }


# ----------------------------------------------------------------------
# Pure parsing
# ----------------------------------------------------------------------


def test_token_plan_ratios_become_percentages_with_epoch_reset():
    windows = parse_token_plan_windows({"per1MonthPercentage": 0.5, "per1MonthResetTime": RESET_MS})
    assert windows == (QuotaWindow(kind="monthly", used_percent=50.0, reset_at=RESET_AT),)


def test_token_plan_windows_are_discovered_and_ordered_by_duration():
    payload = {
        "per1MonthPercentage": 0.9,
        "per1WeekPercentage": 0.4,
        "per1WeekResetTime": RESET_MS,
        "per5HourPercentage": 0.1,
        "requestId": "ignored",
    }
    kinds = [window.kind for window in parse_token_plan_windows(payload)]
    assert kinds == ["five_hour", "weekly", "monthly"]


def test_token_plan_ignores_unknown_windows_and_non_numeric_fields():
    payload = {
        "per2DayPercentage": 0.2,
        "per1MonthPercentage": "not-a-number",
        "per1MonthResetTime": RESET_MS,
    }
    assert parse_token_plan_windows(payload) == ()


def test_token_plan_skips_window_without_percentage():
    assert parse_token_plan_windows({"per1MonthResetTime": RESET_MS}) == ()


def test_token_plan_rejects_non_object_payload():
    with pytest.raises(ParseError):
        parse_token_plan_windows(["nope"])


def test_coding_plan_picks_first_valid_instance_and_skips_zero_totals():
    instance = valid_coding_plan_instance(coding_payload())
    windows = parse_coding_plan_windows(instance)
    assert [window.kind for window in windows] == ["five_hour", "monthly"]
    five_hour, monthly = windows
    assert five_hour.used_percent == pytest.approx(28.0)
    assert (five_hour.quota, five_hour.used) == (100.0, 28.0)
    assert five_hour.reset_at == RESET_AT
    assert monthly.used_percent == pytest.approx(81.0)


def test_coding_plan_reports_no_instance_without_valid_status():
    assert valid_coding_plan_instance({"codingPlanInstanceInfos": [{"status": "EXPIRED"}]}) is None
    assert valid_coding_plan_instance({}) is None


def test_coding_plan_rejects_non_list_instances():
    with pytest.raises(ParseError):
        valid_coding_plan_instance({"codingPlanInstanceInfos": {"status": "VALID"}})


def test_unwrap_console_payload_follows_datav2_then_falls_back():
    payload = {"per1MonthPercentage": 0.25}
    assert unwrap_console_payload({"data": {"DataV2": {"data": {"data": payload}}}}) == payload
    assert unwrap_console_payload({"data": {"data": payload}}) == payload
    assert unwrap_console_payload({"data": {"DataV2": {}}}) == {}
    assert unwrap_console_payload({"data": {"flat": 1}}) == {"flat": 1}
    assert unwrap_console_payload([]) == {}


# ----------------------------------------------------------------------
# Client behaviour
# ----------------------------------------------------------------------


def test_query_uses_token_plan_windows_when_present():
    client, session = make_client(
        token_response(),
        gateway({"per5HourPercentage": 0.43, "per5HourResetTime": RESET_MS}),
    )
    snapshot = client.query()
    assert snapshot.plan_type == "token_plan"
    assert snapshot.plan_name == "Token Plan 个人版"
    assert snapshot.windows[0].kind == "five_hour"
    assert snapshot.windows[0].used_percent == 43.0
    assert snapshot.windows[0].quota is None
    assert len(session.calls) == 2


def test_token_request_is_signed_against_model_studio_with_empty_body():
    client, session = make_client(token_response(), gateway({"per1MonthPercentage": 0.1}))
    client.query()
    token_call, gateway_call = session.calls
    assert token_call["url"].startswith("https://modelstudio.cn-beijing.aliyuncs.com/")
    assert token_call["data"] == b""
    assert token_call["headers"]["x-acs-content-sha256"] == EMPTY_BODY_SHA256
    assert token_call["headers"]["x-acs-action"] == "GenerateCLIAccessToken"
    assert token_call["headers"]["authorization"].startswith("ACS3-HMAC-SHA256 Credential=LTAItest,")
    assert token_call["verify"] is True and token_call["allow_redirects"] is False


def test_gateway_call_is_form_encoded_with_bearer_token():
    client, session = make_client(token_response(), gateway({"per1MonthPercentage": 0.1}))
    client.query()
    gateway_call = session.calls[1]
    parsed = urlsplit(gateway_call["url"])
    assert parsed.hostname == "bailian-cs.console.aliyun.com"
    query = dict(parse_qsl(parsed.query))
    assert query["api"] == TOKEN_PLAN_USAGE_API
    assert query["action"] == "BroadScopeAspnGateway"
    assert gateway_call["headers"]["Authorization"] == "Bearer token-1"
    form = parse_qs(gateway_call["data"].decode("utf-8"))
    params = json.loads(form["params"][0])
    assert params["Api"] == TOKEN_PLAN_USAGE_API
    assert params["Data"]["cornerstoneParam"]["consoleSite"] == "BAILIAN_ALIYUN"
    assert form["region"] == ["cn-beijing"]


def test_query_falls_back_to_coding_plan():
    client, session = make_client(
        token_response(),
        gateway({}),
        gateway(coding_payload()),
    )
    snapshot = client.query()
    assert snapshot.plan_type == "coding_plan"
    assert snapshot.plan_name == "Coding Plan Standard"
    assert len(snapshot.windows) == 2
    assert TOKEN_PLAN_USAGE_API.replace("/", "%2F") in session.calls[1]["url"]
    assert CODING_PLAN_USAGE_API in session.calls[2]["url"]


def test_query_requires_some_active_subscription():
    client, _ = make_client(
        token_response(),
        gateway({}),
        gateway({"codingPlanInstanceInfos": []}),
    )
    with pytest.raises(NoActivePlanError):
        client.query()


def test_expired_console_token_is_minted_once_and_retried():
    client, session = make_client(
        token_response("token-1"),
        gateway_failure("Console.NotLogined"),
        token_response("token-2"),
        gateway({"per1MonthPercentage": 0.2}),
    )
    snapshot = client.query()
    assert snapshot.windows[0].used_percent == 20.0
    bearer = [call["headers"].get("Authorization") for call in session.calls]
    assert bearer[1] == "Bearer token-1"
    assert bearer[3] == "Bearer token-2"


def test_repeated_not_logined_becomes_an_authentication_error():
    client, _ = make_client(
        token_response(),
        gateway_failure("Console.NotLogined"),
        token_response(),
        gateway_failure("Console.NotLogined"),
    )
    with pytest.raises(AuthenticationError):
        client.query()


def test_gateway_business_error_maps_to_provider_error():
    client, _ = make_client(token_response(), gateway_failure("InvalidParameter", "bad arg"))
    with pytest.raises(ProviderError):
        client.query()


def test_gateway_permission_error_maps_to_authentication_error():
    client, _ = make_client(token_response(), gateway_failure("NoPermission", "denied"))
    with pytest.raises(AuthenticationError):
        client.query()


def test_team_not_authorised_points_at_the_console_account_management_fix():
    client, _ = make_client(
        token_response(),
        gateway_failure("BailianGateway.Team.NotAuthorised", "team not authorised"),
    )
    with pytest.raises(AuthenticationError) as failure:
        client.query()
    message = failure.value.message
    # Actionable guidance plus the raw code, so the user can fix it in the console.
    assert "账号管理" in message
    assert "BailianGateway.Team.NotAuthorised" in message
    assert failure.value.category == "authentication"


def test_gateway_server_error_envelope_is_transient():
    client, _ = make_client(
        token_response(), gateway_failure("ServiceUnavailable", "busy", status=503)
    )
    with pytest.raises(TransientProviderError):
        client.query()


def test_token_pop_error_maps_to_authentication_error():
    client, _ = make_client(
        FakeResponse({"Code": "InvalidAccessKeyId.NotFound", "Message": "bad ak"}, status=403)
    )
    with pytest.raises(AuthenticationError):
        client.query()


def test_transient_http_statuses_map_to_transient_errors():
    for status in (429, 503):
        client, _ = make_client(FakeResponse(ValueError("not json"), status=status))
        with pytest.raises(TransientProviderError):
            client.query()


def test_network_failures_are_recoverable_errors():
    client, _ = make_client(requests.ConnectionError("boom"))
    with pytest.raises(NetworkError):
        client.query()


def test_unofficial_hosts_are_refused():
    for url in (
        "http://modelstudio.cn-beijing.aliyuncs.com/x",
        "https://evil.example.com/cli/api.json",
        "https://modelstudio.cn-beijing.aliyuncs.com.evil.example.com/",
    ):
        with pytest.raises(ProviderError):
            _reject_unofficial_host(url)


def test_official_hosts_are_allowed():
    _reject_unofficial_host("https://bailian-cs.console.aliyun.com/cli/api.json")
    _reject_unofficial_host("https://modelstudio.cn-beijing.aliyuncs.com/modelstudio/cli/generateAccessToken")
