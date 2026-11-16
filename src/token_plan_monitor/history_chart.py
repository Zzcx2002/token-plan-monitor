"""Pure chart geometry over history rows: range filtering, cycle segmentation,
pixel bucketing, scales and ticks.

No IO and no platform imports -- the same testable split as ``trends.py``. The
monitor carries history rows in ``MonitorViewState``; this module turns them
into draw-ready geometry and the macOS ``chart.py`` renders it with CALayer /
CAShapeLayer only.

Honesty rules inherited from the rest of the app:

* A window cycle is a separate line segment. The line NEVER connects across a
  reset -- a reset is a break, not a slope.
* Percent-only plans never gain a fabricated absolute amount: ``quota``/``used``
  stay ``None`` wherever the API did not report them.
* Thin evidence yields an ``empty_reason`` instead of a confident-looking chart.
* ``used_percent`` is clamped for DRAWING only (the model does not clamp it);
  samples, tooltip lookups and table rows keep the reported value.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from .history import HistoryEntry
from .trends import RESET_JUMP_TOLERANCE_PERCENT

#: Selectable time ranges, oldest last. 90 days is the retention cap
#: (history.HISTORY_MAX_AGE_DAYS), so a longer range would only show air.
RANGE_KEYS = ("24h", "7d", "30d", "90d")
RANGE_DELTAS = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
}
#: X-axis tick steps per range, anchored at the right edge walking back.
_TICK_STEPS = {
    "24h": timedelta(hours=4),
    "7d": timedelta(days=1),
    "30d": timedelta(days=5),
    "90d": timedelta(days=15),
}

# Plot geometry constants shared with macos/chart.py (points).
PLOT_HEIGHT = 160.0
Y_AXIS_WIDTH = 34.0
X_AXIS_HEIGHT = 18.0
#: One drawing bucket per this many points of plot width.
BUCKET_PIXELS = 2.0
#: Reset captions only render when neighboring marks are at least this far
#: apart; a 90-day five-hour chart has ~432 cycles and cannot label them all.
RESET_CAPTION_MIN_GAP = 44.0
#: Above this many cycle cuts the reset hairlines themselves are omitted --
#: the segment breaks still communicate every reset.
MAX_RESET_MARKS = 24
MIN_CHART_SAMPLES = 2


@dataclass(frozen=True)
class ChartSample:
    """One reported observation of one window, values exactly as reported."""

    queried_at: datetime
    used_percent: float
    quota: float | None
    used: float | None
    reset_at: datetime | None


@dataclass(frozen=True)
class ChartCycle:
    """One window cycle: bucketed draw points plus the raw samples behind them."""

    #: (x, y) in plot coordinates (origin top-left, y down), time order.
    points: tuple[tuple[float, float], ...]
    samples: tuple[ChartSample, ...]


@dataclass(frozen=True)
class ChartBucketRow:
    """One 数据列表 row: the newest observation inside a drawing bucket."""

    queried_at: datetime
    used_percent: float
    quota: float | None
    used: float | None
    cycle_index: int


@dataclass(frozen=True)
class ChartGeometry:
    kind: str
    range_key: str
    range_start: datetime
    range_end: datetime
    plot_width: float
    plot_height: float
    #: Every in-range sample, time order -- the tooltip lookup population.
    samples: tuple[ChartSample, ...]
    cycles: tuple[ChartCycle, ...]
    #: (x, show_caption) per cycle cut; () when cuts exceed MAX_RESET_MARKS.
    reset_marks: tuple[tuple[float, bool], ...]
    #: (percent, y) for 0 / 50 / 100.
    y_ticks: tuple[tuple[float, float], ...]
    #: (x, time) along the range.
    x_ticks: tuple[tuple[float, datetime], ...]
    threshold_y: float
    threshold_used_percent: float
    #: Bucket rows, newest first (the table's order).
    rows: tuple[ChartBucketRow, ...]
    latest: ChartSample | None
    sample_count: int
    #: True when ANY in-range sample reported both quota and used; per-row
    #: values stay None-honest regardless.
    has_absolute: bool
    #: None | "no_history" (0 samples) | "thin" (1 sample).
    empty_reason: str | None


def window_samples(
    entries: Sequence[HistoryEntry], kind: str, start: datetime, end: datetime
) -> tuple[ChartSample, ...]:
    """In-range observations of one window kind, ascending by time.

    Rows missing the kind are skipped, never fatal (mirrors trends.py).
    """
    samples = [
        ChartSample(
            queried_at=entry.queried_at,
            used_percent=window.used_percent,
            quota=window.quota,
            used=window.used,
            reset_at=window.reset_at,
        )
        for entry in entries
        if start <= entry.queried_at <= end
        for window in entry.windows
        if window.kind == kind
    ]
    samples.sort(key=lambda sample: sample.queried_at)
    return tuple(samples)


def split_cycles(
    samples: Sequence[ChartSample],
) -> tuple[tuple[ChartSample, ...], ...]:
    """Forward walk cutting cycles on the same two signals trends.py uses.

    A cut happens when both neighboring reset_at values are known and differ,
    OR usage drops more than the shared tolerance (a reset when reset_at is
    missing or drifting). Splitting BEFORE bucketing is what guarantees a
    reset survives as a path break even inside a single pixel bucket.
    """
    cycles: list[list[ChartSample]] = []
    for sample in samples:
        if cycles:
            previous = cycles[-1][-1]
            reset_changed = (
                previous.reset_at is not None
                and sample.reset_at is not None
                and previous.reset_at != sample.reset_at
            )
            usage_dropped = (
                sample.used_percent
                < previous.used_percent - RESET_JUMP_TOLERANCE_PERCENT
            )
            if reset_changed or usage_dropped:
                cycles.append([])
        if not cycles:
            cycles.append([])
        cycles[-1].append(sample)
    return tuple(tuple(cycle) for cycle in cycles)


def _bucket_groups(
    samples: Sequence[ChartSample], origin: datetime, bucket_seconds: float
) -> list[list[ChartSample]]:
    """Group samples into time buckets anchored at ``origin`` so buckets align
    across charts. Empty buckets are dropped; groups stay in time order."""
    groups: dict[int, list[ChartSample]] = {}
    for sample in samples:
        index = int((sample.queried_at - origin).total_seconds() // bucket_seconds)
        groups.setdefault(index, []).append(sample)
    return [groups[index] for index in sorted(groups)]


def bucket_cycle(
    samples: Sequence[ChartSample], origin: datetime, bucket_seconds: float
) -> tuple[tuple[datetime, float], ...]:
    """Downsample one cycle to ~one point per BUCKET_PIXELS of plot width.

    Each bucket keeps its min-value AND max-value sample (first occurrence of
    each, emitted in time order, one point when they coincide) so spikes and
    dips survive aggregation instead of being averaged away.
    """
    points: list[tuple[datetime, float]] = []
    for group in _bucket_groups(samples, origin, bucket_seconds):
        low = min(group, key=lambda sample: sample.used_percent)
        high = max(group, key=lambda sample: sample.used_percent)
        kept = [low] if low is high else sorted(
            [low, high], key=lambda sample: sample.queried_at
        )
        points.extend((sample.queried_at, sample.used_percent) for sample in kept)
    return tuple(points)


def time_ticks(
    start: datetime, end: datetime, range_key: str
) -> tuple[datetime, ...]:
    """Tick times anchored at ``end`` walking back by the range's step."""
    step = _TICK_STEPS[range_key]
    ticks: list[datetime] = []
    moment = end
    while moment >= start:
        ticks.append(moment)
        moment -= step
    return tuple(reversed(ticks))


def x_scale(t: datetime, start: datetime, end: datetime, width: float) -> float:
    span = (end - start).total_seconds()
    if span <= 0:
        return 0.0
    return (t - start).total_seconds() / span * width


def y_scale(percent: float, height: float) -> float:
    """Flipped y (origin top-left); clamps to [0, 100] for DRAWING only."""
    clamped = max(0.0, min(100.0, percent))
    return height - clamped / 100.0 * height


def build_chart(
    entries: Sequence[HistoryEntry],
    kind: str,
    *,
    now: datetime,
    range_key: str,
    plot_width: float,
    plot_height: float = PLOT_HEIGHT,
    alert_threshold_percent: int = 10,
) -> ChartGeometry:
    """Full draw-ready geometry for one window over one range.

    The x domain is fixed to [now - delta, now] (not data-driven) so every
    chart aligns and range switches are predictable. ``now`` is a parameter:
    the UI passes the last attempt / query time so the right edge is
    deterministic between refreshes and honest when stale.
    """
    delta = RANGE_DELTAS[range_key]
    start = now - delta
    end = now
    samples = window_samples(entries, kind, start, end)
    sample_count = len(samples)
    if sample_count == 0:
        empty_reason: str | None = "no_history"
    elif sample_count < MIN_CHART_SAMPLES:
        empty_reason = "thin"
    else:
        empty_reason = None

    span_seconds = (end - start).total_seconds()
    bucket_count = max(1.0, plot_width / BUCKET_PIXELS)
    bucket_seconds = span_seconds / bucket_count

    cycles: list[ChartCycle] = []
    rows: list[ChartBucketRow] = []
    for cycle_index, cycle_samples in enumerate(split_cycles(samples)):
        points = tuple(
            (x_scale(t, start, end, plot_width), y_scale(percent, plot_height))
            for t, percent in bucket_cycle(cycle_samples, start, bucket_seconds)
        )
        cycles.append(ChartCycle(points=points, samples=tuple(cycle_samples)))
        for group in _bucket_groups(cycle_samples, start, bucket_seconds):
            newest = group[-1]
            rows.append(
                ChartBucketRow(
                    queried_at=newest.queried_at,
                    used_percent=newest.used_percent,
                    quota=newest.quota,
                    used=newest.used,
                    cycle_index=cycle_index,
                )
            )
    rows.reverse()  # newest first for the table

    reset_marks = _reset_marks(cycles, start, end, plot_width)
    threshold_used_percent = float(100 - alert_threshold_percent)
    return ChartGeometry(
        kind=kind,
        range_key=range_key,
        range_start=start,
        range_end=end,
        plot_width=plot_width,
        plot_height=plot_height,
        samples=samples,
        cycles=tuple(cycles),
        reset_marks=reset_marks,
        y_ticks=(
            (0.0, y_scale(0.0, plot_height)),
            (50.0, y_scale(50.0, plot_height)),
            (100.0, y_scale(100.0, plot_height)),
        ),
        x_ticks=tuple(
            (x_scale(t, start, end, plot_width), t)
            for t in time_ticks(start, end, range_key)
        ),
        threshold_y=y_scale(threshold_used_percent, plot_height),
        threshold_used_percent=threshold_used_percent,
        rows=tuple(rows),
        latest=samples[-1] if samples else None,
        sample_count=sample_count,
        has_absolute=any(
            sample.quota is not None and sample.used is not None
            for sample in samples
        ),
        empty_reason=empty_reason,
    )


def _reset_marks(
    cycles: Sequence[ChartCycle],
    start: datetime,
    end: datetime,
    plot_width: float,
) -> tuple[tuple[float, bool], ...]:
    """One hairline per cycle cut, density-gated.

    The first cycle may begin mid-cycle (its earlier part is left of the
    range); that clipped edge is NOT a reset and gets no mark at x=0.
    """
    cuts = len(cycles) - 1
    if cuts <= 0 or cuts > MAX_RESET_MARKS:
        return ()
    xs = [
        x_scale(cycle.samples[0].queried_at, start, end, plot_width)
        for cycle in cycles[1:]
    ]
    marks: list[tuple[float, bool]] = []
    for index, x in enumerate(xs):
        previous = xs[index - 1] if index > 0 else 0.0
        following = xs[index + 1] if index + 1 < len(xs) else plot_width
        show_caption = (
            x - previous >= RESET_CAPTION_MIN_GAP
            and following - x >= RESET_CAPTION_MIN_GAP
        )
        marks.append((x, show_caption))
    return tuple(marks)


def nearest_sample(geometry: ChartGeometry, x: float) -> ChartSample | None:
    """The sample whose x is closest to ``x`` (the crosshair snaps to it).

    None when x is outside the plot or there are no samples -- the caller
    hides the crosshair instead of guessing.
    """
    if not geometry.samples or x < 0 or x > geometry.plot_width:
        return None
    xs = [
        x_scale(sample.queried_at, geometry.range_start, geometry.range_end,
                geometry.plot_width)
        for sample in geometry.samples
    ]
    index = bisect_left(xs, x)
    if index == 0:
        return geometry.samples[0]
    if index == len(xs):
        return geometry.samples[-1]
    before = geometry.samples[index - 1]
    after = geometry.samples[index]
    return before if x - xs[index - 1] <= xs[index] - x else after