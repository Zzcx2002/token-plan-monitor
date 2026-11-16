import json
from datetime import UTC, datetime, timedelta

import pytest

from token_plan_monitor.history import (
    HISTORY_VERSION,
    HistoryEntry,
    account_fingerprint,
    entry_from_snapshot,
    parse_line,
    prune,
    should_append,
)
from token_plan_monitor.models import QuotaWindow, UsageSnapshot

BASE_TIME = datetime(2026, 11, 16, 8, tzinfo=UTC)
ACCOUNT = "a1b2c3d4e5f6"


def make_entry(
    percent=25.0,
    minutes=0,
    account=ACCOUNT,
    reset=None,
    quota=None,
    used=None,
):
    return HistoryEntry(
        queried_at=BASE_TIME + timedelta(minutes=minutes),
        account=account,
        plan_type="agent_plan",
        windows=(
            QuotaWindow(
                kind="five_hour",
                used_percent=percent,
                reset_at=reset,
                quota=quota,
                used=used,
            ),
        ),
    )


def test_line_round_trip_preserves_every_reported_number():
    original = make_entry(
        percent=28.5,
        reset=BASE_TIME + timedelta(hours=4),
        quota=100.0,
        used=28.5,
    )
    parsed = parse_line(original.to_line())
    assert parsed == original
    # The row carries the row version, not the global config schema version.
    assert json.loads(original.to_line())["v"] == HISTORY_VERSION


def test_parse_line_skips_malformed_and_unknown_rows():
    bad_rows = [
        "",
        "not json",
        "[]",
        json.dumps({"v": HISTORY_VERSION + 1, "t": "2026-11-16T08:00:00Z"}),
        make_entry().to_line().replace('"v":1', '"v":"1"'),
        # Missing required keys.
        json.dumps({"v": 1, "account": ACCOUNT, "plan_type": "agent_plan"}),
        # Naive timestamp.
        make_entry().to_line().replace("2026-11-16T08:00:00Z", "2026-11-16T08:00:00"),
        # Bad account shape / plan type / window.
        make_entry().to_line().replace(ACCOUNT, "not-a-fingerprint"),
        make_entry().to_line().replace('"agent_plan"', '"mystery_plan"'),
        make_entry().to_line().replace('"five_hour"', '"yearly"'),
    ]
    for row in bad_rows:
        assert parse_line(row) is None, row
    empty_windows = json.loads(make_entry().to_line())
    empty_windows["windows"] = {}
    assert parse_line(json.dumps(empty_windows)) is None
    list_windows = json.loads(make_entry().to_line())
    list_windows["windows"] = []
    assert parse_line(json.dumps(list_windows)) is None


def test_account_fingerprint_is_stable_one_way_and_short():
    first = account_fingerprint("AKLT-example")
    assert first == account_fingerprint("AKLT-example")
    assert len(first) == 12
    assert all(char in "0123456789abcdef" for char in first)
    assert first != account_fingerprint("AKLT-other")
    assert "AKLT-example" not in first


def test_entry_from_snapshot_maps_the_reported_fields():
    snapshot = UsageSnapshot(
        plan_type="coding_plan",
        plan_name="Coding Plan",
        queried_at=BASE_TIME,
        windows=(QuotaWindow(kind="weekly", used_percent=60.0, quota=500, used=300),),
    )
    entry = entry_from_snapshot(snapshot, ACCOUNT)
    assert entry.queried_at == snapshot.queried_at
    assert entry.plan_type == "coding_plan"
    assert entry.windows == snapshot.windows
    assert entry.account == ACCOUNT


def test_should_append_records_changes_immediately_and_dedupes_idle_repeats():
    first = make_entry()
    assert should_append(None, first) is True
    # Identical values within the gap: skip (manual refresh spam).
    assert should_append(first, make_entry(minutes=5)) is False
    # Identical values after the gap: keep a heartbeat row.
    assert should_append(first, make_entry(minutes=15)) is True
    # Any value change is recorded regardless of the gap.
    assert should_append(first, make_entry(percent=26.0, minutes=1)) is True
    assert (
        should_append(first, make_entry(minutes=1, reset=BASE_TIME + timedelta(hours=4)))
        is True
    )


def test_prune_enforces_age_and_count_caps():
    now = BASE_TIME
    old = make_entry(minutes=-100 * 24 * 60)  # 100 days ago
    recent = [make_entry(minutes=index) for index in range(5)]
    kept = prune([old] + recent, now)
    assert old not in kept
    assert kept == recent
    # Count cap keeps the newest rows.
    assert prune(recent, now, max_rows=3) == recent[-3:]
    # Both caps together.
    assert prune([old] + recent, now, max_rows=2) == recent[-2:]
    assert prune([], now) == []


def test_entry_validation_rejects_bad_shapes():
    with pytest.raises(ValueError):
        HistoryEntry(
            queried_at=datetime(2026, 11, 16, 8), account=ACCOUNT,
            plan_type="agent_plan", windows=(QuotaWindow(kind="five_hour", used_percent=1),),
        )
    with pytest.raises(ValueError):
        make_entry(account="ABC")
    with pytest.raises(ValueError):
        HistoryEntry(
            queried_at=BASE_TIME, account=ACCOUNT, plan_type="agent_plan", windows=(),
        )