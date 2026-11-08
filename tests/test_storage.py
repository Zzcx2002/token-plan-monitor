import json
from datetime import UTC, datetime

from token_plan_monitor.models import (
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


def test_widget_preferences_round_trip_with_negative_coordinates(tmp_path):
    storage = Storage(tmp_path, FakeKeyring())
    preferences = WidgetPreferences(x=-1920, y=-240, visible=False, topmost=False)
    storage.save_widget_preferences(preferences)
    restarted = Storage(tmp_path, FakeKeyring())
    assert restarted.load_widget_preferences() == preferences


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
