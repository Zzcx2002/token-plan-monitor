from __future__ import annotations

import json
import os
import math
from pathlib import Path
from typing import Any

import keyring

from .appearance import AppearancePreferences

from .models import (
    SCHEMA_VERSION,
    CacheError,
    ConfigurationError,
    Credentials,
    Settings,
    UsageSnapshot,
    WidgetPreferences,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
KEYRING_SERVICE = "TokenPlanMonitor"
AK_USERNAME = "volcengine_access_key_id"
SK_USERNAME = "volcengine_secret_access_key"


class Storage:
    def __init__(self, data_dir: Path | None = None, keyring_backend: Any = None):
        self.data_dir = data_dir or DEFAULT_DATA_DIR
        self.config_path = self.data_dir / "config.json"
        self.snapshot_path = self.data_dir / "snapshot.json"
        self.widget_path = self.data_dir / "widget.json"
        self.geometry_path = self.data_dir / "window_geometry.json"
        self.appearance_path = self.data_dir / "appearance.json"
        self._keyring = keyring_backend or keyring

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

    def load_credentials(self) -> Credentials | None:
        try:
            ak = self._keyring.get_password(KEYRING_SERVICE, AK_USERNAME)
            sk = self._keyring.get_password(KEYRING_SERVICE, SK_USERNAME)
        except Exception as exc:
            raise ConfigurationError("无法读取 Windows 凭据管理器") from exc
        if not ak or not sk:
            return None
        return Credentials(access_key_id=ak, secret_access_key=sk)

    def save_credentials(self, credentials: Credentials) -> None:
        """Save only the two user-authorized Windows credential entries."""
        try:
            self._keyring.set_password(
                KEYRING_SERVICE, SK_USERNAME, credentials.secret_access_key
            )
            self._keyring.set_password(
                KEYRING_SERVICE, AK_USERNAME, credentials.access_key_id
            )
        except Exception as exc:
            raise ConfigurationError("无法保存到 Windows 凭据管理器") from exc

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
