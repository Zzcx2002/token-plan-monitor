"""Launch the bundled notification-chain probe and report its result.

    .venv/bin/python scripts/verify_macos_notifications.py [path/to/App.app]

Defaults to dist/TokenPlanMonitor.app; pass /Applications/TokenPlanMonitor.app
to verify the installed copy (notification authorization is per app identity,
so the installed copy and a fresh dist build can hold different decisions).

Uses `open -W` so the app launches through LaunchServices -- authorization
prompts and banner delivery do not work when the bundle interpreter is
exec'd directly. The probe (--notify-probe, a dev entry like --preview)
guides the chain on the system UI and writes its transcript to
<log dir>/notify-probe.txt; this launcher waits for the app to exit and
prints that transcript.

One launch per authorization state:
  fresh bundle: prompt -> Allow -> UN banner -> click -> exit 0;
  after denying in System Settings -> Notifications: legacy banner -> click
  -> exit 0 (fallback proof).
Exit 2 = no click arrived; exit 1 = guard rejected the environment.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist/TokenPlanMonitor.app"


def main() -> int:
    if not APP.exists():
        print("build the bundle first: .venv-build/bin/python scripts/build_macos.py")
        return 1
    sys.path.insert(0, str(ROOT / "src"))
    from token_plan_monitor.paths import log_directory

    transcript = log_directory() / "notify-probe.txt"
    transcript.unlink(missing_ok=True)
    print("launching probe; follow the system prompts (Allow, then click the banner)", flush=True)
    subprocess.run(["open", "-W", str(APP), "--args", "--notify-probe"], check=True)
    if transcript.exists():
        print(transcript.read_text(encoding="utf-8"), end="")
        last = transcript.read_text(encoding="utf-8").strip().splitlines()[-1]
        return int(last.split("=", 1)[1]) if last.startswith("exit=") else 2
    print("no transcript: probe did not run (stale bundle without --notify-probe?)")
    return 2


if __name__ == "__main__":
    sys.exit(main())