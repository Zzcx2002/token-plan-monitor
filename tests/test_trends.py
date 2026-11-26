from datetime import UTC, datetime, timedelta

import pytest

from token_plan_monitor.history import HistoryEntry
from token_plan_monitor.models import QuotaWindow, UsageSnapshot
from token_plan_monitor.trends import compute_trend, trends_for_snapshot

BASE_TIME = datetime(2026, 11, 16, 8, tzinfo=UTC)
ACCOUNT = "a1b2c3d4e5f6"
NOW = BASE_TIME + timedelta(minutes=60)


def entry(minutes, percent, kind="five_hour", reset=None, quota=None, used=None):
    return HistoryEntry(
        queried_at=BASE_TIME + timedelta(minutes=minutes),
        account=ACCOUNT,
        plan_type="agent_plan",
        windows=(QuotaWindow(kind, percent, reset, quota, used),),
    )


def linear_series(reset=None, quota=None):
    # 2 percentage points per 15 minutes = 8%/h, ending at 18% used.
    return [
        entry(minutes, 10 + index * 2, reset=reset,
              quota=quota, used=(10 + index * 2) if quota is not None else None)
        for index, minutes in enumerate((0, 15, 30, 45, 60))
    ]


def test_linear_series_yields_the_exact_rate_and_exhaustion():
    reset = BASE_TIME + timedelta(hours=20)
    trend = compute_trend(linear_series(reset=reset), "five_hour", NOW, reset)
    assert trend.sample_count == 5
    assert trend.span_seconds == 3600
    assert trend.percent_per_hour == pytest.approx(8.0)
    # 82% remaining at 8%/h -> exhausted 10.25h from now, before the 20h reset.
    assert abs((trend.exhausted_at - (NOW + timedelta(hours=10.25))).total_seconds()) < 1
    assert trend.resets_before_exhaustion is False


def test_reset_before_exhaustion_is_reassuring():
    reset = BASE_TIME + timedelta(hours=2)
    trend = compute_trend(linear_series(reset=reset), "five_hour", NOW, reset)
    assert abs((trend.exhausted_at - (NOW + timedelta(hours=10.25))).total_seconds()) < 1
    assert trend.resets_before_exhaustion is True


def test_unknown_reset_leaves_the_comparison_open():
    trend = compute_trend(linear_series(), "five_hour", NOW, None)
    assert trend.exhausted_at is not None
    assert trend.resets_before_exhaustion is None


def test_cycle_cut_by_a_changed_reset_at():
    old_reset = BASE_TIME + timedelta(hours=1)
    new_reset = BASE_TIME + timedelta(hours=5)
    entries = [
        entry(0, 50, reset=old_reset),
        entry(15, 55, reset=old_reset),
        entry(30, 5, reset=new_reset),
        entry(45, 10, reset=new_reset),
    ]
    trend = compute_trend(entries, "five_hour", NOW, new_reset)
    # Last cycle's 50/55% must not inflate this cycle's slope.
    assert trend.sample_count == 2
    assert trend.percent_per_hour == pytest.approx(20.0)


def test_cycle_cut_by_a_usage_jump_when_reset_at_is_missing():
    entries = [
        entry(0, 80),          # previous cycle, nearly exhausted
        entry(15, 5),          # reset happened between these rows
        entry(30, 7),
        entry(45, 9),
    ]
    trend = compute_trend(entries, "five_hour", NOW, None)
    assert trend.sample_count == 3
    assert trend.percent_per_hour == pytest.approx(8.0)


def test_small_backward_wobble_stays_inside_the_cycle():
    entries = [entry(0, 10.0), entry(15, 10.5), entry(30, 11.0), entry(45, 12.0)]
    trend = compute_trend(entries, "five_hour", NOW, None)
    assert trend.sample_count == 4


def test_thin_evidence_reports_no_rate():
    assert compute_trend([], "five_hour", NOW, None).percent_per_hour is None
    assert compute_trend([entry(0, 10)], "five_hour", NOW, None).percent_per_hour is None
    short = compute_trend([entry(0, 10), entry(5, 12)], "five_hour", NOW, None)
    assert short.sample_count == 2
    assert short.span_seconds == 300
    assert short.percent_per_hour is None
    assert short.exhausted_at is None


def test_idle_and_receding_usage_never_predict_exhaustion():
    idle = compute_trend([entry(0, 30), entry(30, 30), entry(60, 30)], "five_hour", NOW, None)
    assert idle.percent_per_hour == 0.0
    assert idle.exhausted_at is None
    receding = compute_trend([entry(0, 12), entry(30, 11), entry(60, 10)], "five_hour", NOW, None)
    assert receding.percent_per_hour < 0
    assert receding.exhausted_at is None


def test_absolute_rate_requires_every_sample_to_carry_absolute_numbers():
    complete = compute_trend(linear_series(quota=100.0), "five_hour", NOW, None)
    assert complete.absolute_per_hour == pytest.approx(8.0)
    mixed = linear_series(quota=100.0)
    mixed[2] = entry(30, 14)  # one sample without quota/used
    assert compute_trend(mixed, "five_hour", NOW, None).absolute_per_hour is None
    percent_only = compute_trend(linear_series(), "five_hour", NOW, None)
    assert percent_only.percent_per_hour == pytest.approx(8.0)
    assert percent_only.absolute_per_hour is None


def test_rows_missing_the_window_are_skipped_not_fatal():
    entries = [entry(0, 10, kind="weekly"), entry(30, 14), entry(60, 18)]
    trend = compute_trend(entries, "five_hour", NOW, None)
    assert trend.sample_count == 2
    assert trend.percent_per_hour == pytest.approx(8.0)


def test_trends_for_snapshot_covers_every_reported_window():
    snapshot = UsageSnapshot(
        plan_type="agent_plan",
        queried_at=NOW,
        windows=(
            QuotaWindow("five_hour", 18, BASE_TIME + timedelta(hours=4)),
            QuotaWindow("weekly", 60),
        ),
    )
    entries = linear_series() + [entry(60, 60, kind="weekly")]
    trends = trends_for_snapshot(entries, snapshot)
    assert [trend.kind for trend in trends] == ["five_hour", "weekly"]
    assert trends[0].percent_per_hour == pytest.approx(8.0)
    # A single weekly observation cannot produce a rate.
    assert trends[1].percent_per_hour is None


def test_forecast_uses_snapshot_time_when_history_dedupes_the_observation():
    entries = [entry(0, 60), entry(60, 80)]
    observed_at = NOW + timedelta(minutes=5)
    snapshot = UsageSnapshot("agent_plan", observed_at, entries[-1].windows)

    trend, = trends_for_snapshot(entries, snapshot)

    assert trend.sample_count == 2
    assert trend.exhausted_at == observed_at + timedelta(hours=1)


def test_forecast_uses_snapshot_remaining_and_ignores_newer_history():
    entries = [entry(0, 60), entry(30, 70), entry(80, 99)]
    snapshot = UsageSnapshot("agent_plan", NOW, (QuotaWindow("five_hour", 80),))

    trend, = trends_for_snapshot(entries, snapshot)

    assert trend.sample_count == 2
    assert trend.percent_per_hour == pytest.approx(20.0)
    assert trend.exhausted_at == NOW + timedelta(hours=1)


def test_unrecorded_snapshot_with_a_usage_drop_excludes_the_old_cycle():
    entries = [entry(0, 60), entry(30, 70)]
    snapshot = UsageSnapshot("agent_plan", NOW, (QuotaWindow("five_hour", 5),))

    trend, = trends_for_snapshot(entries, snapshot)

    assert trend.percent_per_hour is None
    assert trend.exhausted_at is None
