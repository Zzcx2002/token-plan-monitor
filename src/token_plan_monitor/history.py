"""Append-only usage history: the row contract and the pure record rules.

Storage owns the JSONL files; this module owns what a row means, when a new
observation is worth recording, and how retention pruning works -- the same
pure-rules/IO split as ``macos/layout.py``, so every rule is testable offline.

Rows carry only numbers the provider API already reported (percentages and,
when present, absolute quota) plus a one-way fingerprint of the AccessKey ID.
No credential material ever reaches a history file.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from .models import (
    PLAN_TYPES,
    QuotaWindow,
    UsageSnapshot,
    datetime_from_iso,
    datetime_to_iso,
)

#: Row-level version, independent of the global config SCHEMA_VERSION: an
#: unknown row version is skipped on read, never treated as a reset trigger.
HISTORY_VERSION = 1
HISTORY_MAX_AGE_DAYS = 90
HISTORY_MAX_ROWS = 10_000
#: When nothing changed, record at most one row per this interval so manual
#: refresh spam cannot flood the file. A changed value is always recorded.
HISTORY_MIN_GAP_SECONDS = 15 * 60

_ACCOUNT_PATTERN = re.compile(r"[0-9a-f]{12}")


def account_fingerprint(access_key_id: str) -> str:
    """One-way, stable account label for history rows (not a secret)."""
    return hashlib.sha256(access_key_id.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class HistoryEntry:
    queried_at: datetime
    account: str
    plan_type: str
    windows: tuple[QuotaWindow, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.queried_at, datetime) or self.queried_at.tzinfo is None:
            raise ValueError("History time must be timezone-aware")
        if not isinstance(self.account, str) or _ACCOUNT_PATTERN.fullmatch(self.account) is None:
            raise ValueError("History account must be a 12-hex fingerprint")
        if self.plan_type not in PLAN_TYPES:
            raise ValueError(f"Unknown plan type: {self.plan_type}")
        if not self.windows:
            raise ValueError("A history entry must contain at least one quota window")

    def to_line(self) -> str:
        """One JSONL row (without the trailing newline)."""
        return json.dumps(
            {
                "v": HISTORY_VERSION,
                "t": datetime_to_iso(self.queried_at),
                "account": self.account,
                "plan_type": self.plan_type,
                "windows": {window.kind: window.to_dict() for window in self.windows},
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )


def entry_from_snapshot(snapshot: UsageSnapshot, account: str) -> HistoryEntry:
    return HistoryEntry(
        queried_at=snapshot.queried_at,
        account=account,
        plan_type=snapshot.plan_type,
        windows=snapshot.windows,
    )


def parse_line(line: str) -> HistoryEntry | None:
    """Tolerant row reader: any malformed or unknown-version row is skipped."""
    try:
        value = json.loads(line)
        if not isinstance(value, dict) or value.get("v") != HISTORY_VERSION:
            return None
        windows_raw = value.get("windows")
        if not isinstance(windows_raw, dict) or not windows_raw:
            return None
        return HistoryEntry(
            queried_at=datetime_from_iso(value["t"]),
            account=value["account"],
            plan_type=value["plan_type"],
            windows=tuple(QuotaWindow.from_dict(item) for item in windows_raw.values()),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _values_signature(entry: HistoryEntry) -> tuple:
    return tuple(
        (window.kind, window.used_percent, window.reset_at)
        for window in sorted(entry.windows, key=lambda window: window.kind)
    )


def should_append(
    last: HistoryEntry | None,
    new: HistoryEntry,
    min_gap_seconds: int = HISTORY_MIN_GAP_SECONDS,
) -> bool:
    """Record every change immediately; dedupe only identical observations.

    ``last`` is the most recent row *for the same account* -- observations from
    another account never suppress this one.
    """
    if last is None:
        return True
    if _values_signature(last) != _values_signature(new):
        return True
    return (new.queried_at - last.queried_at).total_seconds() >= min_gap_seconds


def prune(
    entries: list[HistoryEntry],
    now: datetime,
    max_age_days: int = HISTORY_MAX_AGE_DAYS,
    max_rows: int = HISTORY_MAX_ROWS,
) -> list[HistoryEntry]:
    """Retention: drop rows older than the age cap, then keep the newest rows
    up to the count cap. Input order is preserved for the surviving rows."""
    cutoff = now - timedelta(days=max_age_days)
    kept = [entry for entry in entries if entry.queried_at >= cutoff]
    if len(kept) > max_rows:
        kept = kept[-max_rows:]
    return kept