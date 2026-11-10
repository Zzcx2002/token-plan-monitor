import pytest

from token_plan_monitor.bailian import BailianClient
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
        name for spec in PROVIDERS for name in (spec.access_key_username, spec.secret_key_username)
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
