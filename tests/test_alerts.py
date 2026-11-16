from datetime import UTC, datetime, timedelta

from token_plan_monitor.alerts import evaluate
from token_plan_monitor.models import AlertSettings, QuotaWindow, UsageSnapshot

BASE_TIME = datetime(2026, 11, 16, 8, tzinfo=UTC)


def snapshot(percent, reset=None, kind="five_hour"):
    return UsageSnapshot(
        plan_type="agent_plan",
        plan_name="Agent Plan Large",
        queried_at=BASE_TIME,
        windows=(QuotaWindow(kind, percent, reset),),
    )


def kinds(events):
    return [(event.category, event.kind) for event in events]


def test_downward_crossing_fires_once_and_recovery_rearms():
    alert = AlertSettings(threshold_percent=10)
    armed = {}
    events, armed = evaluate(snapshot(50), snapshot(95), alert, armed, "volcengine")
    # 95% used = 5% remaining: below the threshold, first crossing fires.
    assert kinds(events) == [("low_quota", "five_hour")]
    assert events[0].remaining_percent == 5.0
    assert events[0].threshold_percent == 10
    assert events[0].provider == "volcengine"
    # Still low on the next refresh: no repeat.
    events, armed = evaluate(snapshot(95), snapshot(96), alert, armed, "volcengine")
    assert events == []
    # Recovery above the threshold re-arms silently.
    events, armed = evaluate(snapshot(96), snapshot(20), alert, armed, "volcengine")
    assert events == []
    # A window reset jumps remaining back up, so the next dip fires again.
    events, armed = evaluate(snapshot(20), snapshot(97), alert, armed, "volcengine")
    assert kinds(events) == [("low_quota", "five_hour")]


def test_first_observation_already_low_reminds_once():
    events, armed = evaluate(None, snapshot(95), AlertSettings(), {}, "volcengine")
    assert kinds(events) == [("low_quota", "five_hour")]
    events, _ = evaluate(snapshot(95), snapshot(95), AlertSettings(), armed, "volcengine")
    assert events == []


def test_disabled_alerts_emit_nothing_and_keep_armed_state():
    armed = {"five_hour": False}
    events, next_armed = evaluate(
        snapshot(95), snapshot(96), AlertSettings(enabled=False), armed, "volcengine"
    )
    assert events == []
    assert next_armed == armed


def test_reset_reminder_is_opt_in_and_needs_a_changed_known_reset():
    old_reset = BASE_TIME + timedelta(hours=4)
    new_reset = BASE_TIME + timedelta(hours=9)
    previous = snapshot(20, reset=old_reset)
    current = snapshot(5, reset=new_reset)
    events, _ = evaluate(previous, current, AlertSettings(notify_on_reset=False), {}, "volcengine")
    assert kinds(events) == []
    events, _ = evaluate(previous, current, AlertSettings(notify_on_reset=True), {}, "volcengine")
    assert kinds(events) == [("reset", "five_hour")]
    # Same reset, or an unknown one on either side: no reset event.
    events, _ = evaluate(current, current, AlertSettings(notify_on_reset=True), {}, "volcengine")
    assert kinds(events) == []
    events, _ = evaluate(snapshot(20), current, AlertSettings(notify_on_reset=True), {}, "volcengine")
    assert kinds(events) == []


def test_each_window_arms_independently_and_input_map_is_not_mutated():
    alert = AlertSettings(threshold_percent=10)
    previous = UsageSnapshot(
        plan_type="agent_plan", queried_at=BASE_TIME,
        windows=(QuotaWindow("five_hour", 20), QuotaWindow("weekly", 20)),
    )
    current = UsageSnapshot(
        plan_type="agent_plan", queried_at=BASE_TIME,
        windows=(QuotaWindow("five_hour", 95), QuotaWindow("weekly", 20)),
    )
    armed = {"weekly": True}
    events, next_armed = evaluate(previous, current, alert, armed, "bailian")
    assert kinds(events) == [("low_quota", "five_hour")]
    assert next_armed == {"weekly": True, "five_hour": False}
    assert armed == {"weekly": True}