"""Build a standalone arm64 app and a drag-to-Applications disk image."""
from __future__ import annotations

import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import sysconfig

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "dist/TokenPlanMonitor.app"


def run(*args, **kwargs):
    subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True, **kwargs)


def thin_binaries(app):
    # py2app's --arch does not thin every copied framework executable.
    for path in app.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        with path.open("rb") as handle:
            if handle.read(4) not in (b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"):
                continue
        temporary = path.with_name(path.name + ".arm64")
        run("lipo", path, "-thin", "arm64", "-output", temporary)
        temporary.chmod(path.stat().st_mode)
        temporary.replace(path)


def verify_binaries(app):
    """Reject wrong architectures or dependencies requiring macOS newer than 14."""
    count = 0
    for path in app.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        with path.open("rb") as handle:
            magic = handle.read(4)
        if magic not in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"):
            continue
        archs = subprocess.check_output(["lipo", "-archs", str(path)], text=True).strip().split()
        if archs != ["arm64"]:
            raise RuntimeError(f"Expected arm64 binary: {path}: {archs}")
        commands = subprocess.check_output(["otool", "-l", str(path)], text=True).splitlines()
        for index, line in enumerate(commands):
            if "LC_BUILD_VERSION" in line or "LC_VERSION_MIN_MACOSX" in line:
                for entry in commands[index + 1:index + 6]:
                    parts = entry.split()
                    if parts and parts[0] in ("minos", "version"):
                        version = tuple(int(part) for part in parts[1].split("."))
                        if version[:2] > (14, 0):
                            raise RuntimeError(f"Binary requires macOS {parts[1]}: {path}")
                        break
        dependencies = subprocess.check_output(["otool", "-L", str(path)], text=True).splitlines()[1:]
        for line in dependencies:
            dependency = line.strip().split(" (")[0]
            if not dependency.startswith(("@", "/usr/lib/", "/System/Library/")):
                raise RuntimeError(f"Nonportable dependency in {path}: {dependency}")
        count += 1
    if not count:
        raise RuntimeError("No Mach-O binaries found")
    print(f"Verified {count} arm64 binaries, deployment targets <= macOS 14")


def verify_resources(app):
    resources = app / "Contents/Resources"
    required = [
        resources / "LICENSE",
        resources / "THIRD_PARTY_NOTICES.md",
        resources / "licenses/Python-3.13.9.txt",
        resources / "licenses/Certifi-2026.10.23.txt",
        resources / "licenses/Pillow-12.3.0-and-native-libraries.txt",
        resources / "licenses/PyObjC-12.2.2.txt",
        resources / "licenses/OpenSSL-3.0.18.txt",
        resources / "licenses/ZlibNG-2.3.3.md",
    ]
    missing = [str(path.relative_to(app)) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(f"Missing bundled license resources: {', '.join(missing)}")
    print("Verified project and third-party license resources")


def main():
    if sys.platform != "darwin" or platform.machine() != "arm64" or sys.version_info[:2] != (3, 13):
        raise SystemExit("Use Python 3.13 on an Apple Silicon Mac")
    if not sysconfig.get_config_var("PYTHONFRAMEWORK"):
        raise SystemExit("py2app requires a framework Python: use the official python.org Python 3.13 installer, not uv's standalone Python")
    # Only generated directories owned by this script are replaced.
    for path in (ROOT / "build", APP, ROOT / "dist/dmg-stage"):
        if path.exists():
            shutil.rmtree(path)
    icon = ROOT / "build/assets/app.icns"
    icon.parent.mkdir(parents=True, exist_ok=True)
    from token_plan_monitor.assets import make_brand_icon
    make_brand_icon(1024).save(icon, format="ICNS")
    env = dict(os.environ, MACOSX_DEPLOYMENT_TARGET="14.0", PYTHONNOUSERSITE="1")
    run(sys.executable, "setup_macos.py", "py2app", env=env)
    generated = ROOT / "dist/macos_launcher.app"
    if generated.exists():
        generated.rename(APP)
    thin_binaries(APP)
    verify_binaries(APP)
    verify_resources(APP)
    run("codesign", "--force", "--deep", "--sign", "-", APP)
    run("codesign", "--verify", "--deep", "--strict", APP)
    stage = ROOT / "dist/dmg-stage"
    stage.mkdir()
    shutil.copytree(APP, stage / APP.name, symlinks=True)
    (stage / "Applications").symlink_to("/Applications")
    run("hdiutil", "create", "-volname", "TokenPlanMonitor", "-srcfolder", stage,
        "-ov", "-format", "UDZO", ROOT / "dist/TokenPlanMonitor-0.1.0-arm64.dmg")
    print(f"Built {APP}")


if __name__ == "__main__":
    main()
