"""Pure trend rules over history rows: cycle slicing, rate, exhaustion guess.

No IO and no platform imports -- the monitor feeds rows in and carries the
result in MonitorViewState, the same testable split as history.py. Honesty
rules: rates come only from numbers the API reported, percent-only plans
never gain a fabricated absolute amount, and thin evidence yields None
fields instead of a confident guess.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Sequence

from .history import HistoryEntry
from .models import UsageSnapshot, WindowTrend

#: Below either bound the slope is noise, so no rate is reported.
MIN_SAMPLES = 2
MIN_SPAN_SECONDS = 10 * 60
#: Rates at or below this count as "no consumption lately", not a prediction.
MIN_RATE_PERCENT_PER_HOUR = 0.01
#: Walking back in time, usage should never rise; a rise bigger than this
#: tolerance means a window reset happened between the two rows.
_RESET_JUMP_TOLERANCE_PERCENT = 1.0


def _slope_per_hour(points: list[tuple[float, float]]) -> float | None:
    """Least-squares slope of (seconds, value) samples, scaled to per-hour."""
    count = len(points)
    mean_x = sum(x for x, _ in points) / count
    mean_y = sum(y for _, y in points) / count
    denominator = sum((x - mean_x) ** 2 for x, _ in points)
    if denominator == 0:
        return None
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in points)
    return numerator / denominator * 3600.0


def _cycle_samples(
    entries: Sequence[HistoryEntry], kind: str, reset_at: datetime | None
) -> list[tuple[datetime, float, float | None, float | None]]:
    """Newest-first walk keeping only rows from the current window cycle.

    Two independent signals cut the cycle, so a missing or drifting reset_at
    still cannot pull last cycle's consumption into this one: a differing
    known reset_at, or usage rising as we walk back in time (a reset).
    """
    samples: list[tuple[datetime, float, float | None, float | None]] = []
    for entry in reversed(entries):
        window = next((item for item in entry.windows if item.kind == kind), None)
        if window is None:
            continue
        if (
            reset_at is not None
            and window.reset_at is not None
            and window.reset_at != reset_at
        ):
            break
        if samples and window.used_percent > samples[-1][1] + _RESET_JUMP_TOLERANCE_PERCENT:
            break
        samples.append((entry.queried_at, window.used_percent, window.quota, window.used))
    samples.reverse()
    return samples


def compute_trend(
    entries: Sequence[HistoryEntry],
    kind: str,
    now: datetime,
    reset_at: datetime | None,
) -> WindowTrend:
    samples = _cycle_samples(entries, kind, reset_at)
    if len(samples) < MIN_SAMPLES:
        return WindowTrend(kind=kind, sample_count=len(samples), span_seconds=0.0)
    span = (samples[-1][0] - samples[0][0]).total_seconds()
    if span < MIN_SPAN_SECONDS:
        return WindowTrend(kind=kind, sample_count=len(samples), span_seconds=span)

    origin = samples[0][0]
    percent_points = [
        ((queried_at - origin).total_seconds(), used_percent)
        for queried_at, used_percent, _, _ in samples
    ]
    rate = _slope_per_hour(percent_points)
    if rate is None:
        return WindowTrend(kind=kind, sample_count=len(samples), span_seconds=span)

    exhausted_at: datetime | None = None
    resets_before: bool | None = None
    if rate > MIN_RATE_PERCENT_PER_HOUR:
        remaining = max(0.0, 100.0 - samples[-1][1])
        exhausted_at = now + timedelta(hours=remaining / rate)
        if reset_at is not None:
            resets_before = reset_at <= exhausted_at

    absolute_rate: float | None = None
    if all(quota is not None and used is not None for _, _, quota, used in samples):
        absolute_points = [
            ((queried_at - origin).total_seconds(), used)
            for queried_at, _, _, used in samples
        ]
        absolute_rate = _slope_per_hour(absolute_points)

    return WindowTrend(
        kind=kind,
        sample_count=len(samples),
        span_seconds=span,
        percent_per_hour=rate,
        exhausted_at=exhausted_at,
        resets_before_exhaustion=resets_before,
        absolute_per_hour=absolute_rate,
    )


def trends_for_snapshot(
    entries: Sequence[HistoryEntry], snapshot: UsageSnapshot, now: datetime
) -> tuple[WindowTrend, ...]:
    return tuple(
        compute_trend(entries, window.kind, now, window.reset_at)
        for window in snapshot.windows
    )