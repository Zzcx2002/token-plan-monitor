"""Interactive notification-chain probe, launched as a real app.

Authorization prompts and banner delivery need a LaunchServices launch
(``open -W TokenPlanMonitor.app --args --notify-probe``); exec'ing the bundle
interpreter directly bypasses that and the system cancels the request. Same
dev-entry class as --preview / --smoke-test, bundle-only by construction.

Guided chain, one launch per authorization state:
  fresh: not_determined -> system prompt -> Allow -> UN banner -> click;
  denied in System Settings: legacy banner -> click (fallback proof).
Results land in <log dir>/notify-probe.txt for the launcher script to read.
"""

from __future__ import annotations

import time

import AppKit as A
import Foundation as F

from ..alerts import AlertEvent
from ..paths import log_directory
from .notifications import MacNotifier


def pump(seconds: float) -> None:
    """Run the main run loop so delegate callbacks and main-queue blocks land."""
    loop = F.NSRunLoop.currentRunLoop()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        loop.runMode_beforeDate_(
            F.NSDefaultRunLoopMode, F.NSDate.dateWithTimeIntervalSinceNow_(0.2)
        )


def run() -> int:
    lines: list[str] = []

    def say(text: str) -> None:
        lines.append(text)
        print(text, flush=True)

    app = A.NSApplication.sharedApplication()
    app.setActivationPolicy_(A.NSApplicationActivationPolicyAccessory)
    # The real app gets this from app.run(); without it the notification
    # presentation channel never comes up and banners silently never show.
    app.finishLaunching()
    clicks: list[float] = []
    presented: list[float] = []
    responded: list[float] = []
    notifier = MacNotifier(on_click=lambda: clicks.append(time.monotonic()))
    notifier.on_present = lambda: presented.append(time.monotonic())
    notifier.on_response = lambda: responded.append(time.monotonic())
    pump(0.5)
    status = notifier.authorization_status()
    say(f"[1] initial status: {status}")
    if notifier._center is not None:
        box = []
        # A plain lambda: PyObjC cannot wrap builtin methods like list.append
        # into an ObjC block.
        notifier._center.getNotificationSettingsWithCompletionHandler_(lambda s: box.append(s))
        pump(1.0)
        if box:
            s = box[0]
            # UNAlertSetting: 0=none 1=banner 2=alert;
            # UNNotificationSetting: 0=disabled 1=enabled 2=notSupported.
            say(f"[1s] alert={s.alertSetting()} center={s.notificationCenterSetting()} "
                f"lock={s.lockScreenSetting()} sound={s.soundSetting()} "
                f"badge={s.badgeSetting()}")
    if status == "unavailable":
        say("guard rejected: not running inside an app bundle")
        code = 1
    else:
        if status in ("not_determined", "denied"):
            say("[2] requesting authorization -- click 允许/Allow if a system prompt appears")
            notifier.request_authorization()
            for _ in range(120 if status == "not_determined" else 6):
                pump(0.5)
                if status == "not_determined" and notifier.authorization_status() in ("authorized", "denied"):
                    break
            status = notifier.authorization_status()
        say(f"[3] status now: {status}")
        if notifier.authorization_error:
            say(f"[3e] authorization error: {notifier.authorization_error}")
        say("[4] delivering a low-quota test notification -- click the banner")
        notifier.notify(
            AlertEvent("low_quota", "five_hour", "volcengine", 8.0, 10, "Agent Plan")
        )
        for _ in range(80):
            pump(0.5)
            if clicks:
                break
        say(f"[5] click received: {bool(clicks)} "
            f"(presented={bool(presented)}, response={bool(responded)})")
        say(f"[6] final status: {notifier.authorization_status()}")
        code = 0 if clicks else 2
    say(f"exit={code}")
    try:
        directory = log_directory()
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "notify-probe.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass
    return code