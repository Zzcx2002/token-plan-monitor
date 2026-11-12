"""Write provider + model choices into the local Claude Code configuration.

Protocol and merge semantics are adapted from the official Bailian CLI
(modelstudioai/cli, Apache-2.0): `commands/config/agent/writers/claude-code.ts`
and its `utils.ts`. This module deliberately imports nothing platform specific
and never resolves a home directory or reads the environment internally: the
caller passes explicit paths so every code path stays testable and no test can
touch a real `~/.claude`.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from .models import CacheError, ConfigurationError

ENV_BASE_URL = "ANTHROPIC_BASE_URL"
ENV_AUTH_TOKEN = "ANTHROPIC_AUTH_TOKEN"
ENV_MODEL = "ANTHROPIC_MODEL"
ENV_SUBAGENT = "CLAUDE_CODE_SUBAGENT_MODEL"
#: The model Claude Code's auto mode uses to judge whether a tool call is safe.
#: Optional: an empty selection never touches this key -- it is deliberately not
#: part of ALL_TIER_KEYS, so neither `set` nor `add_if_absent` can fill it.
ENV_AUTO_MODE = "CLAUDE_CODE_AUTO_MODE_MODEL"
#: A stale ANTHROPIC_API_KEY shadows ANTHROPIC_AUTH_TOKEN, so it must go.
ENV_SHADOWING_KEY = "ANTHROPIC_API_KEY"

#: Claude Code resolves these top-level `model` aliases through a tier variable.
TIER_KEYS_BY_ALIAS = {
    "opus": "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "sonnet": "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "haiku": "ANTHROPIC_DEFAULT_HAIKU_MODEL",
}
ALL_TIER_KEYS = (*TIER_KEYS_BY_ALIAS.values(), ENV_SUBAGENT)

ANTHROPIC_PATH = "/apps/anthropic"
#: Hosts whose root URL means "the Anthropic endpoint lives under /apps/anthropic".
_REWRITABLE_HOSTS = ("dashscope", "maas.aliyuncs.com", "token-plan")


class ClaudeConfigError(ConfigurationError):
    """A Claude configuration file could not be read or safely rewritten."""


@dataclass(frozen=True)
class ClaudeApplyRequest:
    provider: str
    base_url: str
    model: str
    api_key: str = field(repr=False)
    #: Set when normalize_base_url rewrote a compatible-mode URL.
    rewritten_from: str | None = None
    #: Provider default is an unverified Anthropic endpoint (Volcengine Ark).
    base_url_verified: bool = True
    #: Optional auto mode classifier model; "" means "do not touch the key".
    auto_mode_model: str = ""
    #: Set by the layout layer from the provider catalogue; only feeds warning
    #: copy -- a heavy classifier model can time out and block tool calls.
    auto_mode_model_lightweight: bool = True


@dataclass(frozen=True)
class EnvPatch:
    """The complete description of how a settings document will change."""

    set: Mapping[str, str]
    add_if_absent: Mapping[str, str]
    remove: tuple[str, ...]
    top_level_model: str
    #: Tier the current `model` alias resolves through, when there is one.
    tier_for_alias: str | None = None
    #: Selected auto mode classifier model, "" when the user chose nothing.
    auto_mode_model: str = ""

    @property
    def touched_env_keys(self) -> tuple[str, ...]:
        return tuple(self.set) + tuple(self.add_if_absent) + self.remove


@dataclass(frozen=True)
class ApplyResult:
    settings_path: Path
    backup_path: Path | None
    set_keys: tuple[str, ...]
    added_keys: tuple[str, ...]
    removed_keys: tuple[str, ...]
    preserved_env_keys: int
    created: bool


# ----------------------------------------------------------------------
# Pure helpers
# ----------------------------------------------------------------------


def is_absent(value: Any) -> bool:
    """Treat a missing or whitespace-only value as "the user has not set this"."""
    if value is None:
        return True
    return isinstance(value, str) and not value.strip()


def normalize_base_url(raw: str) -> tuple[str, str | None]:
    """Return (anthropic base URL, value it was rewritten from).

    Claude Code speaks Anthropic Messages only, so an OpenAI compatible-mode URL
    has to be rewritten rather than written out broken.
    """
    trimmed = (raw or "").strip().rstrip("/")
    if not trimmed:
        raise ConfigurationError("Base URL 不能为空")
    try:
        parsed = urlsplit(trimmed)
    except ValueError as exc:
        raise ConfigurationError("Base URL 格式无效") from exc

    if parsed.scheme != "https" and parsed.hostname not in ("localhost", "127.0.0.1"):
        raise ConfigurationError("Base URL 必须使用 https（本机调试可用 http://localhost）")
    if not parsed.netloc:
        raise ConfigurationError("Base URL 缺少主机名")
    if "@" in parsed.netloc:
        # Everything before the @ is userinfo, not the host. A URL like
        # https://token-plan.cn-beijing@evil.example.com/... reads as a template
        # instance but sends the key to evil.example.com -- and the pass-through
        # below would hand it back untouched because its path looks right.
        raise ConfigurationError("Base URL 不能包含用户名或密码（形如 https://user:pass@host）")

    if ANTHROPIC_PATH in parsed.path:
        return trimmed, None

    if "/compatible-mode" in parsed.path:
        rewritten = re.sub(r"/compatible-mode(/v\d+)?", ANTHROPIC_PATH, trimmed)
        return rewritten.rstrip("/"), raw.strip()

    if parsed.hostname and any(part in parsed.hostname for part in _REWRITABLE_HOSTS):
        if parsed.path in ("", "/"):
            return f"{trimmed}{ANTHROPIC_PATH}", raw.strip()

    raise ConfigurationError(
        f"Claude Code 需要 Anthropic 兼容地址，收到的是 {trimmed}"
        f"；请使用以 {ANTHROPIC_PATH} 结尾的地址"
    )


def resolve_tier_key(model_value: Any) -> str | None:
    """Map a top-level `model` alias to the tier variable that resolves it."""
    if not isinstance(model_value, str):
        return None
    return TIER_KEYS_BY_ALIAS.get(model_value.strip().lower())


def compute_env_patch(
    request: ClaudeApplyRequest, current_document: Mapping[str, Any] | None
) -> EnvPatch:
    """Decide which keys to write, given what the live file already says.

    Keys written unconditionally: the connection trio, plus the single tier that
    the current top-level `model` alias resolves through — that tier is what
    actually picks the model, so leaving it alone would make the switch a no-op.
    Every other tier is only filled when absent, which preserves a hand-tuned
    split such as haiku/sonnet=flash with opus=max.
    """
    document = current_document or {}
    env = document.get("env")
    env = env if isinstance(env, dict) else {}

    set_keys = {
        ENV_BASE_URL: request.base_url,
        ENV_AUTH_TOKEN: request.api_key,
        ENV_MODEL: request.model,
    }
    alias_tier = resolve_tier_key(document.get("model"))
    if alias_tier is not None:
        set_keys[alias_tier] = request.model

    add_if_absent = {
        key: request.model
        for key in ALL_TIER_KEYS
        if key not in set_keys and is_absent(env.get(key))
    }
    # Optional by contract: an empty selection keeps the key entirely out of
    # the patch -- not written, not backfilled, not removed.
    if request.auto_mode_model:
        set_keys[ENV_AUTO_MODE] = request.auto_mode_model
    remove = tuple(key for key in (ENV_SHADOWING_KEY,) if key in env)

    return EnvPatch(
        set=set_keys,
        add_if_absent=add_if_absent,
        remove=remove,
        top_level_model=request.model,
        tier_for_alias=alias_tier,
        auto_mode_model=request.auto_mode_model,
    )


def merge_settings(
    document: Mapping[str, Any], patch: EnvPatch
) -> dict[str, Any]:
    """Apply `patch` without mutating `document` or dropping unknown keys."""
    merged = dict(document)
    env_value = merged.get("env")
    env = dict(env_value) if isinstance(env_value, dict) else {}

    for key, value in patch.set.items():
        env[key] = value
    for key, value in patch.add_if_absent.items():
        if is_absent(env.get(key)):
            env[key] = value
    for key in patch.remove:
        env.pop(key, None)

    merged["env"] = env
    merged["model"] = patch.top_level_model
    return merged


def strip_jsonc(text: str) -> str:
    """Remove // and /* */ comments and trailing commas, preserving strings."""
    # Pass 1: comments.
    uncommented = []
    index = 0
    in_string = False
    length = len(text)
    while index < length:
        char = text[index]
        nxt = text[index + 1] if index + 1 < length else ""
        if in_string:
            uncommented.append(char)
            if char == "\\":
                uncommented.append(nxt)
                index += 2
                continue
            if char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            uncommented.append(char)
            index += 1
            continue
        if char == "/" and nxt == "/":
            while index < length and text[index] != "\n":
                index += 1
            continue
        if char == "/" and nxt == "*":
            index += 2
            while index < length - 1 and not (text[index] == "*" and text[index + 1] == "/"):
                index += 1
            index += 2
            continue
        uncommented.append(char)
        index += 1

    # Pass 2: trailing commas before a closing brace/bracket.
    cleaned = "".join(uncommented)
    output = []
    index = 0
    in_string = False
    length = len(cleaned)
    while index < length:
        char = cleaned[index]
        if in_string:
            output.append(char)
            if char == "\\":
                output.append(cleaned[index + 1] if index + 1 < length else "")
                index += 2
                continue
            if char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            output.append(char)
            index += 1
            continue
        if char == ",":
            lookahead = index + 1
            while lookahead < length and cleaned[lookahead].isspace():
                lookahead += 1
            if lookahead < length and cleaned[lookahead] in "}]":
                index += 1
                continue
        output.append(char)
        index += 1
    return "".join(output)


def parse_settings_document(text: str) -> dict[str, Any]:
    """Parse a settings document; a malformed file is fatal, never overwritten."""
    try:
        document = json.loads(strip_jsonc(text))
    except (ValueError, TypeError) as exc:
        raise ClaudeConfigError("settings.json 不是有效的 JSON 对象") from exc
    if not isinstance(document, dict):
        raise ClaudeConfigError("settings.json 顶层必须是 JSON 对象")
    return document


def resolve_settings_path(environ: Mapping[str, str], home: Path) -> Path:
    """Claude Code honours CLAUDE_CONFIG_DIR for its settings location."""
    config_dir = (environ.get("CLAUDE_CONFIG_DIR") or "").strip()
    base = Path(config_dir) if config_dir else home / ".claude"
    return base / "settings.json"


def resolve_onboarding_path(environ: Mapping[str, str], home: Path) -> Path:
    """Onboarding state lives in the home directory regardless of config dir."""
    return home / ".claude.json"


def describe_changes(
    request: ClaudeApplyRequest,
    patch: EnvPatch,
    current_document: Mapping[str, Any] | None,
    settings_path: Path,
    had_comments: bool,
) -> list[str]:
    """Human-readable, itemized description of the pending write."""
    document = current_document or {}
    env = document.get("env")
    env = env if isinstance(env, dict) else {}
    lines = [f"写入目标：{settings_path}"]
    lines.append(f"服务商：{request.provider}    模型：{request.model}")
    lines.append(f"Key 将发往：{request.base_url}")

    for key in (ENV_BASE_URL, ENV_MODEL):
        before = env.get(key)
        after = patch.set[key]
        lines.append(f"{key}：{_shown(before)} → {after}")
    token_state = "已设置，将被覆盖" if ENV_AUTH_TOKEN in env else "（未设置）"
    lines.append(f"{ENV_AUTH_TOKEN}：{token_state} → 写入已保存的推理 Key")

    if patch.tier_for_alias:
        current_model = document.get("model")
        lines.append(
            f"顶层 model 是别名（{current_model}），改模型实际生效的是 "
            f"{patch.tier_for_alias}，将更新为 {request.model}"
        )
    untouched = [
        key for key in ALL_TIER_KEYS
        if key not in patch.set and key not in patch.add_if_absent and key in env
    ]
    if untouched:
        lines.append("以下档位已有配置，保持不变：" + "、".join(untouched))
    if patch.add_if_absent:
        lines.append("以下档位原本为空，将填入所选模型：" + "、".join(patch.add_if_absent))

    if patch.auto_mode_model:
        lines.append(
            f"{ENV_AUTO_MODE}：{_shown(env.get(ENV_AUTO_MODE))} → {patch.auto_mode_model}"
            "（auto mode 判断命令安全性的分类模型，与对话模型无关）")
        if not request.auto_mode_model_lightweight:
            lines.append(
                "⚠ 该分类模型偏重：auto mode 判断可能超时并拦下工具调用，建议改用轻量模型")
    elif not is_absent(env.get(ENV_AUTO_MODE)):
        lines.append(
            f"{ENV_AUTO_MODE} 未选择，保留文件中现有的 {env[ENV_AUTO_MODE]}，不改动")

    lines.append(f"顶层 model 将从 {_shown(document.get('model'))} 改为具体模型名 {patch.top_level_model}")

    if ENV_SHADOWING_KEY in env:
        lines.append(f"将删除 {ENV_SHADOWING_KEY}（它会覆盖 {ENV_AUTH_TOKEN}）")
    if request.rewritten_from:
        lines.append(f"已把你填写的地址改写为 Anthropic 端点：{request.rewritten_from}")
    if not request.base_url_verified:
        lines.append("⚠ 该服务商的 Anthropic 兼容端点未经验证，地址可能需要自行修正")
    if had_comments:
        lines.append("⚠ 原文件含 JSON 注释，重写后注释与键顺序将丢失（写入前会自动备份）")
    lines.append(
        f"其余键（如 hooks、permissions、其他 env 变量）一律保留；本次共 {len(patch.touched_env_keys)} 个 env 键受影响"
    )
    return lines


def _shown(value: Any) -> str:
    return "（未设置）" if is_absent(value) else str(value)


# ----------------------------------------------------------------------
# Filesystem layer
# ----------------------------------------------------------------------


def read_document_at(path: Path) -> tuple[dict[str, Any] | None, bool]:
    """Return (document or None when absent, had_comments).

    Raises ClaudeConfigError instead of yielding an empty document for broken
    content: silently replacing a hand-tuned config with `{}` would be worse
    than doing nothing.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, False
    except (OSError, UnicodeError) as exc:
        raise ClaudeConfigError(f"无法读取 {path.name}") from exc
    had_comments = strip_jsonc(text) != text
    return parse_settings_document(text), had_comments


def backup_file(path: Path, now: float) -> Path | None:
    """Copy `path` aside at owner-only permissions, then tighten older copies.

    The backup holds the previous settings.json, which after the first apply
    contains a plaintext ANTHROPIC_AUTH_TOKEN. `write_bytes` created it 0644 (the
    process umask), so the rollback artifact leaked exactly what
    write_document_atomic works to protect. Both chmod calls below are load
    bearing: an O_CREAT mode is masked by the umask, and rename keeps whatever
    mode the temp had.

    Two applies in the same second overwrite rather than failing: that is the
    documented "重复点击结果一致" behavior, and a name collision must not abort a
    write that has not touched the live file yet. Writing through a temp name and
    renaming -- the same shape write_document_atomic uses -- is what makes that
    safe: opening the final name with O_TRUNC would empty the previous backup
    before knowing this write can succeed, and cleaning up after a failure would
    then delete the user's only rollback copy at exactly the moment it is needed.
    The temp carries its own suffix, so nothing globbing *.bak.* mistakes it for
    a finished backup.
    """
    if not path.exists():
        return None
    target = path.with_name(f"{path.name}.bak.{int(now)}")
    temporary = path.with_name(f"{target.name}.tmp")
    try:
        payload = path.read_bytes()
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
        os.chmod(temporary, 0o600)     # the create mode is masked by the umask
        os.replace(temporary, target)
        os.chmod(target, 0o600)        # belt and braces, mirroring the writer above
    except OSError as exc:
        _discard(temporary)
        raise ClaudeConfigError(f"无法备份 {path.name}") from exc
    _tighten_legacy_backups(path)
    return target


def _tighten_legacy_backups(path: Path) -> None:
    """chmod 0600 the backups this app itself created before the fix.

    Matches only the app's own naming, `settings.json.bak.<digits>`: a hand-made
    `settings.json.bak.20261110-160522` is not ours to reorder. Best effort --
    failing to tighten someone else's file must never fail the write that just
    succeeded.
    """
    for stale in path.parent.glob(f"{path.name}.bak.*"):
        if not stale.name.rsplit(".bak.", 1)[-1].isdigit():
            continue
        try:
            os.chmod(stale, 0o600)
        except OSError:
            pass


def write_document_atomic(path: Path, document: Mapping[str, Any]) -> None:
    """Write pretty JSON with owner-only permissions."""
    payload = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    temporary = path.with_name(f"{path.name}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError:
            _discard(temporary)
            raise
        os.replace(temporary, path)
        # Tighten a pre-existing 0644 file: it is about to hold a token.
        os.chmod(path, 0o600)
    except OSError as exc:
        # Cleanup must never replace the real cause with its own error.
        _discard(temporary)
        raise CacheError(f"无法写入 {path.name}") from exc


def _discard(path: Path) -> None:
    """Best-effort temp removal. `missing_ok` only covers FileNotFoundError."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def apply_request(
    request: ClaudeApplyRequest,
    settings_path: Path,
    *,
    clock: Callable[[], float] = time.time,
) -> ApplyResult:
    """Read → validate → patch → backup → write. Aborts with zero writes on damage."""
    document, had_comments = read_document_at(settings_path)
    if document is not None and "env" in document and not isinstance(document["env"], dict):
        # Overwriting a non-object env would discard data we cannot interpret.
        raise ClaudeConfigError("settings.json 的 env 不是 JSON 对象")
    patch = compute_env_patch(request, document)
    created = document is None
    backup_path = backup_file(settings_path, clock())
    merged = merge_settings(document or {}, patch)
    write_document_atomic(settings_path, merged)

    env = merged["env"]
    preserved = len([key for key in env if key not in patch.touched_env_keys])
    return ApplyResult(
        settings_path=settings_path,
        backup_path=backup_path,
        set_keys=tuple(patch.set),
        added_keys=tuple(patch.add_if_absent),
        removed_keys=tuple(patch.remove),
        preserved_env_keys=preserved,
        created=created,
    )


def mark_onboarding_completed(claude_json_path: Path) -> bool:
    """Set hasCompletedOnboarding without ever rebuilding a file we cannot parse.

    `.claude.json` carries project history, so a merge failure is reported as
    "skipped" rather than repaired by writing a fresh document.
    """
    try:
        text = claude_json_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        document: dict[str, Any] = {}
    except (OSError, UnicodeError):
        return False
    else:
        try:
            parsed = json.loads(text)
        except ValueError:
            return False
        if not isinstance(parsed, dict):
            return False
        document = parsed
    if document.get("hasCompletedOnboarding") is True:
        return True
    document["hasCompletedOnboarding"] = True
    try:
        write_document_atomic(claude_json_path, document)
    except (CacheError, ClaudeConfigError):
        return False
    return True
