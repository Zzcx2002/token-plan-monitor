from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path
from typing import Any

import keyring
from .paths import PROJECT_ROOT, data_directory

from .appearance import AppearancePreferences

from .models import (
    DEFAULT_PROVIDER,
    SCHEMA_VERSION,
    CacheError,
    ClaudeProviderPrefs,
    ConfigurationError,
    Credentials,
    Settings,
    UsageSnapshot,
    WidgetPreferences,
)
from .providers import get_provider

DEFAULT_DATA_DIR = data_directory()
KEYRING_SERVICE = "TokenPlanMonitor"
# Vault entry names of the default provider. Each provider owns its own pair, so
# switching providers never exposes or overwrites the other one's secret.
_DEFAULT_SPEC = get_provider(DEFAULT_PROVIDER)
AK_USERNAME = _DEFAULT_SPEC.access_key_username
SK_USERNAME = _DEFAULT_SPEC.secret_key_username


class Storage:
    def __init__(self, data_dir: Path | None = None, keyring_backend: Any = None):
        self.data_dir = data_dir or DEFAULT_DATA_DIR
        self.config_path = self.data_dir / "config.json"
        self.snapshot_path = self.data_dir / "snapshot.json"
        self.widget_path = self.data_dir / "widget.json"
        self.geometry_path = self.data_dir / "window_geometry.json"
        self.appearance_path = self.data_dir / "appearance.json"
        self.claude_path = self.data_dir / "claude.json"
        # Explicitly select the OS backend: never silently use a plaintext plugin.
        if keyring_backend is None and sys.platform == "darwin":
            from keyring.backends.macOS import Keyring
            keyring_backend = Keyring()
        self._keyring = keyring_backend if keyring_backend is not None else keyring
        self.credential_store_name = "macOS 钥匙串" if sys.platform == "darwin" else "Windows 凭据管理器"

    def load_window_size(self, name: str) -> tuple[float, float] | None:
        value = (self._read_json(self.geometry_path) or {}).get(name)
        if not isinstance(value, list) or len(value) != 2:
            return None
        if any(type(n) not in (int, float) or not math.isfinite(n) or n <= 0 for n in value):
            return None
        return float(value[0]), float(value[1])

    def save_window_size(self, name: str, size: tuple[float, float]) -> None:
        values = self._read_json(self.geometry_path) or {}
        values[name] = list(size)
        self._write_json(self.geometry_path, values)

    def load_appearance(self) -> AppearancePreferences:
        return AppearancePreferences.from_dict(self._read_json(self.appearance_path))

    def save_appearance(self, preferences: AppearancePreferences) -> None:
        self._write_json(self.appearance_path, preferences.to_dict())

    def load_settings(self) -> Settings:
        value = self._read_json(self.config_path)
        if value is None:
            return Settings()
        try:
            if value.get("schema_version") != SCHEMA_VERSION:
                raise ConfigurationError("配置版本不兼容")
            return Settings.from_dict(value.get("settings"))
        except (ConfigurationError, KeyError, TypeError, ValueError):
            return Settings()

    def save_settings(self, settings: Settings) -> None:
        self._write_json(
            self.config_path,
            {"schema_version": SCHEMA_VERSION, "settings": settings.to_dict()},
        )

    def load_widget_preferences(self) -> WidgetPreferences:
        value = self._read_json(self.widget_path)
        if value is None:
            return WidgetPreferences()
        try:
            if value.get("schema_version") != SCHEMA_VERSION:
                raise ValueError("Incompatible widget preferences")
            return WidgetPreferences.from_dict(value.get("widget"))
        except (TypeError, ValueError):
            return WidgetPreferences()

    def save_widget_preferences(self, preferences: WidgetPreferences) -> None:
        self._write_json(
            self.widget_path,
            {"schema_version": SCHEMA_VERSION, "widget": preferences.to_dict()},
        )

    def load_snapshot(self) -> UsageSnapshot | None:
        value = self._read_json(self.snapshot_path)
        if value is None:
            return None
        try:
            if value.get("schema_version") != SCHEMA_VERSION:
                return None
            return UsageSnapshot.from_dict(value.get("snapshot"))
        except (KeyError, TypeError, ValueError):
            return None

    def save_snapshot(self, snapshot: UsageSnapshot) -> None:
        self._write_json(
            self.snapshot_path,
            {"schema_version": SCHEMA_VERSION, "snapshot": snapshot.to_dict()},
        )

    def clear_snapshot(self) -> None:
        try:
            self.snapshot_path.unlink(missing_ok=True)
        except OSError as exc:
            raise CacheError("无法清除旧用量缓存") from exc

    def load_credentials(self, provider: str = DEFAULT_PROVIDER) -> Credentials | None:
        ak_name, sk_name = self._credential_entries(provider)
        try:
            ak = self._keyring.get_password(KEYRING_SERVICE, ak_name)
            sk = self._keyring.get_password(KEYRING_SERVICE, sk_name)
        except Exception as exc:
            raise ConfigurationError(f"无法读取{self.credential_store_name}") from exc
        if not ak or not sk:
            return None
        return Credentials(access_key_id=ak, secret_access_key=sk)

    def save_credentials(
        self, credentials: Credentials, provider: str = DEFAULT_PROVIDER
    ) -> None:
        """Save only this provider's two credential entries in the OS vault."""
        ak_name, sk_name = self._credential_entries(provider)
        try:
            # Secret first: a partial write then leaves the pair unusable rather
            # than pairing a new AccessKey ID with a stale secret.
            self._keyring.set_password(
                KEYRING_SERVICE, sk_name, credentials.secret_access_key
            )
            self._keyring.set_password(KEYRING_SERVICE, ak_name, credentials.access_key_id)
        except Exception as exc:
            raise ConfigurationError(f"无法保存到{self.credential_store_name}") from exc

    @staticmethod
    def _credential_entries(provider: str) -> tuple[str, str]:
        spec = get_provider(provider)
        return spec.access_key_username, spec.secret_key_username

    def load_inference_key(self, provider: str = DEFAULT_PROVIDER) -> str | None:
        """Read the vault entry holding the inference key, if any.

        Kept separate from `load_credentials`: control-plane AK/SK cannot call
        the model API, and an inference key must never be sent to the OpenAPI.
        """
        username = get_provider(provider).inference_key_username
        try:
            value = self._keyring.get_password(KEYRING_SERVICE, username)
        except Exception as exc:
            raise ConfigurationError(f"无法读取{self.credential_store_name}") from exc
        return value.strip() if isinstance(value, str) and value.strip() else None

    def save_inference_key(self, api_key: str, provider: str = DEFAULT_PROVIDER) -> None:
        username = get_provider(provider).inference_key_username
        secret = api_key.strip()
        if not secret:
            raise ConfigurationError("推理 API Key 不能为空")
        try:
            self._keyring.set_password(KEYRING_SERVICE, username, secret)
        except Exception as exc:
            raise ConfigurationError(f"无法保存到{self.credential_store_name}") from exc

    def load_claude_prefs(self, provider: str) -> ClaudeProviderPrefs:
        value = self._read_json(self.claude_path)
        if value is None:
            return ClaudeProviderPrefs()
        try:
            if value.get("schema_version") != SCHEMA_VERSION:
                return ClaudeProviderPrefs()
            providers = value.get("providers")
            if not isinstance(providers, dict):
                return ClaudeProviderPrefs()
            entry = providers.get(provider, {})
            return ClaudeProviderPrefs.from_dict(entry if entry is not None else {})
        except (KeyError, TypeError, ValueError):
            return ClaudeProviderPrefs()

    def save_claude_prefs(self, prefs: ClaudeProviderPrefs, provider: str) -> None:
        get_provider(provider)  # reject an unknown id before touching the file
        stored = self._read_json(self.claude_path) or {}
        existing = stored.get("providers")
        providers = dict(existing) if isinstance(existing, dict) else {}
        providers[provider] = prefs.to_dict()
        self._write_json(
            self.claude_path,
            {"schema_version": SCHEMA_VERSION, "providers": providers},
        )

    def _read_json(self, path: Path) -> dict[str, Any] | None:
        try:
            with path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def _write_json(self, path: Path, value: dict[str, Any]) -> None:
        temporary = path.with_name(f"{path.name}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise CacheError(f"无法写入本地文件：{path.name}") from exc
