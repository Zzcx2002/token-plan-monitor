import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest

from token_plan_monitor.macos.layout import (
    FieldValidationError,
    resolve_claude_seed,
    clamp_card,
    fit_card_size,
    lowest_windows,
    primary_action,
    resize_card_frame,
    validate_claude_configuration,
    validate_configuration,
)
from token_plan_monitor.models import (
    ClaudeProviderPrefs,
    ConfigurationError,
    Credentials,
    MonitorViewState,
    QuotaWindow,
    UsageSnapshot,
)
from token_plan_monitor.providers import get_provider
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


def test_claude_configuration_defaults_come_from_the_provider():
    request = validate_claude_configuration(
        "bailian", "cn-beijing", "qwen3.8-max", "", "", "", "sk-sp-held")
    assert request.base_url == "https://token-plan.cn-beijing.maas.aliyuncs.com/apps/anthropic"
    assert request.model == "qwen3.8-max"
    assert request.api_key == "sk-sp-held"          # blank field reuses the vault
    assert request.base_url_verified is True
    assert request.rewritten_from is None


def test_custom_model_id_overrides_the_popup_choice():
    request = validate_claude_configuration(
        "bailian", "cn-beijing", "auto", "qwen-newest", "", "", "key")
    assert request.model == "qwen-newest"


def test_claude_configuration_rewrites_a_compatible_mode_url():
    request = validate_claude_configuration(
        "bailian", "cn-beijing", "auto", "",
        "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1", "", "key")
    assert request.base_url.endswith("/apps/anthropic")
    assert request.rewritten_from.endswith("/compatible-mode/v1")


@pytest.mark.parametrize("field,args", [
    ("model", ("bailian", "cn-beijing", "", "", "", "", "key")),
    ("model", ("bailian", "cn-beijing", "", "has a space", "", "", "key")),
    ("base_url", ("bailian", "cn-beijing", "auto", "", "https://ark.cn-beijing.volces.com/api/v3", "", "key")),
    ("base_url", ("volcengine", "cn-beijing", "auto", "", "", "", "key")),
    ("api_key", ("bailian", "cn-beijing", "auto", "", "", "", "")),
    ("api_key", ("bailian", "cn-beijing", "auto", "", "", "", "key with space")),
    # Region is interpolated into the provider's URL template, so it must clear
    # the same rule the save path applies. The first row is the reproduced
    # attack: it used to render a URL whose real host was evil.example.com.
    ("region", ("bailian", "cn-beijing@evil.example.com/", "auto", "", "", "", "key")),
    ("region", ("bailian", "cn-shanghai", "auto", "", "", "", "key")),
    # Checked even when a Base URL is typed, so one field never depends on
    # whether another happens to be filled in.
    ("region", ("volcengine", "not a region", "auto", "",
                "https://proxy.example.com/apps/anthropic", "", "key")),
])
def test_claude_validation_identifies_field(field, args):
    with pytest.raises(FieldValidationError) as failure:
        validate_claude_configuration(*args)
    assert failure.value.field == field


def test_claude_validation_falls_back_to_the_provider_default_region():
    """An empty Region still yields the official address -- the same fallback the
    save path has always had, and the reason the rule cannot simply require a
    non-empty value."""
    request = validate_claude_configuration("bailian", "", "auto", "", "", "", "key")
    assert request.base_url == "https://token-plan.cn-beijing.maas.aliyuncs.com/apps/anthropic"


def test_claude_validation_accepts_every_supported_bailian_region():
    from urllib.parse import urlsplit
    for region in ("cn-beijing", "ap-southeast-1"):
        request = validate_claude_configuration("bailian", region, "auto", "", "", "", "key")
        host = urlsplit(request.base_url).hostname
        assert host == f"token-plan.{region}.maas.aliyuncs.com", host


def test_claude_request_repr_hides_the_inference_key():
    request = validate_claude_configuration(
        "bailian", "cn-beijing", "auto", "", "", "", "sk-sp-topsecret")
    assert "sk-sp-topsecret" not in repr(request)


def _prefs(model="", custom_model="", base_url=""):
    return ClaudeProviderPrefs(model=model, custom_model=custom_model, base_url=base_url)


def test_never_configured_provider_falls_back_to_the_live_config_not_blank():
    """Regression lock: switching providers used to wipe base URL / model."""
    seed = resolve_claude_seed(
        get_provider("bailian"), None, None,
        live_base_url="https://token-plan.maas.qianwenaiapi.com/apps/anthropic",
        live_model="auto")
    assert seed["claude_base_url"].endswith("qianwenaiapi.com/apps/anthropic")
    assert seed["claude_model"] == "auto"
    assert seed["claude_custom"] == ""


def test_seed_order_is_draft_then_live_then_prefs():
    draft = _prefs(model="qwen3.8-max", base_url="https://draft/apps/anthropic")
    prefs = _prefs(model="qwen3.6-flash", base_url="https://prefs/apps/anthropic")
    live = "https://live/apps/anthropic"

    # What is typed in this session wins over everything.
    seed = resolve_claude_seed(get_provider("bailian"), draft, prefs, live, "auto")
    assert (seed["claude_model"], seed["claude_base_url"]) == (
        "qwen3.8-max", "https://draft/apps/anthropic")

    # Without a draft the file on disk outranks the cache: a hand-edited
    # endpoint must never be silently reverted by the next apply.
    seed = resolve_claude_seed(get_provider("bailian"), None, prefs, live, "auto")
    assert (seed["claude_model"], seed["claude_base_url"]) == ("auto", live)

    # The cache only supplies what the live file does not carry.
    seed = resolve_claude_seed(get_provider("bailian"), None, prefs, "", "")
    assert (seed["claude_model"], seed["claude_base_url"]) == (
        "qwen3.6-flash", "https://prefs/apps/anthropic")


def test_seed_takes_model_and_custom_from_one_layer_without_mixing():
    draft = _prefs(custom_model="brand-new-model")
    prefs = _prefs(model="auto", base_url="https://prefs/apps/anthropic")
    seed = resolve_claude_seed(get_provider("bailian"), draft, prefs, "", "qwen3.7-max")
    assert seed["claude_custom"] == "brand-new-model"
    assert seed["claude_model"] == ""          # not "auto", not "qwen3.7-max"
    assert seed["claude_base_url"] == "https://prefs/apps/anthropic"


def test_seed_moves_an_uncatalogued_model_into_the_custom_field():
    seed = resolve_claude_seed(get_provider("bailian"), None,
                               _prefs(model="qwen3.8-max-preview"), "", "")
    assert seed["claude_model"] == ""
    assert seed["claude_custom"] == "qwen3.8-max-preview"


def test_ark_has_no_catalogue_so_every_model_is_custom():
    seed = resolve_claude_seed(get_provider("volcengine"), None,
                               _prefs(model="doubao-seed-2-1-pro"), "", "")
    assert seed["claude_model"] == ""
    assert seed["claude_custom"] == "doubao-seed-2-1-pro"


def test_empty_everywhere_uses_the_provider_default_and_no_url():
    assert resolve_claude_seed(get_provider("bailian"), None, None, "", "") == {
        "claude_model": "auto", "claude_custom": "", "claude_base_url": ""}
    assert resolve_claude_seed(get_provider("volcengine"), None, None, "", "") == {
        "claude_model": "", "claude_custom": "", "claude_base_url": ""}
