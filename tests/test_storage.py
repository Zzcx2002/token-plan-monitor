import json
from datetime import UTC, datetime, timedelta

import pytest

from token_plan_monitor.history import HISTORY_MAX_ROWS, HistoryEntry, account_fingerprint
from token_plan_monitor.models import (
    DEFAULT_PROVIDER,
    AlertSettings,
    CacheError,
    ClaudeProviderPrefs,
    ConfigurationError,
    Credentials,
    QuotaWindow,
    Settings,
    UsageSnapshot,
    WidgetPreferences,
)
from token_plan_monitor.providers import get_provider
from token_plan_monitor.storage import KEYRING_SERVICE, Storage

# Vault entry names of the default provider, derived here rather than exported
# from storage: production code addresses them via the provider spec only.
_DEFAULT_SPEC = get_provider(DEFAULT_PROVIDER)
AK_USERNAME = _DEFAULT_SPEC.access_key_username
SK_USERNAME = _DEFAULT_SPEC.secret_key_username


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


def test_legacy_config_without_alert_key_keeps_existing_settings(tmp_path):
    # A config.json written by v0.1.x has no "alert" key. Loading it must keep
    # every existing choice and simply default the alert section -- never fall
    # into the schema-incompatible reset path.
    storage = Storage(tmp_path, FakeKeyring())
    storage.config_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "settings": {
                    "provider": "bailian",
                    "region": "cn-beijing",
                    "refresh_minutes": 30,
                },
            }
        ),
        encoding="utf-8",
    )
    loaded = storage.load_settings()
    assert loaded.provider == "bailian"
    assert loaded.region == "cn-beijing"
    assert loaded.refresh_minutes == 30
    assert loaded.alert == AlertSettings()


def test_alert_settings_round_trip(tmp_path):
    storage = Storage(tmp_path, FakeKeyring())
    settings = Settings(
        alert=AlertSettings(enabled=False, threshold_percent=25, notify_on_reset=True)
    )
    storage.save_settings(settings)
    assert storage.load_settings() == settings


def test_malformed_alert_section_falls_back_field_by_field(tmp_path):
    storage = Storage(tmp_path, FakeKeyring())
    storage.config_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "settings": {
                    "provider": "bailian",
                    "region": "cn-beijing",
                    "refresh_minutes": 30,
                    "alert": {
                        "enabled": False,
                        "threshold_percent": 250,
                        "notify_on_reset": "yes",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    loaded = storage.load_settings()
    # The surrounding settings survive; only the bad alert fields default.
    assert loaded.provider == "bailian"
    assert loaded.refresh_minutes == 30
    assert loaded.alert == AlertSettings(enabled=False)
    # A wholly non-object alert section defaults without touching anything else.
    storage.config_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "settings": {"refresh_minutes": 30, "alert": [1, 2]},
            }
        ),
        encoding="utf-8",
    )
    loaded = storage.load_settings()
    assert loaded.refresh_minutes == 30
    assert loaded.alert == AlertSettings()


def test_one_bad_field_defaults_alone_and_keeps_the_rest(tmp_path):
    # Review P0-5: a single invalid field used to reset provider, region,
    # refresh and alerts together, silently. Each bad field now defaults on
    # its own while every good field survives.
    storage = Storage(tmp_path, FakeKeyring())
    good = {"provider": "bailian", "region": "cn-beijing", "refresh_minutes": 30}

    def load_with(**overrides):
        storage.config_path.write_text(
            json.dumps({"schema_version": 1, "settings": {**good, **overrides}}),
            encoding="utf-8",
        )
        return storage.load_settings()

    loaded = load_with(refresh_minutes="15")
    assert loaded.provider == "bailian"
    assert loaded.region == "cn-beijing"
    assert loaded.refresh_minutes == 15
    assert load_with(refresh_minutes=1).refresh_minutes == 15
    assert load_with(refresh_minutes=True).refresh_minutes == 15

    loaded = load_with(region="cn-beijing@evil.example.com/")
    assert loaded.region == "cn-beijing"
    assert loaded.provider == "bailian"
    assert loaded.refresh_minutes == 30
    # Well-formed but outside bailian's region allowlist.
    assert load_with(region="cn-shanghai").region == "cn-beijing"

    loaded = load_with(provider="openai")
    assert loaded.provider == "volcengine"
    assert loaded.refresh_minutes == 30

    # A wholly non-object settings section still defaults the document.
    storage.config_path.write_text(
        json.dumps({"schema_version": 1, "settings": "bad"}), encoding="utf-8"
    )
    assert storage.load_settings() == Settings()


def test_alert_settings_constructor_rejects_invalid_values():
    with pytest.raises(ConfigurationError):
        AlertSettings(threshold_percent=0)
    with pytest.raises(ConfigurationError):
        AlertSettings(threshold_percent=100)
    with pytest.raises(ConfigurationError):
        AlertSettings(threshold_percent=True)
    with pytest.raises(ConfigurationError):
        AlertSettings(enabled="yes")
    with pytest.raises(ConfigurationError):
        Settings(alert={"threshold_percent": 20})
    # from_dict is the tolerant path: it never raises, whatever it is fed.
    assert AlertSettings.from_dict(None) == AlertSettings()
    assert AlertSettings.from_dict("bad") == AlertSettings()
    assert AlertSettings.from_dict({"threshold_percent": "20"}) == AlertSettings()


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
        ClaudeProviderPrefs(model="qwen3.8-max", base_url="https://x/apps/anthropic",
                            subagent_model="qwen3.6-flash"), "bailian")
    assert storage.load_claude_prefs("bailian").model == "qwen3.8-max"
    assert storage.load_claude_prefs("bailian").subagent_model == "qwen3.6-flash"
    assert storage.load_claude_prefs("volcengine") == ClaudeProviderPrefs()
    storage.save_claude_prefs(ClaudeProviderPrefs(custom_model="ep-abc"), "volcengine")
    assert storage.load_claude_prefs("bailian").base_url == "https://x/apps/anthropic"
    assert storage.load_claude_prefs("bailian").subagent_model == "qwen3.6-flash"
    assert storage.load_claude_prefs("volcengine").custom_model == "ep-abc"
    assert storage.load_claude_prefs("volcengine").subagent_model == ""


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


def history_entry(minutes=0, percent=25.0, account="a1b2c3d4e5f6"):
    return HistoryEntry(
        queried_at=datetime(2026, 11, 16, 8, tzinfo=UTC) + timedelta(minutes=minutes),
        account=account,
        plan_type="agent_plan",
        windows=(QuotaWindow(kind="five_hour", used_percent=percent),),
    )


def test_history_append_and_load_round_trip(tmp_path):
    storage = Storage(tmp_path, FakeKeyring())
    entry = history_entry()
    storage.append_history(entry, "volcengine")
    assert storage.history_path("volcengine") == tmp_path / "history" / "volcengine.jsonl"
    assert storage.load_history("volcengine") == [entry]
    assert storage.load_history("bailian") == []
    with pytest.raises(ConfigurationError):
        storage.append_history(entry, "openai")
    with pytest.raises(ConfigurationError):
        storage.load_history("openai")


def test_history_dedupes_unchanged_values_but_records_changes(tmp_path):
    storage = Storage(tmp_path, FakeKeyring())
    storage.append_history(history_entry(), "volcengine")
    storage.append_history(history_entry(minutes=5), "volcengine")  # identical, within gap
    assert len(storage.load_history("volcengine")) == 1
    storage.append_history(history_entry(minutes=6, percent=26.0), "volcengine")
    assert len(storage.load_history("volcengine")) == 2
    storage.append_history(history_entry(minutes=30, percent=26.0), "volcengine")
    assert len(storage.load_history("volcengine")) == 3
    # A different account's identical values never suppress this account's row.
    storage.append_history(history_entry(minutes=31, account="ffffffffffff"), "volcengine")
    assert len(storage.load_history("volcengine")) == 4


def test_history_repairs_a_torn_tail_and_skips_bad_rows(tmp_path):
    storage = Storage(tmp_path, FakeKeyring())
    path = storage.history_path("volcengine")
    path.parent.mkdir(parents=True)
    # A garbage row, then a crash that left half a row without a trailing newline.
    path.write_text('not json\n{"v":1,"t":"2026-11-16T07:00:00Z","accou', encoding="utf-8")
    entry = history_entry()
    storage.append_history(entry, "volcengine")
    assert storage.load_history("volcengine") == [entry]
    assert path.read_text(encoding="utf-8").endswith("\n")


def test_history_rotation_enforces_the_row_cap(tmp_path, monkeypatch):
    monkeypatch.setattr("token_plan_monitor.storage.HISTORY_MAX_ROWS", 3)
    storage = Storage(tmp_path, FakeKeyring())
    for index in range(6):
        storage.append_history(history_entry(minutes=index, percent=20.0 + index), "volcengine")
    kept = storage.load_history("volcengine")
    assert len(kept) == 3
    assert [entry.windows[0].used_percent for entry in kept] == [23.0, 24.0, 25.0]


def test_history_rotation_drops_rows_past_the_age_cap(tmp_path, monkeypatch):
    monkeypatch.setattr("token_plan_monitor.storage.HISTORY_MAX_ROWS", 3)
    storage = Storage(tmp_path, FakeKeyring())
    ancient = history_entry(minutes=-100 * 24 * 60)
    storage.append_history(ancient, "volcengine")
    for index in range(4):
        storage.append_history(history_entry(minutes=index, percent=20.0 + index), "volcengine")
    kept = storage.load_history("volcengine")
    assert ancient not in kept
    assert len(kept) == 3


def test_history_age_cap_fires_without_the_row_cap(tmp_path):
    # Review P2-13: low-frequency history never reached the 10k row cap, so
    # the 90-day prune never ran and stale rows survived forever. The age cap
    # must fire on every append, far below the row cap.
    storage = Storage(tmp_path, FakeKeyring())
    path = storage.history_path("volcengine")
    path.parent.mkdir(parents=True)
    stale = [
        history_entry(minutes=-days * 24 * 60, percent=20.0 + days % 7)
        for days in range(1000, 90, -1)
    ]
    recent = [history_entry(minutes=-days * 24 * 60, percent=30.0) for days in range(80, 0, -1)]
    storage._write_history(path, stale + recent)

    kept = storage.append_history(history_entry(), "volcengine")

    assert len(kept) == 81  # 80 recent + the new row; 910 stale rows dropped
    assert len(storage.load_history("volcengine", limit=HISTORY_MAX_ROWS)) == 81
    assert len(path.read_text(encoding="utf-8").splitlines()) == 81


def test_history_deduped_append_still_prunes_stale_rows(tmp_path):
    # Even a suppressed (unchanged-values) append must enforce the age cap.
    storage = Storage(tmp_path, FakeKeyring())
    path = storage.history_path("volcengine")
    path.parent.mkdir(parents=True)
    ancient = history_entry(minutes=-100 * 24 * 60)
    current = history_entry()
    storage._write_history(path, [ancient, current])

    kept = storage.append_history(history_entry(minutes=5), "volcengine")

    assert kept == [current]  # deduped, yet the 100-day-old row is gone
    assert storage.load_history("volcengine") == [current]


def test_history_append_does_not_rewrite_when_nothing_is_pruned(tmp_path):
    storage = Storage(tmp_path, FakeKeyring())
    storage.append_history(history_entry(), "volcengine")
    path = storage.history_path("volcengine")
    before = path.read_bytes()
    mtime = path.stat().st_mtime_ns
    # Deduped and nothing stale: the file must be left byte-for-byte alone.
    storage.append_history(history_entry(minutes=5), "volcengine")
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == mtime


def test_history_load_limit_defaults_below_the_retention_cap(tmp_path):
    # Pins the monitor's limit fix: a 90-day chart at the 15-minute cadence
    # needs ~8640 rows, so the monitor must ask for the full retention cap,
    # not the 2000-row default that would silently truncate long ranges.
    storage = Storage(tmp_path, FakeKeyring())
    rows = [
        history_entry(minutes=index, percent=20.0 + index % 50)
        for index in range(2001)
    ]
    path = storage.history_path("volcengine")
    path.parent.mkdir(parents=True)
    storage._write_history(path, rows)
    assert len(storage.load_history("volcengine")) == 2000
    full = storage.load_history("volcengine", limit=HISTORY_MAX_ROWS)
    assert len(full) == 2001
    assert full[0] == rows[0]


def test_history_is_isolated_per_provider_and_filterable_per_account(tmp_path):
    storage = Storage(tmp_path, FakeKeyring())
    mine = history_entry(account=account_fingerprint("AKLT-a"))
    other = history_entry(minutes=1, account=account_fingerprint("AKLT-b"))
    storage.append_history(mine, "volcengine")
    storage.append_history(other, "volcengine")
    storage.append_history(history_entry(minutes=2), "bailian")
    # Switching provider keeps the old file: history is an observation log.
    assert storage.history_path("volcengine").exists()
    assert storage.history_path("bailian").exists()
    assert storage.load_history("volcengine", account=mine.account) == [mine]
    assert storage.load_history("volcengine", account=other.account) == [other]
    assert storage.load_history("volcengine", limit=1) == [other]


def test_history_io_errors_surface_as_cache_error(tmp_path):
    storage = Storage(tmp_path, FakeKeyring())
    # A regular file where the history directory should be: mkdir fails.
    (tmp_path / "history").write_text("blocker", encoding="utf-8")
    with pytest.raises(CacheError):
        storage.append_history(history_entry(), "volcengine")
    with pytest.raises(CacheError):
        storage.load_history("volcengine")
