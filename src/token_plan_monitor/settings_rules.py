"""Provider-agnostic settings validation shared by both platform shells.

Everything here is pure: no AppKit, no Tk, no disk or network access, so the
same rules run inside the macOS native shell, the Windows Tk shell and the
offline test suite. The macOS package re-exports these names from
``macos.layout`` to keep its historical import paths working.
"""
from __future__ import annotations

from .claude_code import ARK_PLAN_URLS, ClaudeApplyRequest, normalize_base_url
from .models import (
    HEAVY_ID_MARKS,
    MODEL_ID_PATTERN,
    AlertSettings,
    ConfigurationError,
    Credentials,
    Settings,
    validate_region,
)
from .providers import get_provider


class FieldValidationError(ConfigurationError):
    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field


def _field_prompt(verb: str, field_label: str) -> str:
    """Join a Chinese verb and a field label with typographically correct spacing.

    Latin labels ("AccessKey ID") need a separating space; labels that already
    start with Chinese ("阿里云 AccessKey ID") read better without one.
    """
    return f"{verb}{' ' if field_label[:1].isascii() else ''}{field_label}"


def validate_configuration(provider: str, ak: str, sk: str, region: str, interval: str,
                           existing: Credentials | None,
                           alert_enabled: bool = True, alert_threshold: str = "10",
                           alert_reset: bool = False) -> tuple[Settings, Credentials]:
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
        threshold = int(alert_threshold.strip())
    except ValueError as exc:
        raise FieldValidationError("alert_threshold", "阈值必须是 1–99 的整数") from exc
    if not 1 <= threshold <= 99:
        raise FieldValidationError("alert_threshold", "阈值必须在 1–99 之间")
    try:
        # An empty region falls back to the provider default rather than failing.
        settings = Settings(provider=provider, region=region.strip() or spec.default_region,
                            refresh_minutes=minutes,
                            alert=AlertSettings(enabled=alert_enabled,
                                                threshold_percent=threshold,
                                                notify_on_reset=alert_reset))
    except ConfigurationError as exc:
        raise FieldValidationError("region", exc.message) from exc
    credentials = Credentials(ak, sk.strip() or (existing.secret_access_key if existing else ""))
    return settings, credentials


def _looks_lightweight(model: str, spec) -> bool:
    options = {option.id: option for option in spec.claude_models}
    option = options.get(model)
    if option is not None:
        return option.lightweight
    return not any(mark in model.lower() for mark in HEAVY_ID_MARKS)


def validate_claude_configuration(provider: str, region: str, popup_model: str,
                                  custom_model: str, base_url_text: str,
                                  api_key_text: str,
                                  existing_key: str | None,
                                  popup_auto: str = "", custom_auto: str = "",
                                  popup_subagent: str = "",
                                  custom_subagent: str = "") -> ClaudeApplyRequest:
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
            "base_url", f"请选择{spec.label}套餐对应的 Anthropic 地址，或填写兼容代理地址"
        )
    try:
        normalized, rewritten_from = normalize_base_url(base_url, provider)
    except ConfigurationError as exc:
        raise FieldValidationError("base_url", exc.message) from exc

    api_key = api_key_text.strip() or (existing_key.strip() if existing_key else "")
    if not api_key:
        raise FieldValidationError("api_key", "请填写推理 API Key（百炼形如 sk-sp-…）")
    if any(character.isspace() for character in api_key):
        raise FieldValidationError("api_key", "推理 API Key 不能包含空格")

    # Optional: nothing selected => the key is never touched. Custom beats popup,
    # exactly like the main model pair above.
    auto_model = (custom_auto.strip() or popup_auto.strip())
    if auto_model and MODEL_ID_PATTERN.fullmatch(auto_model) is None:
        raise FieldValidationError("auto_model", "模型 ID 只能包含字母、数字和 . _ : / + -")

    # Optional explicit subagent model; empty keeps the tier preserve/backfill
    # rule. Custom beats popup, exactly like the pairs above. No lightweight
    # warning here -- that concern is specific to the auto mode classifier.
    subagent_model = (custom_subagent.strip() or popup_subagent.strip())
    if subagent_model and MODEL_ID_PATTERN.fullmatch(subagent_model) is None:
        raise FieldValidationError("subagent_model", "模型 ID 只能包含字母、数字和 . _ : / + -")

    return ClaudeApplyRequest(
        provider=provider,
        base_url=normalized,
        model=model,
        api_key=api_key,
        rewritten_from=rewritten_from,
        base_url_verified=(normalized in ARK_PLAN_URLS.values() if provider == "volcengine"
                           else normalized == spec.default_claude_base_url(region_value)),
        auto_mode_model=auto_model,
        auto_mode_model_lightweight=(
            _looks_lightweight(auto_model, spec) if auto_model else True),
        subagent_model=subagent_model,
    )


def resolve_claude_seed(spec, draft, prefs, live_base_url="", live_model="",
                        live_auto_model="", model_options=None,
                        live_subagent_model=""):
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
    popup choice is never merged with an older layer's free-form id; the auto
    mode and subagent pairs obey the same rule and have no default -- "unset"
    is a value.

    `model_options` overrides the catalogue used to decide popup-vs-custom. The
    UI passes the live-merged catalogue so a freshly fetched model lands in the
    dropdown instead of being demoted to the free-form field; None keeps the
    provider's static catalogue (the offline / pure-test default).
    """
    layers = []
    if draft is not None:
        layers.append((draft.model or "", draft.custom_model or "", draft.base_url or "",
                       draft.auto_mode_model or "", draft.auto_mode_custom_model or "",
                       draft.subagent_model or "", draft.subagent_custom_model or ""))
    # The live file has no free-form ids of its own, only ANTHROPIC_MODEL and
    # the auto mode / subagent variables.
    layers.append((live_model or "", "", live_base_url or "", live_auto_model or "", "",
                   live_subagent_model or "", ""))
    if prefs is not None:
        layers.append((prefs.model or "", prefs.custom_model or "", prefs.base_url or "",
                       prefs.auto_mode_model or "", prefs.auto_mode_custom_model or "",
                       prefs.subagent_model or "", prefs.subagent_custom_model or ""))

    model = custom = base_url = auto = auto_custom = subagent = subagent_custom = ""
    for (layer_model, layer_custom, layer_base, layer_auto, layer_auto_custom,
            layer_subagent, layer_subagent_custom) in layers:
        if not model and not custom and (layer_model or layer_custom):
            model, custom = layer_model, layer_custom
        if not auto and not auto_custom and (layer_auto or layer_auto_custom):
            auto, auto_custom = layer_auto, layer_auto_custom
        if not subagent and not subagent_custom and (layer_subagent or layer_subagent_custom):
            subagent, subagent_custom = layer_subagent, layer_subagent_custom
        if not base_url and layer_base:
            base_url = layer_base
        # Every pair must be resolved before stopping, or a draft that lacks one
        # would hide a lower layer's value for it.
        if model and base_url and auto and (subagent or subagent_custom):
            break

    options = model_options if model_options is not None else spec.claude_models
    known = [option.id for option in options]
    if model and model not in known:
        # An id outside the catalogue can only be expressed as free-form text.
        model, custom = "", model
    if not model and not custom:
        model = spec.claude_default_model
    # The auto and subagent pairs have no provider default: "nothing" is a value.
    if auto and auto not in known:
        auto, auto_custom = "", auto
    if subagent and subagent not in known:
        subagent, subagent_custom = "", subagent
    return {"claude_model": model, "claude_custom": custom, "claude_base_url": base_url,
            "claude_auto_model": auto, "claude_auto_custom": auto_custom,
            "claude_subagent_model": subagent, "claude_subagent_custom": subagent_custom}
