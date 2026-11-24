"""Live model catalogue for the Claude Code hookup.

The static catalogue in `providers.py` is transcribed from the vendor's official
overview table, so it goes stale the moment a plan ships or retires a model.
This module fetches the plan's *actual* model list from the provider's
OpenAI-compatible endpoint, keeps only the chat models Claude Code can route to,
and merges the result with the static catalogue: curated labels and lightweight
flags survive for known models, newly offered models appear with plain labels,
and retired models drop out.

Fetching is strictly best-effort. Any failure (offline, bad key, unexpected
payload) raises `ModelCatalogError` and the caller falls back to the static
catalogue -- the settings dialog never blocks on the network. Like
`claude_code.py`, this module imports nothing platform specific and never reads
the environment or a home directory on its own: the caller passes the base URL
and key explicitly so every path stays testable.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import requests

from .claude_code import ANTHROPIC_PATH, url_transport_error
from .models import HEAVY_ID_MARKS, MODEL_ID_PATTERN, QueryError
from .providers import ClaudeModelOption

#: OpenAI-compatible path that lists the models a plan's inference key can call.
#: Verified live against both official Token Plan hosts (the legacy regional
#: maas.aliyuncs.com host and the fixed token-plan.maas.qianwenaiapi.com host).
MODELS_PATH = "/compatible-mode/v1/models"

#: Shared blocked-state copy for both desktop shells so the status labels can
#: never drift apart. Kept here next to the fetch logic that produces them.
CATALOG_NEEDS_KEY = "填写推理 API Key 后将自动获取模型列表"
CATALOG_UNSUPPORTED_URL = "该 Base URL 不支持在线获取模型"


def catalog_block_reason(base_url: str, api_key: str) -> str | None:
    """Why an automatic catalogue fetch cannot start, or None when it can."""
    if not (api_key or "").strip():
        return CATALOG_NEEDS_KEY
    if ANTHROPIC_PATH not in (base_url or ""):
        return CATALOG_UNSUPPORTED_URL
    return None

#: Substrings marking a model Claude Code cannot chat with: image, video, speech
#: and decision-only models. Mirrors the exclusion documented on
#: BAILIAN_CLAUDE_MODELS; "wan" is the visual-generation family, whose members
#: may not carry an explicit image/video token (e.g. a future t2v variant).
_NON_CHAT_MARKERS = (
    "image", "video", "audio", "tts", "asr", "speech", "decision", "wan",
)


class ModelCatalogError(QueryError):
    """The live model list could not be fetched or parsed."""


def models_url_for(base_url: str) -> str:
    """Derive the compatible-mode model-list URL from an Anthropic base URL.

    Both live on the same host; only the path differs. The Anthropic path (and
    anything after it) is stripped back to the host root before hanging the
    model-list path off it, so a region subdomain or a proxy host is preserved.
    """
    trimmed = (base_url or "").strip().rstrip("/")
    if not trimmed:
        raise ModelCatalogError("Base URL 不能为空")
    try:
        parsed = urlsplit(trimmed)
    except ValueError as exc:
        raise ModelCatalogError("Base URL 格式无效") from exc
    if not parsed.netloc:
        raise ModelCatalogError("Base URL 缺少主机名")
    # Same transport policy as the apply path (claude_code.normalize_base_url):
    # the Bearer key below must never ride plaintext HTTP or a userinfo-smuggled
    # host. Proxy hosts stay allowed -- the user typed them deliberately.
    transport_error = url_transport_error(parsed)
    if transport_error is not None:
        raise ModelCatalogError(transport_error)
    index = parsed.path.find(ANTHROPIC_PATH)
    if index >= 0:
        root_path = parsed.path[:index]
    else:
        # Defensive: an OpenAI-compatible URL (.../compatible-mode/v1) must be
        # truncated at that prefix, otherwise the models path is hung twice.
        # The UI fetch gate only admits Anthropic paths today, so this only
        # protects direct callers of this helper.
        compatible_index = parsed.path.find("/compatible-mode")
        root_path = parsed.path[:compatible_index] if compatible_index >= 0 else parsed.path
    root = f"{parsed.scheme}://{parsed.netloc}{root_path}".rstrip("/")
    return root + MODELS_PATH


def is_chat_model(model_id: str) -> bool:
    """True when Claude Code can route a conversation to this model."""
    lowered = model_id.lower()
    return not any(marker in lowered for marker in _NON_CHAT_MARKERS)


def parse_model_ids(payload: Any) -> tuple[str, ...]:
    """Extract chat-model ids from an OpenAI `/v1/models` response.

    Untrusted input: every id is shape-checked against MODEL_ID_PATTERN and
    filtered to chat models, deduped while preserving the server's order. A
    malformed envelope raises rather than yielding a silently empty catalogue.
    """
    if not isinstance(payload, dict):
        raise ModelCatalogError("模型列表响应不是 JSON 对象")
    data = payload.get("data")
    if not isinstance(data, list):
        raise ModelCatalogError("模型列表响应缺少 data 数组")
    seen: set[str] = set()
    ids: list[str] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        if not isinstance(model_id, str):
            continue
        model_id = model_id.strip()
        if not model_id or MODEL_ID_PATTERN.fullmatch(model_id) is None:
            continue
        if not is_chat_model(model_id) or model_id in seen:
            continue
        seen.add(model_id)
        ids.append(model_id)
    return tuple(ids)


def fetch_model_ids(
    base_url: str,
    api_key: str,
    session: Any = None,
    timeout: float = 15,
) -> tuple[str, ...]:
    """GET the live model list with the inference key; return chat-model ids.

    The key is sent only to the host derived from `base_url`, as a Bearer token,
    and is never logged. A non-200 status, a network error or an unparseable /
    empty body all raise ModelCatalogError so the caller can fall back.
    """
    url = models_url_for(base_url)
    http = session if session is not None else requests.Session()
    try:
        response = http.get(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            # A redirect could forward the Bearer key to another host; the
            # control-plane clients already refuse redirects for this reason.
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        raise ModelCatalogError("无法连接模型列表接口") from exc
    if response.status_code != 200:
        raise ModelCatalogError(f"模型列表接口返回 HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ModelCatalogError("模型列表响应不是有效 JSON") from exc
    ids = parse_model_ids(payload)
    if not ids:
        raise ModelCatalogError("模型列表为空")
    return ids


def _infer_lightweight(model_id: str) -> bool:
    return not any(mark in model_id.lower() for mark in HEAVY_ID_MARKS)


def merge_model_options(
    static_options: tuple[ClaudeModelOption, ...],
    fetched_ids: tuple[str, ...],
) -> tuple[ClaudeModelOption, ...]:
    """Merge live ids with the curated catalogue into the effective model list.

    Curated models keep their labels, lightweight flags and catalogue order, but
    only while the plan still offers them; newly offered models are appended in
    server order with plain labels and an inferred lightweight flag. The result
    is exactly the live chat-model set, so a retired model disappears and a new
    one appears without any hand-maintained transcription.
    """
    fetched_set = set(fetched_ids)
    merged: list[ClaudeModelOption] = []
    seen: set[str] = set()
    for option in static_options:
        if option.id in fetched_set and option.id not in seen:
            merged.append(option)
            seen.add(option.id)
    for model_id in fetched_ids:
        if model_id not in seen:
            merged.append(
                ClaudeModelOption(
                    id=model_id,
                    label=model_id,
                    lightweight=_infer_lightweight(model_id),
                )
            )
            seen.add(model_id)
    return tuple(merged)
