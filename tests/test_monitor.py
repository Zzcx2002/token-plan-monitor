import json
import threading
import time
import pytest
from datetime import UTC, datetime, timedelta

from token_plan_monitor.history import HistoryEntry, QuotaSource, account_fingerprint, entry_from_snapshot
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


SOURCE = QuotaSource("volcengine", "cn-beijing", account_fingerprint("AKLT-a"))


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
        assert storage.load_snapshot(SOURCE).plan_name == "Agent Plan Large"
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


def test_resaving_identical_settings_keeps_the_in_flight_result(tmp_path):
    storage = configured_storage(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    calls = []

    class BlockingClient:
        def query(self):
            calls.append(1)
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
        revision = monitor.get_state().revision
        # Saving the very same settings mid-query must not bump the revision:
        # the in-flight result was fetched with these exact credentials.
        monitor.update_configuration(Settings(), Credentials("AKLT-a", "secret"))
        assert monitor.get_state().revision == revision
        release.set()
        wait_for(lambda: monitor.get_state().status == "ok")
        assert monitor.get_state().snapshot.plan_name == "Agent Plan Large"
        assert storage.load_snapshot(SOURCE).plan_name == "Agent Plan Large"
        history = storage.load_history("volcengine", account_fingerprint("AKLT-a"))
        assert len(history) == 1
        time.sleep(0.05)
        assert len(calls) == 1
    finally:
        release.set()
        monitor.stop()


def test_threshold_change_mid_flight_keeps_the_result_and_lands_new_threshold(tmp_path):
    storage = configured_storage(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    calls = []

    class BlockingClient:
        def query(self):
            calls.append(1)
            entered.set()
            if not release.wait(2):
                raise TimeoutError("test release failed")
            return snapshot(percent=95)

    monitor = UsageMonitor(
        storage, client_factory=lambda settings, credentials: BlockingClient()
    )
    try:
        monitor.start()
        assert entered.wait(2)
        # Same account, only the alert threshold changes: the in-flight query
        # was fetched from the same source and must survive (review P2-15).
        monitor.update_configuration(
            Settings(alert=AlertSettings(threshold_percent=45)),
            Credentials("AKLT-a", "secret"),
        )
        assert monitor.get_state().alert_threshold_percent == 45
        release.set()
        wait_for(lambda: monitor.get_state().status == "ok")
        state = monitor.get_state()
        assert state.snapshot is not None
        # The result publishes with the NEW threshold, not the captured one.
        assert state.alert_threshold_percent == 45
        assert len(storage.load_history("volcengine", account_fingerprint("AKLT-a"))) == 1
        time.sleep(0.05)
        assert len(calls) == 1
    finally:
        release.set()
        monitor.stop()


def test_interval_change_alone_does_not_force_a_requery(tmp_path):
    storage = configured_storage(tmp_path)
    calls = []

    class CountingClient:
        def query(self):
            calls.append(1)
            return snapshot()

    monitor = UsageMonitor(
        storage, client_factory=lambda settings, credentials: CountingClient()
    )
    try:
        monitor.start()
        wait_for(lambda: monitor.get_state().status == "ok")
        monitor.update_configuration(
            Settings(refresh_minutes=30), Credentials("AKLT-a", "secret")
        )
        time.sleep(0.05)
        assert len(calls) == 1
        assert monitor.get_state().snapshot is not None
    finally:
        monitor.stop()


def test_resaving_identical_settings_while_idle_does_not_requery(tmp_path):
    storage = configured_storage(tmp_path)
    calls = []

    class CountingClient:
        def query(self):
            calls.append(1)
            return snapshot()

    monitor = UsageMonitor(
        storage, client_factory=lambda settings, credentials: CountingClient()
    )
    try:
        monitor.start()
        wait_for(lambda: monitor.get_state().status == "ok")
        revision = monitor.get_state().revision
        monitor.update_configuration(Settings(), Credentials("AKLT-a", "secret"))
        time.sleep(0.05)
        assert len(calls) == 1
        assert monitor.get_state().revision == revision
        assert monitor.get_state().snapshot is not None
    finally:
        monitor.stop()


def test_client_is_reused_across_refreshes_and_rebuilt_on_region_change(tmp_path):
    storage = configured_storage(tmp_path)
    instances = []
    calls = []

    class RecordingClient:
        def __init__(self, settings):
            self.region = settings.region
            self.closed = False
            instances.append(self)

        def query(self):
            calls.append(1)
            return snapshot()

        def close(self):
            self.closed = True

    monitor = UsageMonitor(
        storage,
        client_factory=lambda settings, credentials: RecordingClient(settings),
    )
    try:
        monitor.start()
        wait_for(lambda: len(calls) == 1)
        monitor.request_refresh()
        wait_for(lambda: len(calls) == 2)
        # Same (provider, region, credentials): one client serves both refreshes.
        assert len(instances) == 1
        monitor.update_configuration(
            Settings(region="cn-shanghai"), Credentials("AKLT-a", "secret")
        )
        wait_for(lambda: len(calls) == 3)
        assert [client.region for client in instances] == ["cn-beijing", "cn-shanghai"]
        # The retired client's session is closed on the monitor thread.
        assert instances[0].closed is True
        assert instances[1].closed is False
    finally:
        monitor.stop()
    # Loop exit retires the live client too.
    wait_for(lambda: instances[-1].closed)


def test_monitor_can_restart_after_stop(tmp_path):
    storage = configured_storage(tmp_path)
    calls = []

    class CountingClient:
        def query(self):
            calls.append(1)
            return snapshot()

    monitor = UsageMonitor(
        storage, client_factory=lambda settings, credentials: CountingClient()
    )
    monitor.start()
    wait_for(lambda: len(calls) == 1)
    monitor.stop()
    # The loop is fully stopped and its state cleared for a fresh start.
    assert monitor._thread is None
    monitor.start()
    wait_for(lambda: len(calls) == 2)
    assert monitor.get_state().status == "ok"
    assert monitor.get_state().snapshot is not None
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
        assert storage.load_snapshot(SOURCE) is None
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
        assert storage.load_snapshot(SOURCE) is not None
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
        def append_history(self, entry, provider, *, region):
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


def test_history_rows_ride_the_state_filter_account_and_survive_failure(tmp_path):
    storage = configured_storage(tmp_path, "AKLT-a")
    account = account_fingerprint("AKLT-a")
    other = account_fingerprint("AKLT-b")
    base = datetime(2026, 11, 2, tzinfo=UTC)  # snapshot()'s queried_at
    for acct, minutes in ((account, -30), (other, -20), (account, -15)):
        storage.append_history(
            HistoryEntry(
                queried_at=base + timedelta(minutes=minutes),
                account=acct,
                plan_type="agent_plan",
                windows=(QuotaWindow(kind="five_hour", used_percent=15),),
            ),
            "volcengine",
        )

    class FailAfterFirst:
        calls = 0

        def query(self):
            self.calls += 1
            if self.calls > 1:
                raise NetworkError("网络暂时不可用")
            return snapshot()

    client = FailAfterFirst()
    monitor = UsageMonitor(storage, client_factory=lambda *_: client)
    try:
        monitor.start()
        wait_for(lambda: monitor.get_state().status == "ok")
        rows = monitor.get_state().history
        # Two seeded rows for this account plus the refresh's own append;
        # the other account's row never rides along.
        assert len(rows) == 3
        assert all(entry.account == account for entry in rows)
        monitor.request_refresh()
        wait_for(lambda: monitor.get_state().status == "stale")
        assert monitor.get_state().history == rows
        monitor.update_configuration(Settings(), Credentials("AKLT-b", "secret"))
        assert {row.account for row in monitor.get_state().history} == {other}
    finally:
        monitor.stop()


def test_history_is_seeded_at_startup_before_the_first_refresh(tmp_path):
    storage = configured_storage(tmp_path, "AKLT-a")
    storage.save_snapshot(snapshot(), SOURCE)
    account = account_fingerprint("AKLT-a")
    storage.append_history(
        HistoryEntry(
            queried_at=datetime(2026, 11, 1, tzinfo=UTC),
            account=account,
            plan_type="agent_plan",
            windows=(QuotaWindow(kind="five_hour", used_percent=10),),
        ),
        "volcengine",
    )
    release = threading.Event()

    class BlockingClient:
        def query(self):
            if not release.wait(2):
                raise TimeoutError("test release failed")
            return snapshot()

    monitor = UsageMonitor(storage, client_factory=lambda *_: BlockingClient())
    try:
        monitor.start()
        state = monitor.get_state()
        # Cached snapshot + seeded history: the history window has data at
        # revision 0, without waiting for the blocked first refresh.
        assert state.status == "ok"
        assert len(state.history) == 1
        assert state.history[0].account == account
    finally:
        release.set()
        monitor.stop()


def test_unconfigured_startup_carries_no_history(tmp_path):
    storage = configured_storage(tmp_path, ak=None)
    storage.append_history(
        HistoryEntry(
            queried_at=datetime(2026, 11, 1, tzinfo=UTC),
            account="a1b2c3d4e5f6",
            plan_type="agent_plan",
            windows=(QuotaWindow(kind="five_hour", used_percent=10),),
        ),
        "volcengine",
    )
    monitor = UsageMonitor(
        storage, client_factory=lambda *_: StaticClient(snapshot())
    )
    try:
        monitor.start()
        assert monitor.get_state().status == "unconfigured"
        assert monitor.get_state().history == ()
    finally:
        monitor.stop()


def test_history_read_failure_blanks_rows_but_not_the_refresh(tmp_path):
    class BrokenReadStorage(Storage):
        # The refresh's single read now happens inside append_history; the
        # fallback load_history must fail too for the rows to blank out.
        def append_history(self, entry, provider, *, region):
            raise CacheError("disk on fire")

        def load_history(self, provider, account=None, limit=2000, *, region):
            raise CacheError("disk on fire")

    storage = BrokenReadStorage(tmp_path, FakeKeyring())
    storage.save_settings(Settings())
    storage.save_credentials(Credentials("AKLT-a", "secret"))
    monitor = UsageMonitor(
        storage, client_factory=lambda *_: StaticClient(snapshot())
    )
    try:
        monitor.start()
        wait_for(lambda: monitor.get_state().status == "ok")
        state = monitor.get_state()
        assert state.snapshot is not None
        assert state.history == ()
        # Like a failed append, a failed read is logged, never a UI warning.
        assert state.error is None
    finally:
        monitor.stop()


def test_history_write_failure_still_shows_rows_via_the_read_fallback(tmp_path):
    class WriteOnlyFailureStorage(Storage):
        def append_history(self, entry, provider, *, region):
            raise CacheError("disk full")

    storage = WriteOnlyFailureStorage(tmp_path, FakeKeyring())
    storage.save_settings(Settings())
    storage.save_credentials(Credentials("AKLT-a", "secret"))
    # Seed one row through the real writer before the monitor starts.
    Storage.append_history(
        storage,
        entry_from_snapshot(snapshot(), account_fingerprint("AKLT-a")),
        "volcengine",
    )
    monitor = UsageMonitor(
        storage, client_factory=lambda *_: StaticClient(snapshot())
    )
    try:
        monitor.start()
        wait_for(lambda: monitor.get_state().status == "ok")
        state = monitor.get_state()
        assert state.snapshot is not None
        # A write-only fault must not blank the history: the fallback read
        # still serves the rows that are already on disk.
        assert len(state.history) == 1
        assert state.error is None
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


def test_switch_during_snapshot_write_then_restart_offline(tmp_path, monkeypatch):
    storage = configured_storage(tmp_path)
    staged, release = threading.Event(), threading.Event()
    original = storage.save_snapshot

    def pause(snapshot, source, *, commit):
        def while_staged(temporary, target):
            staged.set()
            assert release.wait(3)
            return commit(temporary, target)
        original(snapshot, source, commit=while_staged)

    monkeypatch.setattr(storage, "save_snapshot", pause)
    class Client:
        def __init__(self, settings, credentials):
            self.provider = settings.provider

        def query(self):
            if self.provider == "volcengine":
                return snapshot("Old account")
            raise NetworkError("offline")

    monitor = UsageMonitor(storage, client_factory=Client)
    try:
        monitor.start()
        assert staged.wait(2)
        settings, credentials = Settings(provider="bailian"), Credentials("other-ak", "other-sk")
        storage.save_settings(settings)
        storage.save_credentials(credentials, settings.provider)
        monitor.update_configuration(settings, credentials)
        release.set()
        wait_for(lambda: monitor.get_state().status == "error")
        assert storage.load_snapshot(SOURCE) is None
    finally:
        release.set()
        monitor.stop()
    (tmp_path / "snapshot.json").write_text(json.dumps({"schema_version": 1, "snapshot": snapshot().to_dict()}))
    restarted = UsageMonitor(storage, client_factory=Client)
    try:
        restarted.start()
        wait_for(lambda: restarted.get_state().status == "error")
        assert restarted.get_state().snapshot is None
    finally:
        restarted.stop()


def test_region_switch_and_manual_legacy_assignment_are_isolated(tmp_path):
    from dataclasses import replace

    storage = configured_storage(tmp_path)
    sample = replace(snapshot(), queried_at=datetime.now(UTC))
    entry = entry_from_snapshot(sample, SOURCE.account)
    legacy = tmp_path / "history" / "volcengine.jsonl"
    legacy.parent.mkdir()
    legacy.write_text(entry.to_line() + "\n")
    raw = legacy.read_bytes()
    storage.save_snapshot(sample, SOURCE)
    storage.append_history(entry, SOURCE.provider)

    class Offline:
        def query(self):
            raise NetworkError("offline")

    monitor = UsageMonitor(storage, client_factory=lambda *_: Offline())
    try:
        monitor.start()
        wait_for(lambda: monitor.get_state().status == "stale")
        assert monitor.get_state().unassigned_history == 1
        monitor.update_configuration(Settings(region="cn-shanghai"), Credentials("AKLT-a", "secret"))
        wait_for(lambda: monitor.get_state().status == "error")
        state = monitor.get_state()
        assert state.snapshot is None and not state.history and not state.trends
        monitor.assign_legacy_history("cn-shanghai", state.revision)
        assert monitor.get_state().history == (entry,)
        assert monitor.get_state().unassigned_history == 0
        storage.assign_legacy_history(SOURCE, "cn-shanghai")
        assert storage.load_history(SOURCE.provider, SOURCE.account, region="cn-shanghai") == [entry]
        assert storage.unassigned_history_count(SOURCE) == 0
        assert legacy.read_bytes() == raw
    finally:
        monitor.stop()


def test_interval_change_rearms_idle_deadline_and_refreshes_when_due(tmp_path):
    storage = configured_storage(tmp_path)
    storage.save_settings(Settings(refresh_minutes=1440))
    now = [datetime.now(UTC)]
    calls = []
    class Client:
        def query(self):
            calls.append(1)
            return snapshot()
    monitor = UsageMonitor(storage, client_factory=lambda *_: Client(), clock=lambda: now[0])
    creds = Credentials("AKLT-a", "secret")
    try:
        monitor.start()
        wait_for(lambda: monitor.get_state().status == "ok")
        now[0] += timedelta(minutes=2)
        monitor.update_configuration(Settings(refresh_minutes=5), creds)
        assert monitor._next_run - time.monotonic() == pytest.approx(180, abs=1)
        monitor.update_configuration(Settings(refresh_minutes=20), creds)
        assert monitor._next_run - time.monotonic() == pytest.approx(1080, abs=1)
        assert len(calls) == 1
        now[0] += timedelta(minutes=6)
        monitor.update_configuration(Settings(refresh_minutes=5), creds)
        wait_for(lambda: len(calls) == 2 and not monitor.get_state().is_refreshing)
    finally:
        monitor.stop()
