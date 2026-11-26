import json

import pytest
import requests

from token_plan_monitor.macos.layout import resolve_claude_seed
from token_plan_monitor.model_catalog import (
    ModelCatalogError,
    fetch_model_ids,
    is_chat_model,
    merge_model_options,
    models_url_for,
    parse_model_ids,
)
from token_plan_monitor.models import ConfigurationError
from token_plan_monitor.providers import ClaudeModelOption, get_provider
from token_plan_monitor.storage import Storage


class FakeKeyring:
    def __init__(self):
        self.values = {}

    def get_password(self, service, username):
        return self.values.get((service, username))

    def set_password(self, service, username, value):
        self.values[(service, username)] = value


class FakeResponse:
    def __init__(self, status_code=200, payload=None, raise_json=False):
        self.status_code = status_code
        self._payload = payload
        self._raise_json = raise_json

    def json(self):
        if self._raise_json:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.calls = []

    def get(self, url, headers=None, timeout=None, allow_redirects=None):
        self.calls.append((url, headers, timeout, allow_redirects))
        if self._error is not None:
            raise self._error
        return self._response


# --- URL derivation ---------------------------------------------------------

def test_models_url_derives_same_host_compatible_mode():
    assert models_url_for("https://token-plan.cn-beijing.maas.aliyuncs.com/apps/anthropic") == (
        "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/models")
    assert models_url_for("https://token-plan.maas.qianwenaiapi.com/apps/anthropic") == (
        "https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1/models")


def test_models_url_strips_trailing_slash_and_anything_after_anthropic_path():
    assert models_url_for("https://token-plan.maas.aliyuncs.com/apps/anthropic/") == (
        "https://token-plan.maas.aliyuncs.com/compatible-mode/v1/models")
    assert models_url_for("https://proxy.example.com/apps/anthropic/v1") == (
        "https://proxy.example.com/compatible-mode/v1/models")


def test_models_url_rejects_empty_or_hostless():
    with pytest.raises(ModelCatalogError):
        models_url_for("")
    with pytest.raises(ModelCatalogError):
        models_url_for("not-a-url")


@pytest.mark.parametrize("base_url", [
    # Plaintext HTTP would put the Bearer key on the wire in the clear.
    "http://token-plan.cn-beijing.maas.aliyuncs.com/apps/anthropic",
    "http://evil.example.com/apps/anthropic",
    # Userinfo smuggles the real host past a path/substring check: the key
    # would go to evil.example.com while the URL reads like a template instance.
    "https://token-plan.cn-beijing@evil.example.com/apps/anthropic",
    "https://user:pass@token-plan.cn-beijing.maas.aliyuncs.com/apps/anthropic",
])
def test_models_url_rejects_plaintext_http_and_userinfo(base_url):
    with pytest.raises(ModelCatalogError):
        models_url_for(base_url)


@pytest.mark.parametrize("base_url", [
    "http://localhost:8080/apps/anthropic",
    "http://127.0.0.1/apps/anthropic",
])
def test_models_url_allows_local_debug_http(base_url):
    # Same localhost exception as the apply path (normalize_base_url).
    assert models_url_for(base_url).endswith("/compatible-mode/v1/models")
    assert models_url_for(base_url).startswith("http://")


# --- Chat-model filtering ---------------------------------------------------

@pytest.mark.parametrize("model_id", [
    "wan2.7-image", "wan2.7-image-pro", "wan3.0-video", "qwen-image-3.0-pro",
    "qwen-audio-3.0-tts-plus", "qwen-audio-3.0-asr-flash",
    "qwen-audio-3.0-realtime-plus", "decision-model-preview",
])
def test_non_chat_models_are_excluded(model_id):
    assert is_chat_model(model_id) is False


@pytest.mark.parametrize("model_id", [
    "auto", "qwen3.8-max", "qwen3.8-flash", "qwen4-max", "qwen4-max-preview",
    "deepseek-v4-pro", "deepseek-v4.1-flash-1017", "glm-5.3",
])
def test_chat_models_are_kept(model_id):
    assert is_chat_model(model_id) is True


# --- Payload parsing --------------------------------------------------------

def test_parse_filters_dedupes_and_preserves_order():
    payload = {"data": [
        {"id": "qwen4-max"}, {"id": "wan3.0-video"}, {"id": "auto"},
        {"id": "qwen4-max"}, {"id": "qwen-audio-3.0-tts-plus"},
        {"id": "glm-5.3"},
    ]}
    assert parse_model_ids(payload) == ("qwen4-max", "auto", "glm-5.3")


def test_parse_rejects_unsafe_ids():
    payload = {"data": [
        {"id": "bad id with spaces"}, {"id": ""}, {"id": "x" * 300},
        {"id": "ok-model"}, {"id": 42}, {"notid": "x"}, "plain-string",
    ]}
    assert parse_model_ids(payload) == ("ok-model",)


@pytest.mark.parametrize("payload", [None, [], {}, {"data": "nope"}, {"data": None}])
def test_parse_rejects_malformed_envelope(payload):
    with pytest.raises(ModelCatalogError):
        parse_model_ids(payload)


# --- Fetch ------------------------------------------------------------------

def test_fetch_sends_bearer_key_to_derived_url_and_returns_ids():
    session = FakeSession(FakeResponse(200, {"data": [{"id": "qwen4-max"}, {"id": "auto"}]}))
    ids = fetch_model_ids("https://token-plan.maas.aliyuncs.com/apps/anthropic", "sk-sp-x", session)
    assert ids == ("qwen4-max", "auto")
    url, headers, _timeout, allow_redirects = session.calls[0]
    assert url == "https://token-plan.maas.aliyuncs.com/compatible-mode/v1/models"
    assert headers["Authorization"] == "Bearer sk-sp-x"
    # A redirect could forward the Bearer key to another host.
    assert allow_redirects is False


def test_fetch_non_200_raises():
    session = FakeSession(FakeResponse(401, {}))
    with pytest.raises(ModelCatalogError):
        fetch_model_ids("https://token-plan.maas.aliyuncs.com/apps/anthropic", "k", session)


def test_fetch_network_error_raises():
    session = FakeSession(error=requests.ConnectionError("boom"))
    with pytest.raises(ModelCatalogError):
        fetch_model_ids("https://token-plan.maas.aliyuncs.com/apps/anthropic", "k", session)


def test_fetch_bad_json_raises():
    session = FakeSession(FakeResponse(200, raise_json=True))
    with pytest.raises(ModelCatalogError):
        fetch_model_ids("https://token-plan.maas.aliyuncs.com/apps/anthropic", "k", session)


def test_fetch_empty_chat_list_raises():
    session = FakeSession(FakeResponse(200, {"data": [{"id": "wan3.0-video"}]}))
    with pytest.raises(ModelCatalogError):
        fetch_model_ids("https://token-plan.maas.aliyuncs.com/apps/anthropic", "k", session)


# --- Merge ------------------------------------------------------------------

def test_merge_keeps_curated_labels_and_appends_new_models():
    static = (
        ClaudeModelOption("auto", "auto · 按请求自动匹配"),
        ClaudeModelOption("qwen3.8-max", "qwen3.8-max · 推理/视觉理解"),
        ClaudeModelOption("qwen3.8-flash", "qwen3.8-flash · 推理/视觉理解", lightweight=True),
    )
    fetched = ("qwen3.8-flash", "qwen4-max", "auto", "qwen3.8-max", "qwen4-flash")
    merged = merge_model_options(static, fetched)
    assert [o.id for o in merged] == ["auto", "qwen3.8-max", "qwen3.8-flash", "qwen4-max", "qwen4-flash"]
    by_id = {o.id: o for o in merged}
    assert by_id["auto"].label == "auto · 按请求自动匹配"
    assert by_id["qwen3.8-flash"].lightweight is True
    # New models get plain labels and an inferred lightweight flag.
    assert by_id["qwen4-max"].label == "qwen4-max"
    assert by_id["qwen4-max"].lightweight is False   # "-max" is heavy
    assert by_id["qwen4-flash"].lightweight is True


def test_merge_drops_retired_models():
    static = (ClaudeModelOption("auto", "auto"), ClaudeModelOption("old-model", "old-model"))
    merged = merge_model_options(static, ("auto",))
    assert [o.id for o in merged] == ["auto"]


def test_merge_empty_fetch_keeps_nothing_but_caller_guard_preserves_static():
    # merge itself returns the live set (empty here); the UI only calls it when
    # the cache is non-empty, so the static catalogue is never wiped.
    static = (ClaudeModelOption("auto", "auto"),)
    assert merge_model_options(static, ()) == ()


# --- Storage cache ----------------------------------------------------------

def test_catalog_round_trip_and_provider_isolation(tmp_path):
    storage = Storage(tmp_path, FakeKeyring())
    assert storage.load_model_catalog("bailian") == ()
    storage.save_model_catalog("bailian", ("auto", "qwen4-max"))
    assert storage.load_model_catalog("bailian") == ("auto", "qwen4-max")
    assert storage.load_model_catalog("volcengine") == ()
    storage.save_model_catalog("volcengine", ("other",))
    assert storage.load_model_catalog("bailian") == ("auto", "qwen4-max")
    assert storage.load_model_catalog("volcengine") == ("other",)


def test_catalog_rejects_unknown_provider(tmp_path):
    storage = Storage(tmp_path, FakeKeyring())
    with pytest.raises(ConfigurationError):
        storage.save_model_catalog("nope", ("auto",))


def test_corrupt_catalog_falls_back_to_empty(tmp_path):
    storage = Storage(tmp_path, FakeKeyring())
    storage.models_path.write_text("{not json", encoding="utf-8")
    assert storage.load_model_catalog("bailian") == ()
    storage.models_path.write_text(json.dumps({"schema_version": 999, "providers": {}}), encoding="utf-8")
    assert storage.load_model_catalog("bailian") == ()


def test_catalog_load_filters_unsafe_ids(tmp_path):
    storage = Storage(tmp_path, FakeKeyring())
    storage.models_path.write_text(json.dumps({
        "schema_version": 1,
        "providers": {"bailian": {"ids": ["ok-model", "bad id", "", 42, "ok-model"]}},
    }), encoding="utf-8")
    assert storage.load_model_catalog("bailian") == ("ok-model",)


# --- Seed integration -------------------------------------------------------

def test_fetched_model_lands_in_popup_not_custom_field():
    spec = get_provider("bailian")
    fetched = ("auto", "qwen4-max")
    options = merge_model_options(spec.claude_models, fetched)
    # A live config pointing at a newly fetched model stays in the dropdown.
    seed = resolve_claude_seed(spec, None, None, "", "qwen4-max", "", model_options=options)
    assert seed["claude_model"] == "qwen4-max"
    assert seed["claude_custom"] == ""
    # Without the merged options the same id is demoted to the custom field.
    seed_static = resolve_claude_seed(spec, None, None, "", "qwen4-max", "")
    assert seed_static["claude_model"] == ""
    assert seed_static["claude_custom"] == "qwen4-max"


@pytest.mark.skipif(__import__("sys").platform != "darwin", reason="macOS settings actions")
def test_open_switch_and_cancel_send_no_custom_catalog_requests():
    from unittest.mock import Mock
    from token_plan_monitor.macos.ui import MonitorUI

    ui = Mock()
    ui._catalog_inflight = set()
    ui.settings_provider = "bailian"
    ui._catalog_fetch_target.return_value = ("https://proxy.example:8443/apps/anthropic", "test-key")
    for provider in ("bailian", "volcengine"):
        MonitorUI._maybe_auto_fetch(ui, provider)
    ui.confirm_catalog_origin.return_value = False
    MonitorUI.refresh_models(ui)
    ui.confirm_catalog_origin.assert_called_once_with("bailian", "https://proxy.example:8443")
    ui._start_model_fetch.assert_not_called()
    ui.confirm_catalog_origin.return_value = True
    MonitorUI.refresh_models(ui)
    assert ui._start_model_fetch.call_count == 1

    ui._start_model_fetch.reset_mock()
    official = get_provider("bailian").default_claude_base_url("cn-beijing")
    ui._catalog_fetch_target.return_value = (official, "test-key")
    MonitorUI._maybe_auto_fetch(ui, "volcengine")
    ui._start_model_fetch.assert_not_called()
    MonitorUI._maybe_auto_fetch(ui, "bailian")
    ui._start_model_fetch.assert_called_once_with("bailian", official, "test-key")
