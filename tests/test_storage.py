import json
from datetime import UTC, datetime

import pytest

from token_plan_monitor.models import (
    ClaudeProviderPrefs,
    ConfigurationError,
    Credentials,
    QuotaWindow,
    Settings,
    UsageSnapshot,
    WidgetPreferences,
)
from token_plan_monitor.storage import (
    AK_USERNAME,
    KEYRING_SERVICE,
    SK_USERNAME,
    Storage,
)


class FakeKeyring:
    def __init__(self):
        self.values = {}

    def get_password(self, service, username):
        return self.values.get((service, username))

    def set_password(self, service, username, value):
        self.values[(service, username)] = value


def sample_snapshot():
    return UsageSnapshot(
        plan_type="agent_plan",
        plan_name="Agent Plan Large",
        queried_at=datetime(2026, 11, 2, 8, 30, tzinfo=UTC),
        windows=(
            QuotaWindow(
                kind="five_hour",
                used_percent=25,
                quota=50,
                used=12.5,
                reset_at=datetime(2026, 11, 2, 10, tzinfo=UTC),
            ),
        ),
    )


def test_settings_and_snapshot_round_trip(tmp_path):
    storage = Storage(tmp_path, FakeKeyring())
    settings = Settings(region="cn-shanghai", refresh_minutes=30)
    storage.save_settings(settings)
    storage.save_snapshot(sample_snapshot())
    assert storage.load_settings() == settings
    assert storage.load_snapshot() == sample_snapshot()


def test_corrupt_or_incompatible_json_falls_back_safely(tmp_path):
    storage = Storage(tmp_path, FakeKeyring())
    storage.config_path.write_text("not json", encoding="utf-8")
    storage.snapshot_path.write_text("[]", encoding="utf-8")
    assert storage.load_settings() == Settings()
    assert storage.load_snapshot() is None
    storage.config_path.write_text(
        json.dumps({"schema_version": 99, "settings": {}}), encoding="utf-8"
    )
    assert storage.load_settings() == Settings()
    storage.config_path.write_text(
        json.dumps({"schema_version": 1, "settings": {"region": []}}),
        encoding="utf-8",
    )
    assert storage.load_settings() == Settings()
    storage.snapshot_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "snapshot": {
                    "plan_type": "agent_plan",
                    "queried_at": None,
                    "windows": [],
                },
            }
        ),
        encoding="utf-8",
    )
    assert storage.load_snapshot() is None


def test_credentials_use_only_authorized_entries_and_never_enter_json(tmp_path):
    keyring = FakeKeyring()
    storage = Storage(tmp_path, keyring)
    credentials = Credentials("AKLT-test", "very-secret")
    storage.save_credentials(credentials)
    storage.save_settings(Settings())
    storage.save_snapshot(sample_snapshot())
    assert keyring.values == {
        (KEYRING_SERVICE, SK_USERNAME): "very-secret",
        (KEYRING_SERVICE, AK_USERNAME): "AKLT-test",
    }
    assert storage.load_credentials() == credentials
    serialized = storage.config_path.read_text(encoding="utf-8")
    serialized += storage.snapshot_path.read_text(encoding="utf-8")
    assert "very-secret" not in serialized
    assert "AKLT-test" not in serialized


@pytest.mark.parametrize("desktop_mode", [False, True])
def test_widget_preferences_round_trip_with_negative_coordinates(tmp_path, desktop_mode):
    storage = Storage(tmp_path, FakeKeyring())
    preferences = WidgetPreferences(
        x=-1920, y=-240, visible=False, topmost=False, desktop_mode=desktop_mode,
        width=520, height=240,
    )
    storage.save_widget_preferences(preferences)
    restarted = Storage(tmp_path, FakeKeyring())
    assert restarted.load_widget_preferences() == preferences


def test_legacy_widget_preferences_preserve_existing_flags_and_position(tmp_path):
    storage = Storage(tmp_path, FakeKeyring())
    storage.widget_path.write_text(json.dumps({
        "schema_version": 1,
        "widget": {"x": -1920, "y": -240, "visible": False, "topmost": False},
    }), encoding="utf-8")
    assert storage.load_widget_preferences() == WidgetPreferences(
        x=-1920, y=-240, visible=False, topmost=False, desktop_mode=False,
    )


@pytest.mark.parametrize("invalid", [None, 0, 1, "true", [], {}])
def test_invalid_desktop_mode_falls_back_to_defaults(tmp_path, invalid):
    storage = Storage(tmp_path, FakeKeyring())
    storage.widget_path.write_text(json.dumps({
        "schema_version": 1, "widget": {"desktop_mode": invalid},
    }), encoding="utf-8")
    assert storage.load_widget_preferences() == WidgetPreferences()


@pytest.mark.parametrize("field,invalid", [
    ("width", None), ("width", True), ("width", 384.5), ("width", "384"),
    ("width", 319), ("height", 159), ("height", -1), ("height", 10001),
])
def test_invalid_widget_dimensions_fall_back_to_defaults(tmp_path, field, invalid):
    storage = Storage(tmp_path, FakeKeyring())
    storage.widget_path.write_text(json.dumps({
        "schema_version": 1, "widget": {field: invalid},
    }), encoding="utf-8")
    assert storage.load_widget_preferences() == WidgetPreferences()


def test_corrupt_widget_preferences_fall_back_to_defaults(tmp_path):
    storage = Storage(tmp_path, FakeKeyring())
    storage.widget_path.write_text("not json", encoding="utf-8")
    assert storage.load_widget_preferences() == WidgetPreferences()
    storage.widget_path.write_text(
        json.dumps({"schema_version": 1, "widget": {"x": True, "y": 20}}),
        encoding="utf-8",
    )
    assert storage.load_widget_preferences() == WidgetPreferences()
    storage.widget_path.write_text(
        json.dumps({"schema_version": 99, "widget": {}}), encoding="utf-8"
    )
    assert storage.load_widget_preferences() == WidgetPreferences()


def test_clear_snapshot_is_idempotent(tmp_path):
    storage = Storage(tmp_path, FakeKeyring())
    storage.save_snapshot(sample_snapshot())
    storage.clear_snapshot()
    storage.clear_snapshot()
    assert storage.load_snapshot() is None


def test_provider_credentials_are_isolated_in_the_vault(tmp_path):
    keyring = FakeKeyring()
    storage = Storage(tmp_path, keyring)
    volcengine = Credentials("AKLT-volc", "volc-secret")
    bailian = Credentials("LTAI-bailian", "bailian-secret")
    storage.save_credentials(volcengine, "volcengine")
    storage.save_credentials(bailian, "bailian")
    # The default provider argument keeps reading the Volcengine entries.
    assert storage.load_credentials() == volcengine
    assert storage.load_credentials("volcengine") == volcengine
    assert storage.load_credentials("bailian") == bailian

    # Losing one provider's secret never leaks into the other provider's pair.
    keyring.values.pop((KEYRING_SERVICE, "volcengine_secret_access_key"))
    assert storage.load_credentials("volcengine") is None
    assert storage.load_credentials("bailian") == bailian


def test_provider_secrets_never_reach_the_json_files(tmp_path):
    storage = Storage(tmp_path, FakeKeyring())
    storage.save_credentials(Credentials("LTAI-bailian", "bailian-secret"), "bailian")
    storage.save_settings(Settings(provider="bailian", region="cn-beijing"))
    storage.save_snapshot(sample_snapshot())
    serialized = storage.config_path.read_text(encoding="utf-8")
    serialized += storage.snapshot_path.read_text(encoding="utf-8")
    assert "bailian-secret" not in serialized
    assert "LTAI-bailian" not in serialized


def test_inference_keys_are_isolated_per_provider(tmp_path):
    keyring = FakeKeyring()
    storage = Storage(tmp_path, keyring)
    storage.save_inference_key("sk-sp-bailian", "bailian")
    storage.save_credentials(Credentials("AKLT", "ak-secret"), "volcengine")
    assert storage.load_inference_key("bailian") == "sk-sp-bailian"
    assert storage.load_inference_key("volcengine") is None
    assert {name for _, name in keyring.values} == {
        "bailian_inference_api_key",
        "volcengine_access_key_id",
        "volcengine_secret_access_key",
    }
    with pytest.raises(ConfigurationError):
        storage.save_inference_key("   ", "bailian")


def test_inference_key_never_reaches_the_json_files(tmp_path):
    storage = Storage(tmp_path, FakeKeyring())
    storage.save_inference_key("sk-sp-secret", "bailian")
    storage.save_claude_prefs(
        ClaudeProviderPrefs(model="qwen3.8-max", base_url="https://x/apps/anthropic"), "bailian")
    storage.save_settings(Settings(provider="bailian"))
    storage.save_snapshot(sample_snapshot())
    serialized = "".join(
        path.read_text(encoding="utf-8")
        for path in (storage.config_path, storage.snapshot_path, storage.claude_path))
    assert "sk-sp-secret" not in serialized


def test_claude_prefs_round_trip_without_cross_provider_clobbering(tmp_path):
    storage = Storage(tmp_path, FakeKeyring())
    storage.save_claude_prefs(
        ClaudeProviderPrefs(model="qwen3.8-max", base_url="https://x/apps/anthropic"), "bailian")
    assert storage.load_claude_prefs("bailian").model == "qwen3.8-max"
    assert storage.load_claude_prefs("volcengine") == ClaudeProviderPrefs()
    storage.save_claude_prefs(ClaudeProviderPrefs(custom_model="ep-abc"), "volcengine")
    assert storage.load_claude_prefs("bailian").base_url == "https://x/apps/anthropic"
    assert storage.load_claude_prefs("volcengine").custom_model == "ep-abc"


@pytest.mark.parametrize("payload", [
    "{ not json",
    "[]",
    '{"schema_version": 99, "providers": {"bailian": {"model": "x"}}}',
    '{"schema_version": 1, "providers": []}',
    '{"schema_version": 1, "providers": {"bailian": {"model": 42}}}',
    '{"schema_version": 1, "providers": {"bailian": null}}',
])
def test_corrupt_claude_prefs_fall_back_to_defaults(tmp_path, payload):
    storage = Storage(tmp_path, FakeKeyring())
    storage.claude_path.write_text(payload, encoding="utf-8")
    assert storage.load_claude_prefs("bailian") == ClaudeProviderPrefs()


def test_saving_prefs_for_an_unknown_provider_is_rejected(tmp_path):
    storage = Storage(tmp_path, FakeKeyring())
    with pytest.raises(ConfigurationError):
        storage.save_claude_prefs(ClaudeProviderPrefs(), "openai")
