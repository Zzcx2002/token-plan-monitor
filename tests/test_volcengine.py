from datetime import UTC, datetime

import pytest
import requests

from token_plan_monitor.models import (
    AuthenticationError,
    Credentials,
    NetworkError,
    NoActivePlanError,
    ParseError,
    ProviderError,
    Settings,
)
from token_plan_monitor.volcengine import (
    VolcengineClient,
    parse_agent_windows,
    parse_coding_windows,
    parse_reset_time,
)

NOW = datetime(2026, 11, 2, tzinfo=UTC)


class FakeResponse:
    def __init__(self, body, status=200, json_error=None):
        self.body = body
        self.status_code = status
        self.json_error = json_error

    def json(self):
        if self.json_error is not None:
            raise self.json_error
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
    client = VolcengineClient(
        Settings(),
        Credentials("AKLTtest", "secret"),
        session=session,
        clock=lambda: NOW,
    )
    return client, session


def agent_body(quota=50.0, used=12.5):
    return {
        "Result": {
            "PlanType": "Large",
            "AFPFiveHour": {
                "Quota": quota,
                "Used": used,
                "ResetTime": 1778806800000,
            },
            "AFPDaily": {"Quota": 100.0, "Used": 22.5},
            "AFPWeekly": {"Quota": 0.0, "Used": 0.0},
            "AFPMonthly": {"Quota": 0.0, "Used": 0.0},
        }
    }


def coding_body():
    return {
        "Result": {
            "Status": "Running",
            "QuotaUsage": [
                {"Level": "session", "Percent": 0.0, "ResetTimestamp": -1},
                {"Level": "weekly", "Percent": 1.672568, "ResetTimestamp": 1782057600},
                {"Level": "monthly", "Percent": 0.836284, "ResetTimestamp": 1784303999},
            ],
        }
    }


def test_agent_three_windows_and_daily_filter():
    result = {
        "AFPFiveHour": {"Quota": 50, "Used": 12.5, "ResetTime": 1778806800000},
        "AFPDaily": {"Quota": 100, "Used": 22.5},
        "AFPWeekly": {"Quota": 500, "Used": 150, "ResetTime": 1779062400000},
        "AFPMonthly": {"Quota": 2000, "Used": 850.5, "ResetTime": 1780531200000},
    }
    windows = parse_agent_windows(result)
    assert [window.kind for window in windows] == ["five_hour", "weekly", "monthly"]
    assert [window.used_percent for window in windows] == [25.0, 30.0, 42.525]
    assert windows[0].remaining_absolute == 37.5
    assert all(window.reset_at is not None for window in windows)


def test_agent_zero_quota_and_partial_windows():
    assert parse_agent_windows(agent_body(0, 0)["Result"]) == ()
    windows = parse_agent_windows(agent_body()["Result"])
    assert len(windows) == 1
    assert windows[0].used_percent == 25.0


def test_coding_real_response_levels():
    windows = parse_coding_windows(coding_body()["Result"])
    assert [window.kind for window in windows] == ["five_hour", "weekly", "monthly"]
    assert windows[0].reset_at is None
    assert windows[1].used_percent == pytest.approx(1.672568)
    assert windows[2].remaining_percent == pytest.approx(99.163716)


def test_coding_unknown_window_and_fallback_fields():
    windows = parse_coding_windows(
        {
            "Usages": [
                {"Type": "daily", "Percent": 9},
                {"Period": "week", "UsedPercent": 20},
                {"Window": "5h", "UsagePercent": 10},
            ]
        }
    )
    assert [window.kind for window in windows] == ["weekly", "five_hour"]
    assert parse_coding_windows({}) == ()


def test_reset_time_seconds_milliseconds_iso_and_invalid():
    expected = datetime(2026, 6, 22, tzinfo=UTC)
    timestamp = expected.timestamp()
    assert parse_reset_time(timestamp) == expected
    assert parse_reset_time(timestamp * 1000) == expected
    assert parse_reset_time("2026-06-22T00:00:00Z") == expected
    assert parse_reset_time(-1) is None
    with pytest.raises(ParseError):
        parse_reset_time("not-a-date")


def test_query_agent_does_not_call_coding_and_request_is_exact():
    client, session = make_client(FakeResponse(agent_body()))
    snapshot = client.query()
    assert snapshot.plan_type == "agent_plan"
    assert snapshot.plan_name == "Agent Plan Large"
    assert snapshot.queried_at == NOW
    assert len(session.calls) == 1
    call = session.calls[0]
    assert call["method"] == "POST"
    assert call["data"] == b""
    assert call["timeout"] == 15
    assert call["verify"] is True
    assert call["allow_redirects"] is False
    assert "Action=GetAFPUsage" in call["url"]
    assert call["headers"]["Authorization"].startswith("HMAC-SHA256 ")


def test_query_falls_back_only_after_valid_empty_agent_result():
    client, session = make_client(
        FakeResponse(agent_body(0, 0)),
        FakeResponse(coding_body()),
    )
    snapshot = client.query()
    assert snapshot.plan_type == "coding_plan"
    assert len(session.calls) == 2
    assert "Action=GetCodingPlanUsage" in session.calls[1]["url"]


def test_authentication_error_stops_fallback_even_with_http_400():
    body = {
        "ResponseMetadata": {
            "Error": {"Code": "InvalidAuthorization", "Message": "bad signature"}
        }
    }
    client, session = make_client(FakeResponse(body, status=400))
    with pytest.raises(AuthenticationError):
        client.query()
    assert len(session.calls) == 1


def test_network_error_stops_fallback():
    client, session = make_client(requests.Timeout("secret request details"))
    with pytest.raises(NetworkError) as error:
        client.query()
    assert len(session.calls) == 1
    assert "secret request details" not in error.value.message


def test_http_200_embedded_provider_error_is_not_success():
    body = {"ResponseMetadata": {"Error": {"Code": "InvalidParameter.Action", "Message": "bad action"}}}
    client, _ = make_client(FakeResponse(body))
    with pytest.raises(ProviderError):
        client.query()


def test_malformed_agent_result_does_not_fall_back():
    client, session = make_client(FakeResponse({"Result": []}))
    with pytest.raises(ParseError):
        client.query()
    assert len(session.calls) == 1


def test_no_active_plan_is_visible():
    client, _ = make_client(
        FakeResponse(agent_body(0, 0)),
        FakeResponse({"Result": {"QuotaUsage": []}}),
    )
    with pytest.raises(NoActivePlanError):
        client.query()


def test_non_json_success_is_parse_error():
    client, _ = make_client(FakeResponse(None, json_error=ValueError("bad json")))
    with pytest.raises(ParseError):
        client.query()
