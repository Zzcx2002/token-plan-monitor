"""MacNotifier decision logic with injected fake centers (review P0-3).

The UN authorization/settings callbacks are async in production; the fakes
land settings synchronously and let each test fire the authorization answer
by hand, which is exactly the race the pending queue exists for.
"""

import sys
import types

import pytest

from token_plan_monitor.alerts import AlertEvent
from token_plan_monitor.macos import notifications as nt


def make_event(kind="five_hour", category="low_quota", percent=8.0):
    return AlertEvent(category, kind, "volcengine", percent, 10, "Agent Plan")


class FakeSettings:
    def __init__(self, status):
        self._status = status

    def authorizationStatus(self):
        return self._status


class FakeUNCenter:
    def __init__(self, status):
        self.status = status
        self.added = []
        self.auth_handlers = []
        self.delegate = None

    def setDelegate_(self, delegate):
        self.delegate = delegate

    def getNotificationSettingsWithCompletionHandler_(self, handler):
        handler(FakeSettings(self.status))

    def requestAuthorizationWithOptions_completionHandler_(self, options, handler):
        self.auth_handlers.append(handler)

    def addNotificationRequest_withCompletionHandler_(self, request, handler):
        self.added.append((request, handler))


class FakeLegacyCenter:
    def __init__(self):
        self.delivered = []
        self.delegate = None

    def setDelegate_(self, delegate):
        self.delegate = delegate

    def deliverNotification_(self, note):
        self.delivered.append(note)


class FakeNote:
    def __init__(self):
        self.title = None
        self.text = None

    def init(self):
        return self

    def setTitle_(self, value):
        self.title = value

    def setInformativeText_(self, value):
        self.text = value


def fake_un_module():
    mod = types.ModuleType("UserNotifications")
    mod.UNAuthorizationStatusNotDetermined = 0
    mod.UNAuthorizationStatusDenied = 1
    mod.UNAuthorizationStatusAuthorized = 2
    mod.UNAuthorizationOptionAlert = 1
    mod.UNAuthorizationOptionSound = 2
    mod.UNNotificationInterruptionLevelTimeSensitive = "time-sensitive"

    class Content:
        def __init__(self):
            self.title = self.body = self.sound = self.interruption = None

        def init(self):
            return self

        def setTitle_(self, value):
            self.title = value

        def setBody_(self, value):
            self.body = value

        def setSound_(self, value):
            self.sound = value

        def setInterruptionLevel_(self, value):
            self.interruption = value

    mod.UNMutableNotificationContent = types.SimpleNamespace(alloc=Content)
    mod.UNNotificationSound = types.SimpleNamespace(defaultSound=lambda: "default-sound")
    mod.UNNotificationRequest = types.SimpleNamespace(
        requestWithIdentifier_content_trigger_=lambda ident, content, trigger: (ident, content, trigger)
    )
    return mod


@pytest.fixture
def cocoa(monkeypatch):
    """Fake AppKit/Foundation: legacy delivery runs synchronously on 'main'."""
    legacy_center = FakeLegacyCenter()
    fake_a = types.SimpleNamespace(
        NSUserNotification=types.SimpleNamespace(alloc=FakeNote),
        NSUserNotificationCenter=types.SimpleNamespace(
            defaultUserNotificationCenter=lambda: legacy_center
        ),
    )
    fake_f = types.SimpleNamespace(
        NSOperationQueue=types.SimpleNamespace(
            mainQueue=lambda: types.SimpleNamespace(
                addOperationWithBlock_=lambda block: block()
            )
        ),
    )
    monkeypatch.setattr(nt, "A", fake_a)
    monkeypatch.setattr(nt, "F", fake_f)
    return legacy_center


def make_notifier(monkeypatch, status, legacy_center=None):
    monkeypatch.setitem(sys.modules, "UserNotifications", fake_un_module())
    center = FakeUNCenter(status)
    notifier = nt.MacNotifier(center=center, legacy_center=legacy_center)
    return notifier, center


def test_undetermined_alert_is_held_then_delivered_on_grant(cocoa, monkeypatch):
    notifier, center = make_notifier(monkeypatch, 0)
    notifier.notify(make_event())
    # Nothing delivered yet, the prompt is up, the alert is held.
    assert center.added == []
    assert len(center.auth_handlers) == 1
    assert len(notifier._pending) == 1

    center.auth_handlers[0](True, None)

    assert notifier.authorization_status() == "authorized"
    assert notifier._pending == []
    assert len(center.added) == 1
    ident, content, _trigger = center.added[0][0]
    assert ident.startswith("low_quota-five_hour-")
    assert content.interruption == "time-sensitive"
    assert content.title


def test_undetermined_alert_flushes_to_legacy_on_denial(cocoa, monkeypatch):
    notifier, center = make_notifier(monkeypatch, 0, legacy_center=cocoa)
    notifier.notify(make_event())
    center.auth_handlers[0](False, None)
    assert center.added == []
    assert len(cocoa.delivered) == 1
    assert cocoa.delivered[0].title
    assert notifier._pending == []


def test_pending_dedupes_by_window_and_is_bounded(cocoa, monkeypatch):
    notifier, center = make_notifier(monkeypatch, 0)
    notifier.notify(make_event(kind="five_hour", percent=8.0))
    notifier.notify(make_event(kind="five_hour", percent=5.0))
    # Newest observation per (category, kind) wins.
    assert len(notifier._pending) == 1
    assert notifier._pending[0].remaining_percent == 5.0
    for index in range(10):
        notifier.notify(make_event(kind=f"window-{index}"))
    assert len(notifier._pending) == notifier._PENDING_LIMIT


def test_un_delivery_error_falls_back_to_legacy(cocoa, monkeypatch):
    notifier, center = make_notifier(monkeypatch, 2, legacy_center=cocoa)
    notifier.notify(make_event())
    assert len(center.added) == 1
    # The cached status said authorized, but the system rejected the request
    # (e.g. the user just denied in System Settings): legacy gets one try.
    center.added[0][1](object())
    assert len(cocoa.delivered) == 1


def test_denied_status_tries_legacy_even_without_a_prebuilt_center(cocoa, monkeypatch):
    notifier, center = make_notifier(monkeypatch, 1, legacy_center=None)
    notifier.notify(make_event())
    assert center.added == []
    # The legacy center is created lazily on the main thread.
    assert len(cocoa.delivered) == 1


def test_authorized_delivery_uses_un_with_unique_ids(cocoa, monkeypatch):
    notifier, center = make_notifier(monkeypatch, 2)
    notifier.notify(make_event(kind="five_hour"))
    notifier.notify(make_event(kind="weekly"))
    assert len(center.added) == 2
    assert cocoa.delivered == []
    ids = [request[0] for request, _handler in center.added]
    assert len(set(ids)) == 2


def test_grant_with_no_pending_alerts_delivers_nothing(cocoa, monkeypatch):
    notifier, center = make_notifier(monkeypatch, 0)
    notifier.request_authorization()
    center.auth_handlers[0](True, None)
    assert center.added == []
    assert notifier.authorization_status() == "authorized"
