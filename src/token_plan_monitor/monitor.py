from __future__ import annotations

import logging
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime
from functools import partial
from typing import Any, Callable

from .models import (
    CacheError,
    ConfigurationError,
    Credentials,
    MonitorViewState,
    QueryError,
    Settings,
)
from .alerts import AlertEvent, evaluate
from .history import HISTORY_MAX_ROWS, QuotaSource, account_fingerprint, entry_from_snapshot
from .providers import create_client
from .storage import Storage
from .trends import trends_for_snapshot

logger = logging.getLogger(__name__)


class UsageMonitor:
    def __init__(
        self,
        storage: Storage,
        client_factory: Callable[..., Any] = create_client,
        on_state: Callable[[MonitorViewState], None] | None = None,
        clock: Callable[[], datetime] | None = None,
        notifier: Callable[[AlertEvent], None] | None = None,
    ):
        self.storage = storage
        self.client_factory = client_factory
        self.on_state = on_state
        self.clock = clock or (lambda: datetime.now(UTC))
        self.notifier = notifier
        # Per-window alert arming, in memory only (see alerts.py).
        self._alert_armed: dict[str, bool] = {}
        self._source_alerts: dict[QuotaSource, dict[str, bool]] = {}

        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pending = False
        self._in_flight = False
        self._next_run: float | None = None
        self._settings = Settings()
        self._credentials: Credentials | None = None
        self._state = MonitorViewState()
        # One client per (provider, region, credentials): keep-alive and the
        # Bailian bearer-token cache only work if the instance survives across
        # refreshes. Owned exclusively by the monitor thread.
        self._client: Any = None
        self._client_key: tuple | None = None

    def start(self) -> None:
        with self._lock:
            if self._thread is not None:
                return
            # A stop() before any start() leaves the stop flag set; a fresh
            # start always begins with a clean signal state.
            self._stop.clear()
            self._wake.clear()
            self._settings = self.storage.load_settings()
            provider = self._settings.provider
            try:
                self._credentials = self.storage.load_credentials(provider)
                error = None
            except ConfigurationError as exc:
                self._credentials = None
                error = exc.message
            snapshot = (self.storage.load_snapshot(QuotaSource.for_credentials(self._settings, self._credentials))
                        if self._credentials is not None else None)
            status = (
                "unconfigured"
                if self._credentials is None
                else "ok" if snapshot is not None else "empty"
            )
            # Seed history from disk so a launch with a cached snapshot shows
            # the history window immediately instead of waiting for the first
            # refresh. One bounded read, consistent with the settings/snapshot/
            # keychain reads start() already does on this thread.
            history, unassigned = (), 0
            if self._credentials is not None:
                source = QuotaSource.for_credentials(self._settings, self._credentials)
                history, unassigned = self._source_history(source)
            self._state = MonitorViewState(
                status=status,
                snapshot=snapshot,
                error=error,
                error_category="configuration" if error else None,
                revision=0,
                provider=provider,
                alert_threshold_percent=self._settings.alert.threshold_percent,
                history=history, region=self._settings.region, unassigned_history=unassigned,
                trends=trends_for_snapshot(history, snapshot, self.clock()) if snapshot else (),
            )
            self._pending = self._credentials is not None
            if self._pending:
                self._wake.set()
            self._thread = threading.Thread(
                target=self._run,
                name="quota-monitor",
                daemon=True,
            )
            self._thread.start()
        self._publish()

    def _source_history(self, source):
        try:
            history = self.storage.load_history(source.provider, source.account, limit=HISTORY_MAX_ROWS,
                                                region=source.region)
            return tuple(history), self.storage.unassigned_history_count(source)
        except CacheError as exc:
            logger.warning("Could not read source history: %s", exc.message)
            return (), 0

    def assign_legacy_history(self, region: str, revision: int) -> None:
        with self._lock:
            if self._credentials is None or revision != self._state.revision:
                raise ConfigurationError("当前账号已切换，请重新选择")
            source = QuotaSource.for_credentials(self._settings, self._credentials)
            self.storage.assign_legacy_history(source, region)
            history, count = self._source_history(source)
            snapshot = self._state.snapshot
            self._state = replace(self._state, history=history, unassigned_history=count,
                                  trends=trends_for_snapshot(history, snapshot, self.clock()) if snapshot else (),
                                  revision=revision + 1)
            if self._in_flight:
                self._pending = True
                self._wake.set()
        self._publish()

    def request_refresh(self) -> None:
        with self._lock:
            if self._stop.is_set() or self._credentials is None:
                return
            self._pending = True
            self._wake.set()

    def refresh_if_due(self) -> None:
        """Recheck a wall-clock deadline after macOS wakes from sleep."""
        with self._lock:
            last = self._state.last_attempt_at
            if last is None and self._state.snapshot is not None:
                last = self._state.snapshot.queried_at
            due = last is None or (self.clock() - last).total_seconds() >= self._settings.refresh_minutes * 60
            if due and not self._in_flight and not self._pending:
                self.request_refresh()

    def update_configuration(
        self, settings: Settings, credentials: Credentials
    ) -> None:
        with self._lock:
            if settings == self._settings and credentials == self._credentials:
                # Re-saving identical settings is a no-op: bumping the revision
                # here would discard an in-flight query fetched with these very
                # credentials and pay for a full re-query nothing asked for.
                # Settings/Credentials are frozen, normalized dataclasses, so
                # == is a reliable deep comparison (alert threshold included).
                unchanged = True
            else:
                unchanged = False
                # Switching provider invalidates the cached snapshot just like
                # switching accounts does: it was fetched from a different service.
                provider_changed = settings.provider != self._settings.provider
                account_changed = provider_changed or credentials != self._credentials
                # Region changes the endpoint the data comes from, so it
                # invalidates in-flight results just like an account switch.
                source_changed = account_changed or settings.region != self._settings.region
                if source_changed and self._credentials is not None:
                    previous_source = QuotaSource.for_credentials(self._settings, self._credentials)
                    self._source_alerts[previous_source] = self._alert_armed
                self._settings = settings
                self._credentials = credentials
                if source_changed:
                    source = QuotaSource.for_credentials(settings, credentials)
                    snapshot = self.storage.load_snapshot(source)
                    history, count = self._source_history(source)
                    self._state = replace(
                        self._state,
                        status="ok" if snapshot is not None else "empty",
                        snapshot=snapshot,
                        last_attempt_at=None,
                        error=None,
                        error_category=None,
                        revision=self._state.revision + 1,
                        provider=settings.provider,
                        alert_threshold_percent=settings.alert.threshold_percent,
                        region=settings.region, unassigned_history=count,
                        trends=trends_for_snapshot(history, snapshot, self.clock()) if snapshot else (),
                        history=history,
                    )
                    self._pending = True
                    self._next_run = None
                    self._alert_armed = self._source_alerts.pop(source, {})
                    self._wake.set()
                else:
                    # Only refresh interval / alert choices changed: the query
                    # source is untouched, so an in-flight result stays valid
                    # and must not be discarded. No revision bump, no forced
                    # re-query; the new interval takes effect at the next
                    # completion, which reschedules from the current settings.
                    self._state = replace(
                        self._state,
                        alert_threshold_percent=settings.alert.threshold_percent,
                    )
        if unchanged:
            self._publish()
            return
        self._publish()

    def get_state(self) -> MonitorViewState:
        with self._lock:
            return self._state

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        with self._lock:
            # Only a fully stopped loop may be restarted: if the join timed
            # out, the old thread is still running (a 15s network call easily
            # outlives the 2s join), and clearing state here would let start()
            # spawn a second concurrent loop. The cached client needs no
            # cleanup -- the loop's finally already retired it.
            if self._thread is not None and not self._thread.is_alive():
                self._thread = None
                self._stop.clear()
                self._wake.clear()
                self._in_flight = False
                self._pending = False
                self._next_run = None

    def _retire_client(self) -> None:
        """Close and drop the cached client. Call under the lock."""
        client, self._client, self._client_key = self._client, None, None
        close = getattr(client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                logger.exception("Could not close the quota client")

    def _run(self) -> None:
        try:
            self._run_loop()
        finally:
            # The loop thread owns the client; release its session on exit.
            with self._lock:
                self._retire_client()

    def _commit_snapshot(self, revision, temporary, path):
        # Serialize only the final rename with source switches. Encoding, I/O and
        # fsync have already finished outside the UI lock.
        with self._lock:
            if self._state.revision != revision:
                return False
            temporary.replace(path)
            return True

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            timeout: float | None = None
            with self._lock:
                if self._next_run is not None:
                    timeout = max(0.0, self._next_run - time.monotonic())
            self._wake.wait(timeout)
            self._wake.clear()
            if self._stop.is_set():
                return

            with self._lock:
                now = time.monotonic()
                due = self._pending or (
                    self._next_run is not None and now >= self._next_run
                )
                if not due or self._credentials is None:
                    continue
                self._pending = False
                self._in_flight = True
                revision = self._state.revision
                settings = self._settings
                credentials = self._credentials
                previous_snapshot = self._state.snapshot
                # Rebuild only when the query source changes. Constructors
                # are cheap (no network), so this stays under the lock; the
                # old session is closed here on the monitor thread, never on
                # the UI thread that calls update_configuration.
                key = (settings.provider, settings.region, credentials)
                if self._client is None or key != self._client_key:
                    self._retire_client()
                    self._client = self.client_factory(settings, credentials)
                    self._client_key = key
                client = self._client
                self._state = replace(self._state, is_refreshing=True)
            self._publish()

            try:
                snapshot = client.query()
                error: QueryError | None = None
            except QueryError as exc:
                snapshot = None
                error = exc
            except Exception:
                logger.exception("Unexpected quota refresh failure")
                snapshot = None
                error = QueryError("刷新失败，请稍后重试")

            cache_warning: str | None = None
            trends: tuple = ()
            history_rows: tuple = ()
            if snapshot is not None:
                with self._lock:
                    current = revision == self._state.revision
                if current:
                    try:
                        self.storage.save_snapshot(snapshot, QuotaSource.for_credentials(settings, credentials),
                                                   commit=partial(self._commit_snapshot, revision))
                    except CacheError as exc:
                        cache_warning = exc.message
                    # History is best-effort: a failed append is logged, never
                    # surfaced as a cache warning and never fails the refresh.
                    account = account_fingerprint(credentials.access_key_id)
                    entries = None
                    try:
                        # One read per refresh: append_history returns the
                        # retained rows (all accounts), so trends and the raw
                        # state rows come from the same parse -- the file is
                        # no longer read twice back-to-back.
                        entries = self.storage.append_history(
                            entry_from_snapshot(snapshot, account),
                            settings.provider, region=settings.region,
                        )
                    except CacheError as exc:
                        logger.warning("Could not append history: %s", exc.message)
                    if entries is None:
                        # Append failed (e.g. disk full); a plain read may still
                        # work, so history survives a write-only fault.
                        try:
                            entries = self.storage.load_history(
                                settings.provider, account, limit=HISTORY_MAX_ROWS, region=settings.region
                            )
                        except CacheError as exc:
                            logger.warning("Could not load history for trends: %s", exc.message)
                    if entries is not None:
                        # The full retention cap (not the 2000-row default):
                        # a 90-day chart at the 15-minute cadence needs ~8640
                        # rows. Filtering/slicing in memory is free -- the
                        # rows are already parsed.
                        history = [
                            item for item in entries if item.account == account
                        ][-HISTORY_MAX_ROWS:]
                        trends = trends_for_snapshot(history, snapshot, self.clock())
                        history_rows = tuple(history)

            events: list[AlertEvent] = []
            with self._lock:
                self._in_flight = False
                # A matching revision means the query source never changed, so
                # only alert/interval choices can differ from the captured
                # settings -- publish and evaluate with the CURRENT ones so a
                # threshold saved mid-flight lands with this very result.
                current_settings = self._settings
                if revision != self._state.revision:
                    # A source change already queued another refresh. Never
                    # publish data fetched with obsolete credentials/endpoint.
                    pass
                elif snapshot is not None:
                    self._state = MonitorViewState(
                        status="ok",
                        snapshot=snapshot,
                        is_refreshing=False,
                        error=cache_warning,
                        last_attempt_at=self.clock(),
                        revision=revision,
                        provider=settings.provider, region=settings.region,
                        unassigned_history=self._state.unassigned_history,
                        alert_threshold_percent=current_settings.alert.threshold_percent,
                        trends=trends,
                        history=history_rows,
                    )
                    events, self._alert_armed = evaluate(
                        previous_snapshot, snapshot, current_settings.alert,
                        self._alert_armed, settings.provider,
                    )
                    if events:
                        # Attach the fresh trend context so the notification
                        # can say when the quota is estimated to run out.
                        by_kind = {trend.kind: trend for trend in trends}
                        events = [
                            replace(
                                event,
                                exhausted_at=by_kind[event.kind].exhausted_at,
                                resets_before_exhaustion=by_kind[event.kind].resets_before_exhaustion,
                            ) if event.kind in by_kind else event
                            for event in events
                        ]
                else:
                    assert error is not None
                    previous = self._state.snapshot
                    self._state = MonitorViewState(
                        status="stale" if previous is not None else "error",
                        snapshot=previous,
                        is_refreshing=False,
                        error=error.message,
                        error_category=error.category,
                        last_attempt_at=self.clock(),
                        revision=revision,
                        provider=settings.provider, region=settings.region,
                        unassigned_history=self._state.unassigned_history,
                        alert_threshold_percent=current_settings.alert.threshold_percent,
                        # Like the retained snapshot, trends and history rows
                        # survive a failed refresh instead of blanking out.
                        trends=self._state.trends,
                        history=self._state.history,
                    )
                if self._credentials is None:
                    self._next_run = None
                else:
                    self._next_run = (
                        time.monotonic() + self._settings.refresh_minutes * 60
                    )
                if self._pending:
                    self._wake.set()
            self._publish()
            # Notifications go out after the state publish, off the lock, and
            # a broken notifier can never take the refresh loop down.
            for event in events:
                if self.notifier is None:
                    break
                try:
                    self.notifier(event)
                except Exception:
                    logger.exception("Alert notifier failed")

    def _publish(self) -> None:
        if self.on_state is None:
            return
        state = self.get_state()
        try:
            self.on_state(state)
        except Exception:
            logger.exception("State listener failed")
