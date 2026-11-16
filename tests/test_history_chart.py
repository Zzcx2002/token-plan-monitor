from datetime import UTC, datetime, timedelta

import pytest

from token_plan_monitor.history import HistoryEntry
from token_plan_monitor.history_chart import (
    BUCKET_PIXELS,
    MAX_RESET_MARKS,
    PLOT_HEIGHT,
    RANGE_DELTAS,
    RANGE_KEYS,
    build_chart,
    bucket_cycle,
    nearest_sample,
    split_cycles,
    time_ticks,
    window_samples,
    x_scale,
    y_scale,
)
from token_plan_monitor.models import QuotaWindow
from token_plan_monitor.trends import _cycle_samples

BASE_TIME = datetime(2026, 11, 16, 8, tzinfo=UTC)
ACCOUNT = "a1b2c3d4e5f6"
NOW = BASE_TIME + timedelta(hours=24)
WIDTH = 400.0


def entry(minutes, percent, kind="five_hour", reset=None, quota=None, used=None):
    return HistoryEntry(
        queried_at=BASE_TIME + timedelta(minutes=minutes),
        account=ACCOUNT,
        plan_type="agent_plan",
        windows=(QuotaWindow(kind, percent, reset, quota, used),),
    )


def chart(entries, kind="five_hour", range_key="24h", width=WIDTH, threshold=10):
    return build_chart(
        entries, kind, now=NOW, range_key=range_key,
        plot_width=width, alert_threshold_percent=threshold,
    )


def test_window_samples_filters_kind_and_range_and_sorts():
    entries = [
        entry(60, 30),                                   # in range
        entry(0, 10),                                    # in range, older
        entry(-60, 5),                                   # before range start
        entry(60, 99, kind="weekly"),                    # other kind
        entry(24 * 60 + 15, 90),                         # after range end
    ]
    samples = window_samples(entries, "five_hour", NOW - timedelta(hours=24), NOW)
    assert [s.used_percent for s in samples] == [10.0, 30.0]
    assert samples[0].queried_at < samples[1].queried_at


def test_split_cycles_cuts_on_a_changed_reset_at():
    old_reset = BASE_TIME + timedelta(hours=5)
    new_reset = BASE_TIME + timedelta(hours=10)
    samples = window_samples(
        [entry(0, 50, reset=old_reset), entry(15, 55, reset=old_reset),
         entry(30, 5, reset=new_reset), entry(45, 10, reset=new_reset)],
        "five_hour", NOW - timedelta(hours=24), NOW,
    )
    cycles = split_cycles(samples)
    assert len(cycles) == 2
    assert [s.used_percent for s in cycles[0]] == [50.0, 55.0]
    assert [s.used_percent for s in cycles[1]] == [5.0, 10.0]


def test_split_cycles_cuts_on_a_forward_drop_but_not_a_small_wobble():
    dropped = window_samples(
        [entry(0, 80), entry(15, 5), entry(30, 7)],
        "five_hour", NOW - timedelta(hours=24), NOW,
    )
    assert len(split_cycles(dropped)) == 2
    wobble = window_samples(
        [entry(0, 10.0), entry(15, 10.5), entry(30, 10.2), entry(45, 11.0)],
        "five_hour", NOW - timedelta(hours=24), NOW,
    )
    # A 0.3-point backward wobble is within the 1% tolerance: one cycle.
    assert len(split_cycles(wobble)) == 1


def test_last_cycle_matches_the_trends_current_cycle_slice():
    old_reset = BASE_TIME + timedelta(hours=1)
    new_reset = BASE_TIME + timedelta(hours=5)
    entries = [
        entry(0, 50, reset=old_reset),
        entry(15, 55, reset=old_reset),
        entry(30, 5, reset=new_reset),
        entry(45, 10, reset=new_reset),
        entry(60, 15, reset=new_reset),
    ]
    geometry = chart(entries)
    trend_slice = _cycle_samples(entries, "five_hour", new_reset)
    chart_last = [(s.queried_at, s.used_percent) for s in geometry.cycles[-1].samples]
    assert chart_last == [(t, percent) for t, percent, _, _ in trend_slice]


def test_three_cycles_across_a_long_range_are_all_preserved():
    entries = []
    for cycle in range(3):
        reset = BASE_TIME + timedelta(hours=5 * (cycle + 1))
        for step in range(4):
            entries.append(entry(cycle * 300 + step * 15, 10 + step * 5, reset=reset))
    geometry = chart(entries, range_key="7d")
    assert len(geometry.cycles) == 3
    assert geometry.empty_reason is None
    # Every cycle keeps its own rising shape; no cross-reset connection.
    for cycle in geometry.cycles:
        percents = [s.used_percent for s in cycle.samples]
        assert percents == sorted(percents)


def test_bucket_cycle_keeps_the_min_and_max_in_time_order():
    origin = BASE_TIME
    samples = window_samples(
        [entry(0, 20), entry(1, 80), entry(2, 10), entry(3, 40)],
        "five_hour", NOW - timedelta(hours=24), NOW,
    )
    # One bucket covering all four samples: min (10 @ +2m) and max (80 @ +1m)
    # survive, emitted in time order so the earlier extreme comes first.
    points = bucket_cycle(samples, origin, bucket_seconds=3600)
    assert points == (
        (BASE_TIME + timedelta(minutes=1), 80.0),
        (BASE_TIME + timedelta(minutes=2), 10.0),
    )


def test_reset_inside_one_pixel_bucket_still_yields_two_cycles():
    # A tiny plot makes the bucket wider than the gap around the reset.
    entries = [entry(0, 80), entry(15, 5), entry(30, 9)]
    geometry = chart(entries, width=4.0)
    assert len(geometry.cycles) == 2
    assert geometry.cycles[0].points[-1][1] == pytest.approx(y_scale(80, PLOT_HEIGHT))
    assert geometry.cycles[1].points[0][1] == pytest.approx(y_scale(5, PLOT_HEIGHT))


def test_bucket_count_is_bounded_per_cycle():
    entries = [entry(minutes, 10 + minutes / 60) for minutes in range(0, 24 * 60, 5)]
    geometry = chart(entries)
    buckets = int(WIDTH / BUCKET_PIXELS) + 1
    # One table row per bucket; at most two draw points (min+max) per bucket.
    assert len(geometry.rows) <= buckets
    for cycle in geometry.cycles:
        assert len(cycle.points) <= 2 * buckets


def test_scales_map_edges_and_clamp_for_drawing_only():
    start, end = NOW - timedelta(hours=24), NOW
    assert x_scale(start, start, end, WIDTH) == pytest.approx(0.0)
    assert x_scale(end, start, end, WIDTH) == pytest.approx(WIDTH)
    assert y_scale(0, PLOT_HEIGHT) == pytest.approx(PLOT_HEIGHT)
    assert y_scale(100, PLOT_HEIGHT) == pytest.approx(0.0)
    assert y_scale(50, PLOT_HEIGHT) == pytest.approx(PLOT_HEIGHT / 2)
    assert y_scale(120, PLOT_HEIGHT) == pytest.approx(0.0)
    assert y_scale(-5, PLOT_HEIGHT) == pytest.approx(PLOT_HEIGHT)
    # The sample itself keeps the reported out-of-range value.
    geometry = chart([entry(0, 120), entry(15, 130)])
    assert geometry.samples[-1].used_percent == 130
    assert geometry.cycles[0].points[-1][1] == pytest.approx(0.0)


def test_time_ticks_step_and_count_per_range():
    expected = {"24h": 7, "7d": 8, "30d": 7, "90d": 7}
    for key in RANGE_KEYS:
        start = NOW - RANGE_DELTAS[key]
        ticks = time_ticks(start, NOW, key)
        assert len(ticks) == expected[key]
        assert ticks[-1] == NOW
        assert ticks[0] >= start
        assert ticks == tuple(sorted(ticks))


def test_nearest_sample_snaps_and_respects_the_plot_bounds():
    entries = [entry(0, 10), entry(60, 20), entry(120, 30)]
    geometry = chart(entries)
    x_mid = x_scale(BASE_TIME + timedelta(minutes=60),
                    geometry.range_start, geometry.range_end, WIDTH)
    assert nearest_sample(geometry, x_mid).used_percent == 20
    # Just left of the middle sample snaps to it, not to the first.
    assert nearest_sample(geometry, x_mid - 1).used_percent == 20
    assert nearest_sample(geometry, -1) is None
    assert nearest_sample(geometry, WIDTH + 1) is None
    assert nearest_sample(chart([]), WIDTH / 2) is None


def test_thin_evidence_is_honest():
    assert chart([]).empty_reason == "no_history"
    assert chart([]).cycles == ()
    assert chart([]).latest is None
    thin = chart([entry(0, 10)])
    assert thin.empty_reason == "thin"
    assert thin.sample_count == 1
    assert thin.latest.used_percent == 10
    ok = chart([entry(0, 10), entry(15, 12)])
    assert ok.empty_reason is None


def test_percent_only_never_invents_absolute_numbers():
    geometry = chart([entry(0, 10), entry(15, 12), entry(30, 14)])
    assert geometry.has_absolute is False
    assert all(row.quota is None and row.used is None for row in geometry.rows)
    assert all(s.quota is None and s.used is None for s in geometry.samples)
    mixed = chart([entry(0, 10, quota=100, used=10), entry(15, 12)])
    assert mixed.has_absolute is True
    # The row without reported absolutes stays None-honest.
    assert any(row.quota is None for row in mixed.rows)


def test_threshold_line_sits_at_the_alert_boundary():
    geometry = chart([entry(0, 10), entry(15, 12)], threshold=10)
    assert geometry.threshold_used_percent == pytest.approx(90.0)
    assert geometry.threshold_y == pytest.approx(y_scale(90, PLOT_HEIGHT))
    strict = chart([entry(0, 10), entry(15, 12)], threshold=25)
    assert strict.threshold_used_percent == pytest.approx(75.0)


def test_reset_marks_are_density_gated():
    # 30 distinct reset_at values -> 30 cycles -> 29 cuts > MAX_RESET_MARKS.
    dense = [
        entry(step * 15, 10 + step % 5, reset=BASE_TIME + timedelta(hours=step + 1))
        for step in range(30)
    ]
    assert len(split_cycles(window_samples(
        dense, "five_hour", NOW - timedelta(hours=24), NOW))) == 30
    assert chart(dense).reset_marks == ()
    assert MAX_RESET_MARKS == 24
    # Three well-separated cycles on a wide plot: captions allowed.
    spread = [
        entry(0, 10, reset=BASE_TIME + timedelta(hours=5)),
        entry(60, 20, reset=BASE_TIME + timedelta(hours=5)),
        entry(8 * 60, 5, reset=BASE_TIME + timedelta(hours=13)),
        entry(9 * 60, 8, reset=BASE_TIME + timedelta(hours=13)),
        entry(16 * 60, 4, reset=BASE_TIME + timedelta(hours=21)),
        entry(17 * 60, 6, reset=BASE_TIME + timedelta(hours=21)),
    ]
    marks = chart(spread).reset_marks
    assert len(marks) == 2
    assert all(show for _, show in marks)
    # The same cuts squeezed into minutes: hairlines stay, captions drop.
    tight = [
        entry(0, 10, reset=BASE_TIME + timedelta(hours=5)),
        entry(1, 5, reset=BASE_TIME + timedelta(hours=13)),
        entry(2, 4, reset=BASE_TIME + timedelta(hours=21)),
    ]
    tight_marks = chart(tight).reset_marks
    assert len(tight_marks) == 2
    assert not any(show for _, show in tight_marks)


def test_left_edge_clipping_does_not_fabricate_a_reset_mark():
    # The first cycle began before the range; only the real cut gets a mark.
    entries = [
        entry(60, 50, reset=BASE_TIME + timedelta(hours=5)),
        entry(120, 80, reset=BASE_TIME + timedelta(hours=5)),
        entry(180, 5, reset=BASE_TIME + timedelta(hours=10)),
        entry(240, 9, reset=BASE_TIME + timedelta(hours=10)),
    ]
    geometry = chart(entries)
    assert len(geometry.cycles) == 2
    assert len(geometry.reset_marks) == 1
    assert geometry.reset_marks[0][0] > 0
    # The clipped first cycle still draws its in-range portion.
    assert geometry.cycles[0].points[0][0] > 0