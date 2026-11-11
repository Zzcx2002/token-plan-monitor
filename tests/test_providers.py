import pytest

from token_plan_monitor.bailian import BailianClient
from token_plan_monitor.claude_code import normalize_base_url
from token_plan_monitor.models import (
    PROVIDER_IDS,
    ConfigurationError,
    Credentials,
    Settings,
)
from token_plan_monitor.providers import PROVIDERS, create_client, get_provider
from token_plan_monitor.volcengine import VolcengineClient


def test_registry_covers_every_provider_with_disjoint_vault_entries():
    assert tuple(spec.id for spec in PROVIDERS) == PROVIDER_IDS
    usernames = [
        name
        for spec in PROVIDERS
        for name in (spec.access_key_username, spec.secret_key_username,
                     spec.inference_key_username)
    ]
    assert len(usernames) == len(set(usernames)), "providers must not share vault entries"


def test_volcengine_keeps_its_original_vault_entry_names():
    spec = get_provider("volcengine")
    assert spec.access_key_username == "volcengine_access_key_id"
    assert spec.secret_key_username == "volcengine_secret_access_key"


def test_get_provider_rejects_unknown_ids():
    with pytest.raises(ConfigurationError):
        get_provider("openai")


def test_create_client_dispatches_on_the_settings_provider():
    credentials = Credentials("ak", "sk")
    assert isinstance(create_client(Settings(), credentials), VolcengineClient)
    assert isinstance(create_client(Settings(provider="bailian"), credentials), BailianClient)


def test_settings_enforce_each_provider_region_policy():
    assert Settings(region="cn-shanghai").region == "cn-shanghai"
    assert Settings(provider="bailian").region == "cn-beijing"
    with pytest.raises(ConfigurationError):
        Settings(provider="bailian", region="cn-shanghai")
    with pytest.raises(ConfigurationError):
        Settings(provider="nope")


def test_bailian_default_base_url_is_a_valid_anthropic_endpoint():
    spec = get_provider("bailian")
    assert spec.claude_base_url_verified is True
    assert spec.default_claude_base_url("cn-beijing") == (
        "https://token-plan.cn-beijing.maas.aliyuncs.com/apps/anthropic")
    assert spec.default_claude_base_url("ap-southeast-1").startswith(
        "https://token-plan.ap-southeast-1.")
    # An empty region must still resolve to the provider default region.
    assert normalize_base_url(spec.default_claude_base_url(""))[0] == (
        spec.default_claude_base_url("cn-beijing"))


def test_ark_never_prefills_an_unverified_endpoint_or_invented_models():
    spec = get_provider("volcengine")
    # Ark has no verified Anthropic endpoint or model catalogue, so the app must
    # leave both blank rather than write a URL known to be the wrong shape.
    assert spec.claude_base_url_verified is False
    assert spec.default_claude_base_url("cn-beijing") == ""
    assert spec.claude_models == ()
    assert spec.claude_default_model == ""


def test_bailian_catalog_models_are_valid_ids_and_include_the_default():
    spec = get_provider("bailian")
    identifiers = [option.id for option in spec.claude_models]
    assert spec.claude_default_model in identifiers
    assert len(identifiers) == len(set(identifiers))
    assert identifiers == [
        "auto", "qwen3.8-max", "qwen3.8-flash", "qwen3.7-max", "qwen3.7-plus",
        "qwen3.6-flash", "deepseek-v4-pro", "deepseek-v4.1-flash", "glm-5.3", "glm-5.2",
    ]
    assert all(option.label.startswith(option.id) for option in spec.claude_models)
    # Retired and non-chat models must not be offered to Claude Code.
    assert "qwen3.8-max-preview" not in identifiers
    assert not any(model.startswith(("wan", "qwen-image", "qwen-audio")) for model in identifiers)
