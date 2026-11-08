import threading
import time
from datetime import UTC, datetime

from token_plan_monitor.models import (
    Credentials,
    NetworkError,
    QuotaWindow,
    Settings,
    UsageSnapshot,
)
from token_plan_monitor.monitor import UsageMonitor
from token_plan_monitor.storage import Storage


class FakeKeyring:
    def __init__(self):
        self.values = {}

    def get_password(self, service, username):
        return self.values.get((service, username))

    def set_password(self, service, username, value):
        self.values[(service, username)] = value


def snapshot(name="Agent Plan Large", percent=25):
    return UsageSnapshot(
        plan_type="agent_plan",
        plan_name=name,
        queried_at=datetime(2026, 11, 2, tzinfo=UTC),
        windows=(QuotaWindow(kind="five_hour", used_percent=percent),),
    )


def configured_storage(tmp_path, ak="AKLT-a"):
    keyring = FakeKeyring()
    storage = Storage(tmp_path, keyring)
    storage.save_settings(Settings())
    if ak is not None:
        storage.save_credentials(Credentials(ak, "secret"))
    return storage


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for monitor state")


def test_successful_refresh_updates_and_persists_snapshot(tmp_path):
    storage = configured_storage(tmp_path)
    monitor = UsageMonitor(
        storage,
        client_factory=lambda settings, credentials: StaticClient(snapshot()),
    )
    try:
        monitor.start()
        wait_for(lambda: monitor.get_state().status == "ok")
        assert monitor.get_state().snapshot.plan_name == "Agent Plan Large"
        assert storage.load_snapshot().plan_name == "Agent Plan Large"
    finally:
        monitor.stop()


def test_failure_retains_last_good_snapshot(tmp_path):
    storage = configured_storage(tmp_path)

    class SequenceClient:
        calls = 0

        def query(self):
            type(self).calls += 1
            if type(self).calls == 1:
                return snapshot()
            raise NetworkError("offline")

    monitor = UsageMonitor(
        storage,
        client_factory=lambda settings, credentials: SequenceClient(),
    )
    try:
        monitor.start()
        wait_for(lambda: monitor.get_state().status == "ok")
        monitor.request_refresh()
        wait_for(lambda: monitor.get_state().status == "stale")
        state = monitor.get_state()
        assert state.snapshot.plan_name == "Agent Plan Large"
        assert state.error == "offline"
    finally:
        monitor.stop()


def test_manual_refreshes_are_coalesced_without_overlap(tmp_path):
    storage = configured_storage(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    calls = []
    active = threading.Lock()

    class BlockingClient:
        def query(self):
            with active:
                calls.append(time.monotonic())
                if len(calls) == 1:
                    entered.set()
                    if not release.wait(2):
                        raise TimeoutError("test release failed")
                return snapshot()

    monitor = UsageMonitor(
        storage,
        client_factory=lambda settings, credentials: BlockingClient(),
    )
    try:
        monitor.start()
        assert entered.wait(2)
        for _ in range(5):
            monitor.request_refresh()
        release.set()
        wait_for(lambda: len(calls) >= 2 and not monitor.get_state().is_refreshing)
        time.sleep(0.1)
        assert len(calls) == 2
    finally:
        release.set()
        monitor.stop()


def test_settings_revision_discards_old_credential_result(tmp_path):
    storage = configured_storage(tmp_path, "AKLT-a")
    entered = threading.Event()
    release = threading.Event()

    class AccountClient:
        def __init__(self, credentials):
            self.credentials = credentials

        def query(self):
            if self.credentials.access_key_id == "AKLT-a":
                entered.set()
                if not release.wait(2):
                    raise TimeoutError("test release failed")
                return snapshot("Old account")
            return snapshot("New account")

    monitor = UsageMonitor(
        storage,
        client_factory=lambda settings, credentials: AccountClient(credentials),
    )
    try:
        monitor.start()
        assert entered.wait(2)
        monitor.update_configuration(Settings(), Credentials("AKLT-b", "new-secret"))
        release.set()
        wait_for(
            lambda: monitor.get_state().snapshot is not None
            and monitor.get_state().snapshot.plan_name == "New account"
        )
        assert monitor.get_state().snapshot.plan_name == "New account"
    finally:
        release.set()
        monitor.stop()


def test_missing_credentials_never_starts_a_request(tmp_path):
    storage = configured_storage(tmp_path, ak=None)
    calls = []

    class UnexpectedClient:
        def query(self):
            calls.append(1)
            return snapshot()

    monitor = UsageMonitor(
        storage,
        client_factory=lambda settings, credentials: UnexpectedClient(),
    )
    try:
        monitor.start()
        monitor.request_refresh()
        time.sleep(0.05)
        assert monitor.get_state().status == "unconfigured"
        assert calls == []
    finally:
        monitor.stop()


class StaticClient:
    def __init__(self, result):
        self.result = result

    def query(self):
        return self.result
