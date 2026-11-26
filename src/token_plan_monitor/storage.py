from __future__ import annotations

import json
import logging
import math
import os
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

import keyring
from .paths import data_directory

from .appearance import AppearancePreferences
from .history import (
    HISTORY_MAX_ROWS,
    HistoryEntry,
    QuotaSource,
    parse_line,
    prune,
    should_append,
)

from .models import (
    DEFAULT_PROVIDER,
    MODEL_ID_PATTERN,
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

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = data_directory()
KEYRING_SERVICE = "TokenPlanMonitor"


class Storage:
    def __init__(self, data_dir: Path | None = None, keyring_backend: Any = None):
        self.data_dir = data_dir or DEFAULT_DATA_DIR
        self.config_path = self.data_dir / "config.json"
        self.widget_path = self.data_dir / "widget.json"
        self.geometry_path = self.data_dir / "window_geometry.json"
        self.appearance_path = self.data_dir / "appearance.json"
        self.claude_path = self.data_dir / "claude.json"
        self.models_path = self.data_dir / "models.json"
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
        except (ConfigurationError, KeyError, TypeError, ValueError) as exc:
            logger.warning("Could not load settings (%s); using defaults", exc)
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

    def snapshot_path(self, source: QuotaSource) -> Path:
        return self.data_dir / "snapshots" / source.provider / source.region / f"{source.account}.json"

    def load_snapshot(self, source: QuotaSource) -> UsageSnapshot | None:
        value = self._read_json(self.snapshot_path(source))
        if value is None:
            return None
        try:
            if value.get("schema_version") != SCHEMA_VERSION or value.get("source") != asdict(source):
                return None
            return UsageSnapshot.from_dict(value.get("snapshot"))
        except (KeyError, TypeError, ValueError):
            return None

    def save_snapshot(self, snapshot: UsageSnapshot, source: QuotaSource, *, commit=os.replace) -> None:
        self._write_json(self.snapshot_path(source), {
            "schema_version": SCHEMA_VERSION, "source": asdict(source), "snapshot": snapshot.to_dict(),
        }, commit=commit)

    def clear_snapshot(self, source: QuotaSource) -> None:
        try:
            self.snapshot_path(source).unlink(missing_ok=True)
        except OSError as exc:
            raise CacheError("无法清除旧用量缓存") from exc

    def history_path(self, provider: str) -> Path:
        get_provider(provider)  # reject an unknown id before touching the file
        return self.data_dir / "history" / f"{provider}.jsonl"

    def append_history(self, entry: HistoryEntry, provider: str) -> list[HistoryEntry]:
        """Append one observation and return the retained rows (all accounts).

        Dedupes unchanged values and enforces BOTH retention caps on every
        call -- the 90-day age cap must not wait for the 10k row cap to
        trigger, or low-frequency history keeps stale rows forever. The single
        read serves dedup AND the returned list, so the monitor never parses
        the file twice per refresh. History is an observation log, not the
        UI's source of truth: callers treat CacheError here as a warning,
        never as a refresh failure. Switching provider or account never
        clears it.
        """
        path = self.history_path(provider)
        try:
            entries = self._read_history(path)
            last = next(
                (item for item in reversed(entries) if item.account == entry.account),
                None,
            )
            if should_append(last, entry):
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a+b") as handle:
                    handle.seek(0, os.SEEK_END)
                    if handle.tell() > 0:
                        handle.seek(-1, os.SEEK_END)
                        if handle.read(1) != b"\n":
                            # A previous append died mid-row: terminate the torn
                            # line so the new row never glues onto it.
                            handle.write(b"\n")
                    handle.write((entry.to_line() + "\n").encode("utf-8"))
                    handle.flush()
                    os.fsync(handle.fileno())
                entries.append(entry)
            # Pass the cap explicitly: prune's default binds at definition
            # time in history.py and would ignore this module's value.
            kept = prune(entries, entry.queried_at, max_rows=HISTORY_MAX_ROWS)
            if len(kept) != len(entries):
                self._write_history(path, kept)
            return kept
        except OSError as exc:
            raise CacheError(f"无法写入用量历史：{path.name}") from exc

    def load_history(
        self, provider: str, account: str | None = None, limit: int = 2000
    ) -> list[HistoryEntry]:
        """Newest observations for a provider, optionally one account only.

        Malformed rows are skipped, never fatal: a torn tail from a crash must
        not hide the healthy rows before it.
        """
        path = self.history_path(provider)
        try:
            entries = self._read_history(path, account)
        except OSError as exc:
            raise CacheError(f"无法读取用量历史：{path.name}") from exc
        return entries[-limit:] if limit > 0 else []

    def _read_history(self, path: Path, account: str | None = None) -> list[HistoryEntry]:
        try:
            # errors="replace": invalid UTF-8 bytes become replacement chars
            # and parse_line skips that row, instead of poisoning the read.
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                lines = handle.readlines()
        except FileNotFoundError:
            if path.parent.exists() and not path.parent.is_dir():
                # A path component is a regular file. POSIX raises
                # NotADirectoryError here; Windows maps the same fault to
                # FileNotFoundError, so re-raise to keep the CacheError
                # contract identical across platforms.
                raise
            return []
        entries = []
        for line in lines:
            entry = parse_line(line)
            if entry is not None and (account is None or entry.account == account):
                entries.append(entry)
        return entries

    def _write_history(self, path: Path, entries: list[HistoryEntry]) -> None:
        # Unique temp name: a fixed ".tmp" would let concurrent writers (or a
        # crash leftover) clobber or block the rotation rewrite.
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent, prefix=f"{path.name}.", suffix=".tmp"
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                for entry in entries:
                    handle.write(entry.to_line() + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise CacheError(f"无法写入用量历史：{path.name}") from exc

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

    def load_model_catalog(self, provider: str) -> tuple[str, ...]:
        """Cached live model ids for a provider; empty when absent or corrupt.

        Non-secret by construction (model ids only), so unlike the inference key
        this lives in a plain JSON file. A malformed cache is ignored, never
        repaired by writing: the caller refetches and overwrites it.
        """
        value = self._read_json(self.models_path)
        if value is None:
            return ()
        try:
            if value.get("schema_version") != SCHEMA_VERSION:
                return ()
            providers = value.get("providers")
            if not isinstance(providers, dict):
                return ()
            entry = providers.get(provider)
            if not isinstance(entry, dict):
                return ()
            ids = entry.get("ids")
            if not isinstance(ids, list):
                return ()
        except (KeyError, TypeError, ValueError):
            return ()
        clean = []
        for model_id in ids:
            if (isinstance(model_id, str) and model_id
                    and MODEL_ID_PATTERN.fullmatch(model_id) is not None
                    and model_id not in clean):
                clean.append(model_id)
        return tuple(clean)

    def save_model_catalog(self, provider: str, ids: tuple[str, ...]) -> None:
        get_provider(provider)  # reject an unknown id before touching the file
        stored = self._read_json(self.models_path) or {}
        existing = stored.get("providers")
        providers = dict(existing) if isinstance(existing, dict) else {}
        providers[provider] = {"ids": list(ids)}
        self._write_json(
            self.models_path,
            {"schema_version": SCHEMA_VERSION, "providers": providers},
        )

    def _read_json(self, path: Path) -> dict[str, Any] | None:
        try:
            with path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
        except FileNotFoundError:
            # A missing file is a normal first run, not a fault.
            return None
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            # Corrupt is different from missing: say so, instead of silently
            # resetting whatever this file backs.
            logger.warning("Could not read %s (%s); treating as absent", path.name, exc)
            return None
        if not isinstance(value, dict):
            logger.warning("Unexpected JSON shape in %s; treating as absent", path.name)
            return None
        return value

    def _write_json(self, path: Path, value: dict[str, Any], *,
                    commit: Callable[[Path, Path], bool | None] = os.replace) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Unique temp name, same rationale as _write_history.
            descriptor, temporary_name = tempfile.mkstemp(
                dir=path.parent, prefix=f"{path.name}.", suffix=".tmp"
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                    json.dump(value, handle, ensure_ascii=False, indent=2)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                if commit(temporary, path) is False:
                    temporary.unlink()
            except OSError:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
        except OSError as exc:
            raise CacheError(f"无法写入本地文件：{path.name}") from exc
