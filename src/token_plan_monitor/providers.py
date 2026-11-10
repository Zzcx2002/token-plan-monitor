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
