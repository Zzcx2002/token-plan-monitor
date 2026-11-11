"""Provider registry: the single source of truth for supported quota sources.

Everything provider-specific that is not protocol details lives here — display
labels, credential vault entry names, region defaults and the settings-dialog
copy — so the UI and storage layers never hardcode a provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .bailian import BailianClient
from .models import (
    DEFAULT_PROVIDER,
    PROVIDER_BAILIAN,
    PROVIDER_IDS,
    PROVIDER_LABELS,
    PROVIDER_VOLCENGINE,
    ConfigurationError,
    Credentials,
    Settings,
)
from .volcengine import VolcengineClient


@dataclass(frozen=True)
class ClaudeModelOption:
    #: Exact model id written into ANTHROPIC_MODEL. Never invented — see catalogs.
    id: str
    label: str


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    client: Any
    access_key_username: str
    secret_key_username: str
    default_region: str
    access_key_label: str
    secret_key_label: str
    #: Prefix for the "used / total" absolute-quota line, e.g. "AFP".
    unit_label: str
    #: Settings-dialog copy; `{store}` is replaced with the OS credential store.
    credential_note: str
    help_text: str
    # --- Claude Code hookup -------------------------------------------------
    #: Inference key lives beside, but never mixed with, the control-plane AK/SK.
    inference_key_username: str
    #: Anthropic-compatible endpoint template with `{region}`; "" = user must supply.
    claude_base_url_template: str
    #: False when the endpoint could not be verified from an authoritative source.
    claude_base_url_verified: bool
    claude_models: tuple[ClaudeModelOption, ...]
    claude_default_model: str
    #: Shape hint shown next to the inference key field; "" when unverified.
    inference_key_hint: str

    def default_claude_base_url(self, region: str) -> str:
        if not self.claude_base_url_template:
            return ""
        return self.claude_base_url_template.format(region=region or self.default_region)


#: Token Plan supported text/multimodal chat models, transcribed from the
#: official 百炼 Token Plan 个人版 overview table. Image, video, speech and
#: decision-only models are excluded because Claude Code cannot route to them,
#: and the retired `qwen3.8-max-preview` is left out deliberately.
BAILIAN_CLAUDE_MODELS = (
    ClaudeModelOption("auto", "auto · 按请求自动匹配"),
    ClaudeModelOption("qwen3.8-max", "qwen3.8-max · 推理/视觉理解"),
    ClaudeModelOption("qwen3.8-flash", "qwen3.8-flash · 推理/视觉理解"),
    ClaudeModelOption("qwen3.7-max", "qwen3.7-max · 推理/文本"),
    ClaudeModelOption("qwen3.7-plus", "qwen3.7-plus · 推理/视觉理解"),
    ClaudeModelOption("qwen3.6-flash", "qwen3.6-flash · 推理/视觉理解"),
    ClaudeModelOption("deepseek-v4-pro", "deepseek-v4-pro · 推理/文本"),
    ClaudeModelOption("deepseek-v4.1-flash", "deepseek-v4.1-flash · 推理/视觉理解"),
    ClaudeModelOption("glm-5.3", "glm-5.3 · 推理/文本"),
    ClaudeModelOption("glm-5.2", "glm-5.2 · 推理/文本"),
)

PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        id=PROVIDER_VOLCENGINE,
        label=PROVIDER_LABELS[PROVIDER_VOLCENGINE],
        client=VolcengineClient,
        access_key_username="volcengine_access_key_id",
        secret_key_username="volcengine_secret_access_key",
        default_region="cn-beijing",
        access_key_label="AccessKey ID",
        secret_key_label="Secret AccessKey",
        unit_label="AFP",
        credential_note="使用控制面账号 AK/SK，不是 sk-… 推理 Key。凭据仅存储在{store}。",
        help_text=(
            "在火山引擎账号的访问密钥管理中获取 AK/SK，并确保有套餐用量查询权限。"
            "它们不是 sk-… 推理 Key。\n"
            "Region 默认 cn-beijing，请按使用区域填写。刷新间隔支持 5–1440 分钟。"
        ),
        inference_key_username="volcengine_inference_api_key",
        # Ark publishes only the OpenAI-compatible data plane
        # (https://ark.<region>.volces.com/api/v3); the vendor docs state the
        # BaseUrl must come from the user. Prefilling a URL that cannot serve
        # Anthropic Messages would be worse than leaving it blank, so the user
        # supplies it and no model list is asserted here.
        claude_base_url_template="",
        claude_base_url_verified=False,
        claude_models=(),
        claude_default_model="",
        # No authoritative description of an Ark inference key's shape, so the
        # field is shown without a format hint rather than with a guessed one.
        inference_key_hint="",
    ),
    ProviderSpec(
        id=PROVIDER_BAILIAN,
        label=PROVIDER_LABELS[PROVIDER_BAILIAN],
        client=BailianClient,
        access_key_username="bailian_access_key_id",
        secret_key_username="bailian_secret_access_key",
        default_region="cn-beijing",
        access_key_label="阿里云 AccessKey ID",
        secret_key_label="阿里云 Secret AccessKey",
        unit_label="额度",
        credential_note=(
            "使用阿里云账号 AK/SK，不是 sk-… 或 sk-sp-… 推理 Key。凭据仅存储在{store}。"
        ),
        help_text=(
            "在阿里云账号的 AccessKey 管理中获取 AK/SK。RAM 子账号还需授予 "
            "AliyunTokenPlanReadOnlyAccess 与 AliyunBSSReadOnlyAccess 只读策略。\n"
            "Token Plan 目前仅支持华北2（北京），Region 请填 cn-beijing。"
            "刷新间隔支持 5–1440 分钟。"
        ),
        inference_key_username="bailian_inference_api_key",
        # Verified from the Token Plan quickstart: the Anthropic-compatible
        # base URL is https://token-plan.<region>.maas.aliyuncs.com/apps/anthropic
        claude_base_url_template=(
            "https://token-plan.{region}.maas.aliyuncs.com/apps/anthropic"
        ),
        claude_base_url_verified=True,
        claude_models=BAILIAN_CLAUDE_MODELS,
        claude_default_model="auto",
        inference_key_hint="，百炼形如 sk-sp-…",
    ),
)

_BY_ID = {spec.id: spec for spec in PROVIDERS}


def get_provider(provider: str) -> ProviderSpec:
    spec = _BY_ID.get(provider)
    if spec is None:
        raise ConfigurationError(f"未知的服务商：{provider}")
    return spec


def default_region(provider: str) -> str:
    return get_provider(provider).default_region


def create_client(settings: Settings, credentials: Credentials) -> Any:
    """Build the quota client for `settings.provider`.

    Keeps the historical `(settings, credentials)` call signature so the monitor
    and its tests can inject a stub without knowing about providers.
    """
    return get_provider(settings.provider).client(settings, credentials)


DEFAULT_PROVIDER_ID = DEFAULT_PROVIDER
SUPPORTED_PROVIDER_IDS = PROVIDER_IDS
