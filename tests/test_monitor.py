import threading
import time
import pytest
from datetime import UTC, datetime, timedelta

from token_plan_monitor.history import HistoryEntry, account_fingerprint
from token_plan_monitor.models import (
    AlertSettings,
    CacheError,
    Credentials,
    AuthenticationError,
    ConfigurationError,
    NetworkError,
    TransientProviderError,
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
        assert state.error_category == "network"
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


@pytest.mark.parametrize("failure", [AuthenticationError, ConfigurationError, NetworkError, TransientProviderError])
def test_error_category_propagates_and_clears_on_recovery(tmp_path, failure):
    storage = configured_storage(tmp_path)
    class RecoveringClient:
        failed = False
        def query(self):
            if not self.failed:
                self.failed = True
                raise failure("safe message")
            return snapshot()
    client = RecoveringClient()
    monitor = UsageMonitor(storage, client_factory=lambda *_: client)
    try:
        monitor.start()
        wait_for(lambda: monitor.get_state().status == "error")
        assert monitor.get_state().error_category == failure.category
        monitor.request_refresh()
        wait_for(lambda: monitor.get_state().status == "ok")
        assert monitor.get_state().error_category is None
        assert monitor.get_state().error is None
    finally:
        monitor.stop()


def test_provider_switch_clears_the_cached_snapshot(tmp_path):
    storage = configured_storage(tmp_path, "AKLT-a")
    release = threading.Event()
    seen = []

    class RecordingClient:
        def __init__(self, settings):
            self.settings = settings

        def query(self):
            seen.append(self.settings.provider)
            if not release.wait(2):
                raise TimeoutError("test release failed")
            return snapshot()

    monitor = UsageMonitor(
        storage, client_factory=lambda settings, credentials: RecordingClient(settings)
    )
    try:
        monitor.start()
        wait_for(lambda: len(seen) == 1)
        monitor.update_configuration(Settings(provider="bailian"), Credentials("AKLT-a", "secret"))
        state = monitor.get_state()
        assert state.provider == "bailian"
        assert state.snapshot is None
        assert state.status == "empty"
        assert storage.load_snapshot() is None
        release.set()
        wait_for(lambda: len(seen) == 2)
        assert seen == ["volcengine", "bailian"]
        wait_for(lambda: monitor.get_state().status == "ok")
        assert monitor.get_state().provider == "bailian"
    finally:
        release.set()
        monitor.stop()


def test_resaving_the_same_provider_keeps_the_cached_snapshot(tmp_path):
    storage = configured_storage(tmp_path, "AKLT-a")
    monitor = UsageMonitor(
        storage, client_factory=lambda settings, credentials: StaticClient(snapshot())
    )
    try:
        monitor.start()
        wait_for(lambda: monitor.get_state().status == "ok")
        monitor.update_configuration(Settings(), Credentials("AKLT-a", "secret"))
        assert monitor.get_state().snapshot is not None
        assert storage.load_snapshot() is not None
    finally:
        monitor.stop()


def test_successful_refresh_appends_history_for_the_active_account(tmp_path):
    storage = configured_storage(tmp_path, "AKLT-a")
    monitor = UsageMonitor(
        storage, client_factory=lambda settings, credentials: StaticClient(snapshot())
    )
    try:
        monitor.start()
        wait_for(lambda: monitor.get_state().status == "ok")
        entries = storage.load_history("volcengine")
        assert len(entries) == 1
        assert entries[0].account == account_fingerprint("AKLT-a")
        assert entries[0].windows == snapshot().windows
        # An identical manual refresh within the gap adds no row.
        monitor.request_refresh()
        wait_for(lambda: not monitor.get_state().is_refreshing)
        assert len(storage.load_history("volcengine")) == 1
    finally:
        monitor.stop()


def test_history_append_failure_never_fails_the_refresh(tmp_path):
    class BrokenHistoryStorage(Storage):
        def append_history(self, entry, provider):
            raise CacheError("disk on fire")

    keyring = FakeKeyring()
    storage = BrokenHistoryStorage(tmp_path, keyring)
    storage.save_settings(Settings())
    storage.save_credentials(Credentials("AKLT-a", "secret"))
    monitor = UsageMonitor(
        storage, client_factory=lambda settings, credentials: StaticClient(snapshot())
    )
    try:
        monitor.start()
        wait_for(lambda: monitor.get_state().status == "ok")
        state = monitor.get_state()
        assert state.snapshot is not None
        # Unlike a snapshot cache failure, history loss is not a UI warning.
        assert state.error is None
    finally:
        monitor.stop()


def test_notifier_fires_once_per_crossing_and_survives_its_own_failure(tmp_path):
    storage = configured_storage(tmp_path)
    events = []

    class SequenceClient:
        def __init__(self):
            # healthy -> low -> still low -> recovered -> low again
            self.results = [snapshot(percent=p) for p in (25, 95, 96, 20, 97)]
            self.index = 0

        def query(self):
            result = self.results[min(self.index, len(self.results) - 1)]
            self.index += 1
            return result

    client = SequenceClient()

    def notifier(event):
        events.append(event)
        if len(events) == 1:
            raise RuntimeError("notifier exploded")

    monitor = UsageMonitor(
        storage, client_factory=lambda *_: client, notifier=notifier
    )
    try:
        monitor.start()
        wait_for(lambda: monitor.get_state().status == "ok")
        assert events == []
        monitor.request_refresh()
        wait_for(lambda: len(events) == 1)  # crossed; the raise did not kill the loop
        assert events[0].category == "low_quota"
        assert events[0].kind == "five_hour"
        monitor.request_refresh()
        wait_for(lambda: client.index >= 3 and not monitor.get_state().is_refreshing)
        assert len(events) == 1  # still low: no repeat notification
        monitor.request_refresh()
        wait_for(lambda: client.index >= 4 and not monitor.get_state().is_refreshing)
        assert len(events) == 1  # recovery only re-arms
        # A new account starts with fresh arming, so being low fires again.
        monitor.update_configuration(Settings(), Credentials("AKLT-b", "secret"))
        wait_for(lambda: len(events) == 2)
        assert monitor.get_state().status == "ok"
    finally:
        monitor.stop()


def test_disabled_alerts_never_reach_the_notifier(tmp_path):
    storage = configured_storage(tmp_path)
    storage.save_settings(Settings(alert=AlertSettings(enabled=False)))
    events = []
    monitor = UsageMonitor(
        storage,
        client_factory=lambda *_: StaticClient(snapshot(percent=95)),
        notifier=events.append,
    )
    try:
        monitor.start()
        wait_for(lambda: monitor.get_state().snapshot is not None)
        monitor.request_refresh()
        wait_for(lambda: not monitor.get_state().is_refreshing)
        assert events == []
    finally:
        monitor.stop()


def test_trends_come_from_history_and_survive_a_failed_refresh(tmp_path):
    storage = configured_storage(tmp_path, "AKLT-a")
    account = account_fingerprint("AKLT-a")
    base = datetime(2026, 11, 2, tzinfo=UTC)  # snapshot()'s queried_at
    for minutes, percent in ((-30, 15), (-15, 20)):
        storage.append_history(
            HistoryEntry(
                queried_at=base + timedelta(minutes=minutes),
                account=account,
                plan_type="agent_plan",
                windows=(QuotaWindow(kind="five_hour", used_percent=percent),),
            ),
            "volcengine",
        )

    class FailAfterFirst:
        calls = 0

        def query(self):
            self.calls += 1
            if self.calls > 1:
                raise NetworkError("网络暂时不可用")
            return snapshot()  # 25% used

    client = FailAfterFirst()
    monitor = UsageMonitor(storage, client_factory=lambda *_: client)
    try:
        monitor.start()
        wait_for(lambda: monitor.get_state().status == "ok")
        trends = monitor.get_state().trends
        assert len(trends) == 1
        # 15 -> 20 -> 25 percent over 30 minutes = 20%/h.
        assert trends[0].percent_per_hour == pytest.approx(20.0)
        monitor.request_refresh()
        wait_for(lambda: monitor.get_state().status == "stale")
        # Like the retained snapshot, the trends stay visible while stale.
        assert monitor.get_state().trends == trends
        # Switching account drops trends: they describe the old account.
        monitor.update_configuration(Settings(), Credentials("AKLT-b", "secret"))
        assert monitor.get_state().trends == ()
    finally:
        monitor.stop()


def test_alert_threshold_reaches_every_view_state(tmp_path):
    storage = configured_storage(tmp_path)
    storage.save_settings(Settings(alert=AlertSettings(threshold_percent=30)))
    monitor = UsageMonitor(
        storage, client_factory=lambda settings, credentials: StaticClient(snapshot())
    )
    try:
        monitor.start()
        # The startup state already carries the persisted threshold.
        assert monitor.get_state().alert_threshold_percent == 30
        wait_for(lambda: monitor.get_state().status == "ok")
        assert monitor.get_state().alert_threshold_percent == 30
        # A settings change updates the threshold immediately, before the
        # refreshed snapshot arrives.
        monitor.update_configuration(
            Settings(alert=AlertSettings(threshold_percent=45)),
            Credentials("AKLT-a", "secret"),
        )
        assert monitor.get_state().alert_threshold_percent == 45
        wait_for(lambda: monitor.get_state().status == "ok")
        assert monitor.get_state().alert_threshold_percent == 45
    finally:
        monitor.stop()
