import uuid

from token_plan_monitor.instance import SingleInstance


def test_named_mutex_rejects_duplicates_and_releases_cleanly():
    name = rf"Local\TokenPlanMonitor.Test.{uuid.uuid4().hex}"
    first = SingleInstance(name)
    duplicate = SingleInstance(name)
    try:
        assert first.acquire()
        assert not duplicate.acquire()
    finally:
        duplicate.release()
        first.release()

    restarted = SingleInstance(name)
    try:
        assert restarted.acquire()
    finally:
        restarted.release()
