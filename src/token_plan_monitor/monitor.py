from __future__ import annotations

import logging
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime
from typing import Callable

from .models import (
    CacheError,
    ConfigurationError,
    Credentials,
    MonitorViewState,
    QueryError,
    Settings,
)
from .storage import Storage
from .volcengine import VolcengineClient

logger = logging.getLogger(__name__)


class UsageMonitor:
    def __init__(
        self,
        storage: Storage,
        client_factory: Callable[..., VolcengineClient] = VolcengineClient,
        on_state: Callable[[MonitorViewState], None] | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.storage = storage
        self.client_factory = client_factory
        self.on_state = on_state
        self.clock = clock or (lambda: datetime.now(UTC))

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

    def start(self) -> None:
        with self._lock:
            if self._thread is not None:
                return
            self._settings = self.storage.load_settings()
            snapshot = self.storage.load_snapshot()
            try:
                self._credentials = self.storage.load_credentials()
                error = None
            except ConfigurationError as exc:
                self._credentials = None
                error = exc.message
            status = (
                "unconfigured"
                if self._credentials is None
                else "ok" if snapshot is not None else "empty"
            )
            self._state = MonitorViewState(
                status=status,
                snapshot=snapshot,
                error=error,
                error_category="configuration" if error else None,
                revision=0,
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
        should_clear_cache = False
        with self._lock:
            account_changed = credentials != self._credentials
            self._settings = settings
            self._credentials = credentials
            self._state = replace(
                self._state,
                status="empty" if account_changed else self._state.status,
                snapshot=None if account_changed else self._state.snapshot,
                error=None,
                error_category=None,
                revision=self._state.revision + 1,
            )
            self._pending = True
            self._next_run = None
            should_clear_cache = account_changed
            self._wake.set()
        if should_clear_cache:
            try:
                self.storage.clear_snapshot()
            except CacheError as exc:
                logger.warning("Could not clear old snapshot: %s", exc.message)
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

    def _run(self) -> None:
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
                self._state = replace(self._state, is_refreshing=True)
            self._publish()

            try:
                snapshot = self.client_factory(settings, credentials).query()
                error: QueryError | None = None
            except QueryError as exc:
                snapshot = None
                error = exc
            except Exception:
                logger.exception("Unexpected quota refresh failure")
                snapshot = None
                error = QueryError("刷新失败，请稍后重试")

            cache_warning: str | None = None
            if snapshot is not None:
                with self._lock:
                    current = revision == self._state.revision
                if current:
                    try:
                        self.storage.save_snapshot(snapshot)
                    except CacheError as exc:
                        cache_warning = exc.message

            with self._lock:
                self._in_flight = False
                if revision != self._state.revision:
                    # A settings change already queued another refresh. Never publish
                    # data fetched with obsolete credentials.
                    pass
                elif snapshot is not None:
                    self._state = MonitorViewState(
                        status="ok",
                        snapshot=snapshot,
                        is_refreshing=False,
                        error=cache_warning,
                        last_attempt_at=self.clock(),
                        revision=revision,
                    )
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

    def _publish(self) -> None:
        if self.on_state is None:
            return
        state = self.get_state()
        try:
            self.on_state(state)
        except Exception:
            logger.exception("State listener failed")
