"""System notifications for quota alerts: UN first, legacy fallback, guard.

UNUserNotificationCenter needs a real .app bundle: a bare Python process has
no bundle proxy and ``currentNotificationCenter()`` raises
NSInternalInconsistencyException (verified on-device). Source runs and
--preview therefore never touch the UN API -- ``un_available`` says so and
the settings window reports it. Local notifications need no entitlement and
work under the project's ad-hoc signature; a rebuild changes the cdhash and
may reset the authorization, so the status is re-checked before every send.
"""

from __future__ import annotations

import logging

import AppKit as A
import Foundation as F
import objc

from ..alerts import AlertEvent
from ..models import provider_label
from ..presentation import alert_notification

logger = logging.getLogger(__name__)


def un_available(bundle_id: str | None, bundle_path: str | None) -> bool:
    """Pure guard: only a bundled app with an identifier may use UN."""
    return bool(bundle_id) and bool(bundle_path) and str(bundle_path).endswith(".app")


def delivery_path(center, legacy_center, status: str) -> str:
    """Pure choice between the three delivery paths, testable without OS calls.

    ``status`` is the UN authorization status; a denied center falls back to
    the deprecated NSUserNotification, and no center at all stays silent.
    """
    if center is not None and status != "denied":
        return "un"
    if legacy_center is not None:
        return "legacy"
    return "silent"


class _NotificationDelegate(A.NSObject):
    """Routes notification clicks back to the owner on the main thread."""

    def initWithOwner_(self, owner):
        self = objc.super(_NotificationDelegate, self).init()
        if self is None:
            return None
        self._owner = owner
        return self

    # UNUserNotificationCenterDelegate
    def userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(
        self, center, response, handler
    ):
        self._owner._click_on_main_thread()
        handler()

    def userNotificationCenter_willPresentNotification_withCompletionHandler_(
        self, center, notification, handler
    ):
        import UserNotifications as UN

        handler(UN.UNNotificationPresentationOptionBanner | UN.UNNotificationPresentationOptionSound)

    # NSUserNotificationCenterDelegate (legacy fallback)
    def userNotificationCenter_didActivateNotification_(self, center, notification):
        self._owner._click_on_main_thread()


class MacNotifier:
    """Implements the monitor's notifier protocol for macOS."""

    def __init__(self, on_click=None):
        self.on_click = on_click
        self._center = None
        self._legacy_center = None
        self._status = "unavailable"
        self._delegate = _NotificationDelegate.alloc().initWithOwner_(self)
        bundle = F.NSBundle.mainBundle()
        if not un_available(bundle.bundleIdentifier(), bundle.bundlePath()):
            logger.info("Notifications unavailable outside an app bundle")
            return
        try:
            import UserNotifications as UN

            self._UN = UN
            self._center = UN.UNUserNotificationCenter.currentNotificationCenter()
            self._center.setDelegate_(self._delegate)
            self._refresh_status()
        except Exception:
            logger.exception("UserNotifications unavailable; trying legacy path")
            self._center = None
        if self._center is None:
            try:
                self._legacy_center = A.NSUserNotificationCenter.defaultUserNotificationCenter()
                self._legacy_center.setDelegate_(self._delegate)
                self._status = "legacy"
            except Exception:
                logger.exception("Legacy notifications unavailable too")
                self._legacy_center = None

    def _refresh_status(self) -> None:
        if self._center is None:
            return

        def handler(settings):
            self._status = {
                self._UN.UNAuthorizationStatusAuthorized: "authorized",
                self._UN.UNAuthorizationStatusDenied: "denied",
                self._UN.UNAuthorizationStatusNotDetermined: "not_determined",
            }.get(settings.authorizationStatus(), "not_determined")

        self._center.getNotificationSettingsWithCompletionHandler_(handler)

    def authorization_status(self) -> str:
        """authorized / denied / not_determined / legacy / unavailable."""
        return self._status

    def request_authorization(self) -> None:
        if self._center is None:
            return

        def handler(granted, error):
            self._status = "authorized" if granted else "denied"

        try:
            self._center.requestAuthorizationWithOptions_completionHandler_(
                self._UN.UNAuthorizationOptionAlert | self._UN.UNAuthorizationOptionSound,
                handler,
            )
        except Exception:
            logger.exception("Could not request notification authorization")

    def _click_on_main_thread(self) -> None:
        if self.on_click is None:
            return
        F.NSOperationQueue.mainQueue().addOperationWithBlock_(self.on_click)

    def notify(self, event: AlertEvent) -> None:
        title, body = alert_notification(event, provider_label(event.provider))
        self._refresh_status()
        path = delivery_path(self._center, self._legacy_center, self._status)
        if path == "un":
            if self._status == "not_determined":
                # Ask once; this notification is dropped by the OS until the
                # user answers, the next one lands normally.
                self.request_authorization()
                return
            try:
                content = self._UN.UNMutableNotificationContent.alloc().init()
                content.setTitle_(title)
                content.setBody_(body)
                content.setSound_(self._UN.UNNotificationSound.defaultSound())
                request = self._UN.UNNotificationRequest.requestWithIdentifier_content_trigger_(
                    # Same identifier per window+category: a repeat replaces
                    # the previous banner instead of stacking duplicates.
                    f"{event.category}-{event.kind}", content, None,
                )
                self._center.addNotificationRequest_withCompletionHandler_(request, None)
            except Exception:
                logger.exception("Could not deliver UN notification")
            return
        if path == "legacy":
            def deliver():
                try:
                    note = A.NSUserNotification.alloc().init()
                    note.setTitle_(title)
                    note.setInformativeText_(body)
                    self._legacy_center.deliverNotification_(note)
                except Exception:
                    logger.exception("Could not deliver legacy notification")

            F.NSOperationQueue.mainQueue().addOperationWithBlock_(deliver)
            return
        logger.info("Notification suppressed (no delivery path): %s", title)