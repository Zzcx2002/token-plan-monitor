"""Pure helpers shared by the native shell and offline tests."""
from __future__ import annotations

import re

from ..claude_code import ClaudeApplyRequest, normalize_base_url
from ..models import (
    CARD_MAX_DIMENSION,
    CARD_MIN_HEIGHT,
    CARD_MIN_WIDTH,
    ConfigurationError,
    Credentials,
    MonitorViewState,
    Settings,
    validate_region,
)
from ..providers import get_provider


class FieldValidationError(ConfigurationError):
    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field


def primary_action(state: MonitorViewState) -> tuple[str, str]:
    if state.status == "unconfigured":
        return "连接账号", "check-credentials"
    if state.error_category in ("authentication", "configuration"):
        return "检查凭据", "check-credentials"
    if state.status in ("stale", "error"):
        return "重试", "refresh"
    return "立即刷新", "refresh"


def lowest_windows(state: MonitorViewState) -> frozenset[str]:
    windows = state.snapshot.windows if state.snapshot else ()
    if not windows:
        return frozenset()
    lowest = min(w.remaining_percent for w in windows)
    return frozenset(w.kind for w in windows if w.remaining_percent == lowest)


def _field_prompt(verb: str, field_label: str) -> str:
    """Join a Chinese verb and a field label with typographically correct spacing.

    Latin labels ("AccessKey ID") need a separating space; labels that already
    start with Chinese ("阿里云 AccessKey ID") read better without one.
    """
    return f"{verb}{' ' if field_label[:1].isascii() else ''}{field_label}"


def validate_configuration(provider: str, ak: str, sk: str, region: str, interval: str,
                           existing: Credentials | None) -> tuple[Settings, Credentials]:
    spec = get_provider(provider)
    if not ak.strip():
        raise FieldValidationError("ak", _field_prompt("请填写", spec.access_key_label))
    if not sk.strip() and existing is None:
        raise FieldValidationError(
            "sk", _field_prompt("首次连接请填写", spec.secret_key_label)
        )
    try:
        minutes = int(interval.strip())
    except ValueError as exc:
        raise FieldValidationError("interval", "刷新间隔必须是整数分钟") from exc
    if not 5 <= minutes <= 1440:
        raise FieldValidationError("interval", "刷新间隔必须在 5–1440 分钟之间")
    try:
        # An empty region falls back to the provider default rather than failing.
        settings = Settings(provider=provider, region=region.strip() or spec.default_region,
                            refresh_minutes=minutes)
    except ConfigurationError as exc:
        raise FieldValidationError("region", exc.message) from exc
    credentials = Credentials(ak, sk.strip() or (existing.secret_access_key if existing else ""))
    return settings, credentials


MODEL_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+\-]{0,199}")


def validate_claude_configuration(provider: str, region: str, popup_model: str,
                                  custom_model: str, base_url_text: str,
                                  api_key_text: str,
                                  existing_key: str | None) -> ClaudeApplyRequest:
    """Turn the Claude section's raw text into an apply request, field-tagged.

    Mirrors `validate_configuration`: an empty key reuses what is already in the
    vault, and an empty base URL falls back to the provider default.
    """
    spec = get_provider(provider)
    model = (custom_model.strip() or popup_model.strip())
    if not model:
        raise FieldValidationError("model", "请选择模型，或直接填写模型 ID")
    if MODEL_ID_PATTERN.fullmatch(model) is None:
        raise FieldValidationError("model", "模型 ID 只能包含字母、数字和 . _ : / + -")

    try:
        # The region lands inside the provider's URL template just below, so it
        # must pass the same rule the save path enforces. Empty falls back to the
        # provider default, exactly as validate_configuration does above, so a
        # blank field still yields the official address.
        region_value = validate_region(provider, region.strip() or spec.default_region)
    except ConfigurationError as exc:
        raise FieldValidationError("region", exc.message) from exc

    base_url = base_url_text.strip() or spec.default_claude_base_url(region_value)
    if not base_url:
        raise FieldValidationError(
            "base_url", f"{spec.label}没有已验证的 Anthropic 兼容地址，请自行填写"
        )
    try:
        normalized, rewritten_from = normalize_base_url(base_url)
    except ConfigurationError as exc:
        raise FieldValidationError("base_url", exc.message) from exc

    api_key = api_key_text.strip() or (existing_key.strip() if existing_key else "")
    if not api_key:
        raise FieldValidationError("api_key", "请填写推理 API Key（百炼形如 sk-sp-…）")
    if any(character.isspace() for character in api_key):
        raise FieldValidationError("api_key", "推理 API Key 不能包含空格")

    return ClaudeApplyRequest(
        provider=provider,
        base_url=normalized,
        model=model,
        api_key=api_key,
        rewritten_from=rewritten_from,
        base_url_verified=spec.claude_base_url_verified,
    )


def resolve_claude_seed(spec, draft, prefs, live_base_url="", live_model=""):
    """Initial values: draft → **live config** → saved prefs → provider default.

    Why this order:

    * `draft` (this dialog session) comes first so switching providers never
      discards what was just typed — the reported bug.
    * The live settings.json ranks **above** stored prefs. Prefs are only a
      cache of what we last wrote; if the file now says something else, the
      user hand-edited it, and trusting the cache would let a re-apply silently
      revert their proxy host. Keeping that promise outranks remembering an
      unapplied choice from a previous session.
    * `prefs` fills only what the live file does not carry.

    `model` and `custom_model` are taken as a pair from one layer so a draft's
    popup choice is never merged with an older layer's free-form id.
    """
    layers = []
    if draft is not None:
        layers.append((draft.model or "", draft.custom_model or "", draft.base_url or ""))
    # The live file has no free-form id of its own, only ANTHROPIC_MODEL.
    layers.append((live_model or "", "", live_base_url or ""))
    if prefs is not None:
        layers.append((prefs.model or "", prefs.custom_model or "", prefs.base_url or ""))

    model = custom = base_url = ""
    for layer_model, layer_custom, layer_base in layers:
        if not model and not custom and (layer_model or layer_custom):
            model, custom = layer_model, layer_custom
        if not base_url and layer_base:
            base_url = layer_base
        if model and base_url:
            break

    known = [option.id for option in spec.claude_models]
    if model and model not in known:
        # An id outside the catalogue can only be expressed as free-form text.
        model, custom = "", model
    if not model and not custom:
        model = spec.claude_default_model
    return {"claude_model": model, "claude_custom": custom, "claude_base_url": base_url}


ScreenRect = tuple[float, float, float, float]
FrameRect = tuple[float, float, float, float]


def _nearest_screen(x: float | None, y: float | None, width: float, height: float,
                    screens: list[ScreenRect]) -> ScreenRect:
    if x is None or y is None:
        return screens[0]
    cx, cy = x + width / 2, y + height / 2

    def distance(screen: ScreenRect) -> float:
        sx, sy, sw, sh = screen
        return max(sx - cx, 0, cx - sx - sw) ** 2 + max(sy - cy, 0, cy - sy - sh) ** 2

    return min(screens, key=distance)


def fit_card_size(x: float | None, y: float | None, width: float, height: float,
                  screens: list[ScreenRect]) -> tuple[int, int]:
    """Restore the minimum size without letting a card exceed its nearest display."""
    width = max(CARD_MIN_WIDTH, min(round(width), CARD_MAX_DIMENSION))
    height = max(CARD_MIN_HEIGHT, min(round(height), CARD_MAX_DIMENSION))
    if not screens:
        return width, height
    _, _, screen_width, screen_height = _nearest_screen(x, y, width, height, screens)
    return (
        min(width, max(CARD_MIN_WIDTH, round(screen_width))),
        min(height, max(CARD_MIN_HEIGHT, round(screen_height))),
    )


def resize_card_frame(frame: FrameRect, dx: float, dy: float, edges: frozenset[str],
                      screen: ScreenRect | None = None) -> tuple[int, int, int, int]:
    """Resize a Cocoa frame while anchoring the edges opposite the dragged ones."""
    x, y, width, height = frame
    right, top = x + width, y + height
    if "left" in edges:
        maximum = right - screen[0] if screen else CARD_MAX_DIMENSION
        width = min(max(CARD_MIN_WIDTH, width - dx), max(CARD_MIN_WIDTH, maximum))
        x = right - width
    elif "right" in edges:
        maximum = screen[0] + screen[2] - x if screen else CARD_MAX_DIMENSION
        width = min(max(CARD_MIN_WIDTH, width + dx), max(CARD_MIN_WIDTH, maximum))
    if "bottom" in edges:
        maximum = top - screen[1] if screen else CARD_MAX_DIMENSION
        height = min(max(CARD_MIN_HEIGHT, height - dy), max(CARD_MIN_HEIGHT, maximum))
        y = top - height
    elif "top" in edges:
        maximum = screen[1] + screen[3] - y if screen else CARD_MAX_DIMENSION
        height = min(max(CARD_MIN_HEIGHT, height + dy), max(CARD_MIN_HEIGHT, maximum))
    return round(x), round(y), round(width), round(height)


def clamp_card(x: float | None, y: float | None, width: float, height: float,
               screens: list[ScreenRect]) -> tuple[int, int]:
    """Cocoa point coordinates, including displays left of/below the main display."""
    if not screens:
        return int(x or 0), int(y or 0)
    if x is None or y is None:
        sx, sy, sw, sh = screens[0]
        x, y = sx + sw - width - 24, sy + sh - height - 24
    sx, sy, sw, sh = _nearest_screen(x, y, width, height, screens)
    return round(max(sx, min(x, sx + max(0, sw - width)))), round(max(sy, min(y, sy + max(0, sh - height))))
