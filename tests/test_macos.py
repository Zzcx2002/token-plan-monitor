import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest

from token_plan_monitor.macos.layout import (
    FieldValidationError,
    clamp_card,
    fit_card_size,
    lowest_windows,
    primary_action,
    resize_card_frame,
    validate_configuration,
)
from token_plan_monitor.models import ConfigurationError, Credentials, MonitorViewState, QuotaWindow, UsageSnapshot
from token_plan_monitor.monitor import UsageMonitor
from token_plan_monitor.paths import PROJECT_ROOT, data_directory, log_directory
from token_plan_monitor.storage import Storage


def test_platform_paths(tmp_path):
    assert data_directory("darwin", tmp_path) == tmp_path / "Library/Application Support/TokenPlanMonitor"
    assert log_directory("darwin", tmp_path) == tmp_path / "Library/Logs/TokenPlanMonitor"
    assert data_directory("win32", tmp_path) == PROJECT_ROOT / "data"
    assert log_directory("win32", tmp_path) == PROJECT_ROOT / "logs"


def test_entrypoint_does_not_import_windows_ui():
    result = subprocess.run([sys.executable, "-c", "import sys; import token_plan_monitor.__main__; assert 'token_plan_monitor.ui' not in sys.modules; assert 'token_plan_monitor.desktop_widget' not in sys.modules"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_settings_keep_secret_and_validate_limits():
    old = Credentials("old", "secret")
    settings, credentials = validate_configuration("volcengine", "new", "", "cn-beijing", "15", old)
    assert credentials == Credentials("new", "secret")
    assert settings.refresh_minutes == 15
    assert settings.provider == "volcengine"
    for interval in ("4", "1441", "1.5", "abc"):
        with pytest.raises(ConfigurationError):
            validate_configuration("volcengine", "ak", "sk", "cn-beijing", interval, None)
    with pytest.raises(ConfigurationError):
        validate_configuration("volcengine", "ak", "", "cn-beijing", "15", None)


@pytest.mark.parametrize("field,values", [
    ("ak", ("volcengine", "", "secret", "cn-beijing", "15")),
    ("sk", ("volcengine", "ak", "", "cn-beijing", "15")),
    ("region", ("volcengine", "ak", "secret", "not a region", "15")),
    ("interval", ("volcengine", "ak", "secret", "cn-beijing", "abc")),
    ("interval", ("volcengine", "ak", "secret", "cn-beijing", "4")),
    ("ak", ("bailian", "", "secret", "cn-beijing", "15")),
    ("region", ("bailian", "ak", "secret", "cn-shanghai", "15")),
])
def test_settings_validation_identifies_field(field, values):
    with pytest.raises(FieldValidationError) as failure:
        validate_configuration(*values, None)
    assert failure.value.field == field


def test_bailian_settings_default_region_and_reject_unsupported_one():
    settings, _ = validate_configuration("bailian", "LTAI-test", "secret", "", "15", None)
    assert settings.provider == "bailian"
    assert settings.region == "cn-beijing"
    with pytest.raises(FieldValidationError) as failure:
        validate_configuration("bailian", "LTAI-test", "secret", "cn-hangzhou", "15", None)
    assert failure.value.field == "region"


def test_unknown_provider_is_a_configuration_error():
    with pytest.raises(ConfigurationError):
        validate_configuration("openai", "ak", "sk", "cn-beijing", "15", None)


@pytest.mark.parametrize("status,category,expected", [
    ("unconfigured", None, ("连接账号", "check-credentials")),
    ("error", "authentication", ("检查凭据", "check-credentials")),
    ("stale", "authentication", ("检查凭据", "check-credentials")),
    ("error", "configuration", ("检查凭据", "check-credentials")),
    ("stale", "network", ("重试", "refresh")),
    ("error", "transient_provider", ("重试", "refresh")),
    ("error", None, ("重试", "refresh")),
    ("ok", None, ("立即刷新", "refresh")),
])
def test_recovery_action_uses_category(status, category, expected):
    state = MonitorViewState(status=status, error_category=category, error="unrelated localized text")
    assert primary_action(state) == expected


def test_lowest_quota_handles_missing_and_tied_windows():
    assert lowest_windows(MonitorViewState()) == frozenset()
    def state(windows):
        return MonitorViewState(snapshot=UsageSnapshot("agent_plan", datetime.now(UTC), tuple(windows)))
    assert lowest_windows(state([QuotaWindow("weekly", 40)])) == {"weekly"}
    assert lowest_windows(state([QuotaWindow("five_hour", 50), QuotaWindow("weekly", 50)])) == {"five_hour", "weekly"}
    assert lowest_windows(state([QuotaWindow("five_hour", 0), QuotaWindow("weekly", 100)])) == {"weekly"}


def test_card_positions_on_negative_displays_and_removed_screens():
    screens = [(0, 30, 1440, 870), (-1920, -240, 1920, 1080)]
    assert clamp_card(-1800, -200, 292, 128, screens) == (-1800, -200)
    assert clamp_card(-1800, -200, 292, 128, screens[:1]) == (0, 30)
    assert clamp_card(None, None, 292, 128, screens) == (1124, 748)
    assert clamp_card(10000, 10000, 292, 128, screens) == (1148, 772)


def test_card_resize_anchors_opposite_edges():
    frame = (100, 200, 384, 160)
    assert resize_card_frame(frame, 40, 20, frozenset({"right", "top"})) == (100, 200, 424, 180)
    assert resize_card_frame(frame, 40, -20, frozenset({"left", "bottom"})) == (140, 180, 344, 180)


def test_card_resize_enforces_minimum_without_moving_anchored_edges():
    frame = (100, 200, 384, 160)
    assert resize_card_frame(frame, 1000, 1000, frozenset({"left", "bottom"})) == (164, 200, 320, 160)
    assert resize_card_frame(frame, -1000, -1000, frozenset({"right", "top"})) == (100, 200, 320, 160)


def test_card_resize_and_restore_respect_screen_bounds():
    frame = (-300, -100, 384, 160)
    screen = (-400, -200, 600, 500)
    assert resize_card_frame(frame, 1000, 1000, frozenset({"right", "top"}), screen) == (-300, -100, 500, 400)
    assert fit_card_size(-300, -100, 1000, 1000, [screen]) == (600, 500)
    assert fit_card_size(-300, -100, 100, 100, [screen]) == (320, 160)
    assert fit_card_size(0, 0, 100, 100, [(0, 0, 200, 100)]) == (320, 160)


def test_keychain_denied_does_not_fall_back_to_files(tmp_path):
    class Denied:
        def get_password(self, *args):
            raise RuntimeError("private backend detail")
        def set_password(self, *args):
            raise RuntimeError("private backend detail")
    storage = Storage(tmp_path, Denied())
    with pytest.raises(ConfigurationError, match="无法读取"):
        storage.load_credentials()
    with pytest.raises(ConfigurationError, match="无法保存"):
        storage.save_credentials(Credentials("ak", "sk"))
    assert list(tmp_path.iterdir()) == []


def test_wake_refresh_only_when_due_and_coalesces(tmp_path):
    now = datetime(2026, 11, 8, tzinfo=UTC)
    monitor = UsageMonitor(Storage(tmp_path, object()), clock=lambda: now)
    monitor._credentials = Credentials("ak", "sk")
    monitor._state = MonitorViewState(last_attempt_at=now - timedelta(minutes=1))
    monitor.refresh_if_due()
    assert not monitor._pending
    monitor._state = MonitorViewState(last_attempt_at=now - timedelta(hours=2))
    monitor._in_flight = True
    monitor.refresh_if_due()
    assert not monitor._pending
    monitor._in_flight = False
    monitor.refresh_if_due()
    assert monitor._pending
    monitor.refresh_if_due()
    assert monitor._pending


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS flock")
def test_lock_survives_duplicate_release_and_recovers_after_crash(tmp_path):
    from token_plan_monitor.instance import SingleInstance
    code = "from pathlib import Path; from token_plan_monitor.instance import SingleInstance; import sys, time; lock=SingleInstance('crash',lock_dir=Path(sys.argv[1])); assert lock.acquire(); print('ready',flush=True); time.sleep(30)"
    child = subprocess.Popen([sys.executable, "-c", code, str(tmp_path)], stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "ready"
        duplicate = SingleInstance("crash", lock_dir=tmp_path)
        assert not duplicate.acquire()
        duplicate.release()
        assert not duplicate.acquire()
    finally:
        child.kill()
        child.wait(timeout=5)
    with_lock = SingleInstance("crash", lock_dir=tmp_path)
    try:
        assert with_lock.acquire()
        assert with_lock.acquire()
    finally:
        with_lock.release()
