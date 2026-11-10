import uuid

from token_plan_monitor.instance import SingleInstance


def test_named_mutex_rejects_duplicates_and_releases_cleanly(tmp_path):
    name = rf"Local\TokenPlanMonitor.Test.{uuid.uuid4().hex}"
    first = SingleInstance(name, lock_dir=tmp_path)
    duplicate = SingleInstance(name, lock_dir=tmp_path)
    try:
        assert first.acquire()
        assert not duplicate.acquire()
    finally:
        duplicate.release()
        first.release()

    restarted = SingleInstance(name, lock_dir=tmp_path)
    try:
        assert restarted.acquire()
    finally:
        restarted.release()
