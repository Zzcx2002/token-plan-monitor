import json
import os
import stat
import sys
from pathlib import Path

import pytest

from token_plan_monitor import claude_code as cc
from token_plan_monitor.claude_code import ClaudeConfigError
from token_plan_monitor.models import CacheError, ConfigurationError

OFFICIAL = "https://token-plan.cn-beijing.maas.aliyuncs.com/apps/anthropic"

#: Mirrors a real, hand-tuned settings.json: alias-driven top-level model,
#: a per-tier model split, a non-official proxy host and unrelated extra keys.
LIVE_DOCUMENT = {
    "env": {
        "ANTHROPIC_AUTH_TOKEN": "sk-sp-OLD",
        "ANTHROPIC_BASE_URL": "https://token-plan.maas.qianwenaiapi.com/apps/anthropic",
        "ANTHROPIC_MODEL": "auto",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "qwen3.6-flash",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "qwen3.8-flash",
        "ANTHROPIC_DEFAULT_OPUS_MODEL": "qwen3.8-max",
        "CLAUDE_CODE_SUBAGENT_MODEL": "auto",
        "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "983616",
    },
    "permissions": {"deny": ["WebSearch"]},
    "model": "opus",
}


def make_request(**overrides):
    values = {
        "provider": "bailian",
        "base_url": OFFICIAL,
        "model": "qwen3.8-max",
        "api_key": "sk-sp-NEW",
    }
    values.update(overrides)
    return cc.ClaudeApplyRequest(**values)


def patch_for(document=LIVE_DOCUMENT, **overrides):
    return cc.compute_env_patch(make_request(**overrides), document)


# ----------------------------------------------------------------------
# Base URL normalization
# ----------------------------------------------------------------------


def test_base_url_passes_through_anthropic_endpoints_including_proxy_hosts():
    assert cc.normalize_base_url(OFFICIAL + "/") == (OFFICIAL, None)
    # The user's own non-official proxy host must survive untouched.
    proxy = "https://token-plan.maas.qianwenaiapi.com/apps/anthropic"
    assert cc.normalize_base_url(proxy) == (proxy, None)


def test_base_url_rewrites_openai_compatible_mode():
    url, rewritten_from = cc.normalize_base_url(
        "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1")
    assert url == OFFICIAL
    assert rewritten_from.endswith("/compatible-mode/v1")


def test_base_url_appends_anthropic_path_to_recognized_root_hosts():
    assert cc.normalize_base_url("https://token-plan.cn-beijing.maas.aliyuncs.com") == (
        OFFICIAL, "https://token-plan.cn-beijing.maas.aliyuncs.com")
    assert cc.normalize_base_url("https://dashscope.aliyuncs.com")[0].endswith("/apps/anthropic")


@pytest.mark.parametrize("url", [
    # The reproduced attack: reads as a Bailian template instance, but urlsplit
    # makes everything before the @ userinfo, so the real host is evil.example.com.
    "https://cn-beijing@evil.example.com/.maas.aliyuncs.com/apps/anthropic",
    "https://user:pass@token-plan.cn-beijing.maas.aliyuncs.com/apps/anthropic",
    "https://@evil.example.com/apps/anthropic",            # empty userinfo still moves the host
])
def test_base_url_rejects_credentials_because_they_disguise_the_host(url):
    """A path that already contains /apps/anthropic used to be returned untouched,
    so a credential-bearing URL reached Claude Code with the key addressed to
    whatever host followed the @."""
    with pytest.raises(ConfigurationError):
        cc.normalize_base_url(url)


def test_base_url_still_accepts_an_arbitrary_proxy_host():
    """The proxy-host contract must survive the userinfo rejection: hosts are
    free to be non-official, only the @ section is not."""
    proxy = "https://my-proxy.example.com/apps/anthropic"
    assert cc.normalize_base_url(proxy) == (proxy, None)


@pytest.mark.parametrize("url", [
    "https://ark.cn-beijing.volces.com/api/v3",   # documented, but not Anthropic
    "https://example.com/v1",
    "not a url",
    "",
])
def test_base_url_rejects_endpoints_claude_cannot_use(url):
    with pytest.raises(ConfigurationError):
        cc.normalize_base_url(url)


def test_base_url_requires_https_except_loopback():
    with pytest.raises(ConfigurationError):
        cc.normalize_base_url("http://evil/apps/anthropic")
    assert cc.normalize_base_url("http://localhost:8880/apps/anthropic")[0].startswith("http://localhost")


# ----------------------------------------------------------------------
# Tier policy
# ----------------------------------------------------------------------


def test_resolve_tier_key_maps_only_aliases():
    assert cc.resolve_tier_key("opus") == "ANTHROPIC_DEFAULT_OPUS_MODEL"
    assert cc.resolve_tier_key(" Sonnet ") == "ANTHROPIC_DEFAULT_SONNET_MODEL"
    assert cc.resolve_tier_key("qwen3.8-max") is None
    assert cc.resolve_tier_key(None) is None


def test_patch_writes_connection_trio_and_the_alias_tier_only():
    patch = patch_for()
    assert patch.set[cc.ENV_BASE_URL] == OFFICIAL
    assert patch.set[cc.ENV_AUTH_TOKEN] == "sk-sp-NEW"
    assert patch.set[cc.ENV_MODEL] == "qwen3.8-max"
    # model:"opus" resolves through the opus tier, so that tier must change...
    assert patch.set["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "qwen3.8-max"
    assert patch.tier_for_alias == "ANTHROPIC_DEFAULT_OPUS_MODEL"
    # ...while the hand-tuned flash split stays untouched.
    assert "ANTHROPIC_DEFAULT_SONNET_MODEL" not in patch.set
    assert "ANTHROPIC_DEFAULT_HAIKU_MODEL" not in patch.set
    assert patch.add_if_absent == {}
    assert patch.top_level_model == "qwen3.8-max"


def test_patch_removes_shadowing_key_only_when_present():
    assert patch_for().remove == ()
    with_key = dict(LIVE_DOCUMENT, env={**LIVE_DOCUMENT["env"], "ANTHROPIC_API_KEY": "sk-x"})
    assert patch_for(with_key).remove == ("ANTHROPIC_API_KEY",)


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_patch_fills_absent_tiers_with_the_chosen_model(blank):
    env = {"ANTHROPIC_BASE_URL": OFFICIAL}
    for key in ("ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
                "ANTHROPIC_DEFAULT_OPUS_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL"):
        if blank is not None:
            env[key] = blank
    patch = patch_for({"env": env, "model": "opus"})
    # alias tier is a hard set; the other three are add-if-absent
    assert patch.set["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "qwen3.8-max"
    assert set(patch.add_if_absent) == {
        "ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
        cc.ENV_SUBAGENT}


def test_patch_with_no_top_level_model_leaves_every_tier_to_add_if_absent():
    patch = patch_for({"env": {}})
    assert patch.tier_for_alias is None
    assert set(patch.set) == {cc.ENV_BASE_URL, cc.ENV_AUTH_TOKEN, cc.ENV_MODEL}
    assert set(patch.add_if_absent) == set(cc.ALL_TIER_KEYS)


# ----------------------------------------------------------------------
# Merge
# ----------------------------------------------------------------------


def test_merge_preserves_unknown_keys_and_does_not_mutate_input():
    patch = patch_for()
    merged = cc.merge_settings(LIVE_DOCUMENT, patch)
    assert merged is not LIVE_DOCUMENT
    assert LIVE_DOCUMENT["env"]["ANTHROPIC_AUTH_TOKEN"] == "sk-sp-OLD", "input mutated"
    assert merged["permissions"] == {"deny": ["WebSearch"]}
    assert merged["env"]["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == "983616"
    assert merged["env"]["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "qwen3.8-flash"
    assert merged["env"]["ANTHROPIC_AUTH_TOKEN"] == "sk-sp-NEW"
    assert merged["model"] == "qwen3.8-max"
    assert set(merged) == set(LIVE_DOCUMENT)


def test_merge_creates_env_and_drops_the_shadowing_key():
    patch = patch_for({"env": {"ANTHROPIC_API_KEY": "stale"}})
    merged = cc.merge_settings({}, patch)
    assert "ANTHROPIC_API_KEY" not in merged["env"]
    assert merged["env"][cc.ENV_MODEL] == "qwen3.8-max"


def test_merge_survives_a_non_object_env_for_pure_computation():
    merged = cc.merge_settings({"env": "oops"}, patch_for({"env": "oops"}))
    assert isinstance(merged["env"], dict)


# ----------------------------------------------------------------------
# JSONC reading and path resolution
# ----------------------------------------------------------------------


def test_strip_jsonc_keeps_comment_lookalikes_inside_strings():
    text = '{"url": "https://a/b//c", /* note */ "model": "opus",}'
    stripped = cc.strip_jsonc(text)
    parsed = json.loads(stripped)
    assert parsed == {"url": "https://a/b//c", "model": "opus"}


def test_parse_settings_document_rejects_broken_and_non_object_content():
    for broken in ("{", "[1,2]", '"just a string"', ""):
        with pytest.raises(ClaudeConfigError):
            cc.parse_settings_document(broken)


def test_resolve_settings_path_prefers_claude_config_dir():
    home = Path("/home/u")
    assert cc.resolve_settings_path({}, home) == home / ".claude/settings.json"
    assert cc.resolve_settings_path({"CLAUDE_CONFIG_DIR": "/tmp/cc"}, home) == Path("/tmp/cc/settings.json")
    assert cc.resolve_settings_path({"CLAUDE_CONFIG_DIR": "  "}, home) == home / ".claude/settings.json"


def test_onboarding_path_ignores_config_dir():
    home = Path("/home/u")
    assert cc.resolve_onboarding_path({"CLAUDE_CONFIG_DIR": "/tmp/cc"}, home) == home / ".claude.json"


def test_describe_changes_lists_target_url_tier_and_destructive_items():
    stale = dict(LIVE_DOCUMENT, env={**LIVE_DOCUMENT["env"], "ANTHROPIC_API_KEY": "stale"})
    patch = patch_for(stale)
    lines = "\n".join(cc.describe_changes(make_request(), patch, stale,
                                         Path("/tmp/cc/settings.json"), True))
    assert "/tmp/cc/settings.json" in lines
    assert "token-plan.maas.qianwenaiapi.com" in lines      # shows the old value
    assert "ANTHROPIC_DEFAULT_OPUS_MODEL" in lines          # which tier changes and why
    assert "ANTHROPIC_DEFAULT_SONNET_MODEL" in lines        # and which stays
    assert "ANTHROPIC_API_KEY" in lines
    assert "注释" in lines
    assert "sk-sp-NEW" not in lines                          # never echo the secret


def test_describe_changes_warns_about_an_unverified_endpoint():
    patch = patch_for()
    request = make_request(base_url_verified=False)
    assert any("未经验证" in line for line in cc.describe_changes(
        request, patch, LIVE_DOCUMENT, Path("/x/settings.json"), False))


# ----------------------------------------------------------------------
# Filesystem behaviour
# ----------------------------------------------------------------------


def test_apply_request_creates_a_missing_file_with_owner_only_mode(tmp_path):
    path = tmp_path / "nested" / "settings.json"
    result = cc.apply_request(make_request(), path, clock=lambda: 1_700_000_000)
    assert result.created is True and result.backup_path is None
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["env"][cc.ENV_AUTH_TOKEN] == "sk-sp-NEW"
    assert document["model"] == "qwen3.8-max"
    if sys.platform != "win32":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_apply_request_backs_up_and_merges_a_live_hand_tuned_file(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(LIVE_DOCUMENT, indent=2), encoding="utf-8")
    before = path.read_bytes()
    result = cc.apply_request(make_request(), path, clock=lambda: 1_700_000_001)
    assert result.backup_path is not None
    assert result.backup_path.read_bytes() == before
    if sys.platform != "win32":
        assert stat.S_IMODE(result.backup_path.stat().st_mode) == 0o600
    merged = json.loads(path.read_text(encoding="utf-8"))
    assert merged["permissions"] == LIVE_DOCUMENT["permissions"]
    assert merged["env"]["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == "983616"
    assert merged["env"]["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "qwen3.8-flash"
    assert merged["env"]["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "qwen3.8-max"
    assert merged["env"]["ANTHROPIC_BASE_URL"] == OFFICIAL
    assert result.preserved_env_keys >= 2


def test_apply_request_tightens_permissions_on_a_world_readable_file(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{}", encoding="utf-8")
    path.chmod(0o644)
    cc.apply_request(make_request(), path, clock=lambda: 2)
    if sys.platform != "win32":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_backup_is_owner_only_even_when_the_live_file_is_world_readable(tmp_path):
    """The backup is a copy of the previous settings.json, so it carries the same
    plaintext token the live file is hardened to protect. write_bytes created it
    0644 under the process umask."""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(LIVE_DOCUMENT, indent=2), encoding="utf-8")
    path.chmod(0o644)
    result = cc.apply_request(make_request(), path, clock=lambda: 500)
    assert result.backup_path is not None
    assert b"sk-sp-OLD" in result.backup_path.read_bytes(), "the backup really holds a key"
    if sys.platform != "win32":
        assert stat.S_IMODE(result.backup_path.stat().st_mode) == 0o600


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_backup_is_private_before_it_ever_reaches_the_final_name(tmp_path, monkeypatch):
    """The write path itself, not just the end state.

    A later sweep would also fix a careless writer, so asserting the final mode
    alone cannot tell the two apart -- and a backup created 0644 then chmodded
    leaves a window in which the key is world-readable under its real name.
    Capturing the mode at rename time proves the artifact was private from the
    moment it first existed on disk.
    """
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(LIVE_DOCUMENT), encoding="utf-8")
    seen = {}
    real_replace = os.replace

    def spy(source, destination, **kwargs):
        seen["mode"] = stat.S_IMODE(os.stat(source).st_mode)
        return real_replace(source, destination, **kwargs)

    monkeypatch.setattr(cc.os, "replace", spy)
    monkeypatch.setattr(os, "umask", lambda _: 0o022)   # a permissive umask, as most Macs have
    result = cc.backup_file(path, 300)
    assert seen, "backup must stage through a temp name, not write the final path"
    assert seen["mode"] == 0o600, oct(seen["mode"])
    assert stat.S_IMODE(result.stat().st_mode) == 0o600


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_backup_failure_does_not_destroy_the_previous_backup(tmp_path):
    """The source is read before the target is opened.

    Opening with O_TRUNC first would truncate -- and then delete -- the rollback
    artifact that already existed, precisely when the operation is failing and
    the user still needs a previous copy.
    """
    path = tmp_path / "settings.json"
    path.write_text("{}", encoding="utf-8")
    # Same timestamp as the backup already on disk, so this call targets it:
    # open-before-read would truncate this file and then delete it on failure.
    keeper = tmp_path / "settings.json.bak.2"
    keeper.write_text('{"keeper": true}', encoding="utf-8")
    keeper.chmod(0o600)
    path.chmod(0o000)
    try:
        with pytest.raises(ClaudeConfigError):
            cc.backup_file(path, 2)
    finally:
        path.chmod(0o600)
    assert keeper.read_text(encoding="utf-8") == '{"keeper": true}'


def test_legacy_world_readable_backups_are_tightened(tmp_path):
    """A fix that only covers new backups would leave every copy written before
    it sitting world-readable with a key inside."""
    path = tmp_path / "settings.json"
    path.write_text("{}", encoding="utf-8")
    legacy = tmp_path / "settings.json.bak.111"
    legacy.write_text(json.dumps(LIVE_DOCUMENT), encoding="utf-8")
    legacy.chmod(0o644)
    # Not ours by naming (date-stamped, not epoch), and an unrelated file: both
    # must be left alone.
    foreign = tmp_path / "settings.json.bak.20261110-160522"
    foreign.write_text('{"not":"ours"}', encoding="utf-8")
    foreign.chmod(0o640)
    unrelated = tmp_path / "notes.txt"
    unrelated.write_text("x", encoding="utf-8")
    unrelated.chmod(0o644)
    cc.apply_request(make_request(), path, clock=lambda: 501)
    if sys.platform != "win32":
        assert stat.S_IMODE(legacy.stat().st_mode) == 0o600
        assert stat.S_IMODE(foreign.stat().st_mode) == 0o640
        assert stat.S_IMODE(unrelated.stat().st_mode) == 0o644


def test_same_second_reapply_replaces_the_backup_instead_of_failing(tmp_path):
    """Locks the O_TRUNC choice: a name collision overwrites (documented as
    "重复点击结果一致") rather than aborting a write that has not touched the
    live file yet."""
    path = tmp_path / "settings.json"
    cc.apply_request(make_request(), path, clock=lambda: 400)
    cc.apply_request(make_request(model="qwen3.6-flash"), path, clock=lambda: 400)
    backups = list(tmp_path.glob("settings.json.bak.*"))
    assert len(backups) == 1, [b.name for b in backups]
    assert json.loads(backups[0].read_text(encoding="utf-8"))["env"]["ANTHROPIC_MODEL"] == "qwen3.8-max"
    if sys.platform != "win32":
        assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600


def test_second_apply_produces_identical_output_and_a_fresh_backup(tmp_path):
    path = tmp_path / "settings.json"
    cc.apply_request(make_request(), path, clock=lambda: 100)
    first = path.read_bytes()
    cc.apply_request(make_request(), path, clock=lambda: 200)
    assert path.read_bytes() == first, "re-apply must be idempotent"
    assert (tmp_path / "settings.json.bak.200").is_file()


def test_malformed_settings_aborts_without_touching_the_file(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ClaudeConfigError):
        cc.apply_request(make_request(), path)
    assert path.read_text(encoding="utf-8") == "{ not json"
    assert not list(tmp_path.glob("*.bak.*")), "no backup should have been taken"


def test_non_object_env_aborts_before_any_write(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"env": "stringified"}), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ClaudeConfigError):
        cc.apply_request(make_request(), path)
    assert path.read_bytes() == before


def test_read_document_at_flags_comments(tmp_path):
    path = tmp_path / "settings.json"
    plain = tmp_path / "plain.json"
    path.write_text('{\n  // hi\n  "model": "opus"\n}', encoding="utf-8")
    plain.write_text('{"model": "opus"}', encoding="utf-8")
    assert cc.read_document_at(path) == ({"model": "opus"}, True)
    assert cc.read_document_at(plain) == ({"model": "opus"}, False)
    missing = tmp_path / "nope.json"
    assert cc.read_document_at(missing) == (None, False)


def test_onboarding_marker_merges_and_never_rebuilds_a_broken_file(tmp_path):
    good = tmp_path / ".claude.json"
    good.write_text(json.dumps({"projects": {"a": 1}}), encoding="utf-8")
    assert cc.mark_onboarding_completed(good) is True
    assert json.loads(good.read_text(encoding="utf-8")) == {
        "projects": {"a": 1}, "hasCompletedOnboarding": True}

    broken = tmp_path / "broken.json"
    broken.write_text("{ nope", encoding="utf-8")
    assert cc.mark_onboarding_completed(broken) is False
    assert broken.read_text(encoding="utf-8") == "{ nope"


def test_onboarding_marker_skips_when_the_directory_is_not_writable(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        assert cc.mark_onboarding_completed(locked / ".claude.json") is False
    finally:
        locked.chmod(0o700)


def test_write_failure_surfaces_as_cache_error(tmp_path):
    # A file where a directory must be: the write stage cannot proceed.
    blocker = tmp_path / "nested"
    blocker.write_text("not a directory", encoding="utf-8")
    with pytest.raises(CacheError):
        cc.write_document_atomic(blocker / "settings.json", {})
    # The failure is reported as CacheError, not as a second OSError from cleanup.
    assert blocker.read_text(encoding="utf-8") == "not a directory"


def test_apply_request_refuses_to_read_a_directory_as_a_file(tmp_path):
    blocker = tmp_path / "settings.json"
    blocker.mkdir()
    with pytest.raises(ClaudeConfigError):
        cc.apply_request(make_request(), blocker)


def test_request_repr_does_not_leak_the_inference_key():
    assert "sk-sp-NEW" not in repr(make_request())
