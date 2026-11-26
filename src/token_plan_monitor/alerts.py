"""Pure alert trigger rules: when a low-quota or reset event fires.

The armed map lives in the monitor's memory (cleared on provider/account
switches, not persisted): a window fires once per downward crossing and
re-arms when its remaining quota climbs back to the threshold -- which a
window reset does naturally. Restarting the app while still low reminds
once more, a deliberate trade-off against a persisted state file.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .models import AlertSettings, UsageSnapshot


@dataclass(frozen=True)
class AlertEvent:
    category: str  # "low_quota" | "reset"
    kind: str
    provider: str
    remaining_percent: float
    threshold_percent: int
    plan_name: str | None = None
    #: Optional trend context, attached by the monitor when available.
    exhausted_at: datetime | None = None
    resets_before_exhaustion: bool | None = None


def evaluate(
    previous: UsageSnapshot | None,
    current: UsageSnapshot,
    alert: AlertSettings,
    armed: dict[str, bool],
    provider: str,
) -> tuple[list[AlertEvent], dict[str, bool]]:
    """Return (events, new armed map). Never mutates the input map."""
    next_armed = dict(armed)
    if not alert.enabled:
        return [], next_armed
    events: list[AlertEvent] = []
    previous_windows = (
        {window.kind: window for window in previous.windows} if previous else {}
    )
    for window in current.windows:
        before = previous_windows.get(window.kind)
        new_cycle = (before is not None and before.reset_at is not None
                     and window.reset_at is not None and before.reset_at != window.reset_at)
        if new_cycle:
            next_armed[window.kind] = True
        if window.remaining_percent < alert.threshold_percent:
            # Missing entry means armed: the first observation already low
            # deserves a reminder too.
            if next_armed.get(window.kind, True):
                events.append(
                    AlertEvent(
                        category="low_quota",
                        kind=window.kind,
                        provider=provider,
                        remaining_percent=window.remaining_percent,
                        threshold_percent=alert.threshold_percent,
                        plan_name=current.plan_name,
                    )
                )
                next_armed[window.kind] = False
        else:
            next_armed[window.kind] = True
        if new_cycle and alert.notify_on_reset:
            events.append(
                AlertEvent(
                    category="reset",
                    kind=window.kind,
                    provider=provider,
                    remaining_percent=window.remaining_percent,
                    threshold_percent=alert.threshold_percent,
                    plan_name=current.plan_name,
                )
            )
    return events, next_armed