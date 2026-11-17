"""Build the standalone Windows onedir app, then zip it with a sha256 digest.

Onedir (not onefile) keeps the pystray/keyring backends as plain files, avoids
the per-launch temp-directory extraction that antivirus tools flag, and makes
the portable ``data/`` and ``logs/`` folders sit predictably next to the exe.
Artifact names are derived from the package version, so they cannot drift from
``token_plan_monitor.__version__`` the way a hardcoded label could.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "TokenPlanMonitor"
# A package-external launcher: PyInstaller otherwise runs __main__.py as a
# top-level script and its relative imports fail with no known parent package.
ENTRY = ROOT / "scripts" / "windows_launcher.py"
# Backends imported through backend-selection indirection: PyInstaller's static
# analysis cannot see them, so name them explicitly.
HIDDEN_IMPORTS = (
    "pystray._win32",
    "keyring.backends.Windows",
)
NOTICE_FILES = ("THIRD_PARTY_NOTICES.md", "licenses")


def run(*args: str) -> None:
    subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True)


def remove_tree(path: Path, attempts: int = 6) -> None:
    """rmtree with retries: Windows Defender briefly locks freshly built exes."""
    for index in range(attempts):
        try:
            shutil.rmtree(path)
            return
        except PermissionError:
            if index == attempts - 1:
                raise
            time.sleep(1.5 * (index + 1))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_application() -> Path:
    work_app = ROOT / "dist" / APP_NAME
    for path in (ROOT / "build", work_app):
        if path.exists():
            remove_tree(path)
    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--windowed",
        "--name", APP_NAME,
        "--icon", str(ROOT / "assets" / "app.ico"),
        "--add-data", f"{ROOT / 'assets'}{os.pathsep}assets",
        # Keep the generated spec inside the gitignored build directory.
        "--specpath", str(ROOT / "build"),
    ]
    for module in HIDDEN_IMPORTS:
        command += ["--hidden-import", module]
    command.append(str(ENTRY))
    run(*command)
    exe = work_app / f"{APP_NAME}.exe"
    bundled_icon = work_app / "_internal" / "assets" / "app.ico"
    if not exe.is_file():
        raise RuntimeError(f"PyInstaller did not produce {exe}")
    if not bundled_icon.is_file():
        raise RuntimeError(f"Missing bundled icon at {bundled_icon}")
    print(f"Built {exe}")
    return work_app


def bundle_notices(work_app: Path) -> None:
    for name in NOTICE_FILES:
        source = ROOT / name
        target = work_app / name
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
    missing = [name for name in NOTICE_FILES if not (work_app / name).exists()]
    if missing:
        raise RuntimeError(f"Missing legal resources: {', '.join(missing)}")
    print("Bundled third-party notices and license texts")


def run_self_check(work_app: Path) -> None:
    """Deterministic in-exe probe: backends, resources, storage, keyring."""
    exe = work_app / f"{APP_NAME}.exe"
    result = subprocess.run([str(exe), "--self-check"], cwd=work_app, timeout=90)
    report = work_app / "self-check-result.txt"
    if report.is_file():
        print(report.read_text(encoding="utf-8").rstrip())
    if result.returncode != 0:
        raise RuntimeError(f"Frozen self-check failed (exit {result.returncode})")
    report.unlink(missing_ok=True)


def probe_tray_launch(work_app: Path) -> None:
    """Launch the real app headless-style: it must stay alive and log cleanly."""
    exe = work_app / f"{APP_NAME}.exe"
    process = subprocess.Popen([str(exe)], cwd=work_app)
    try:
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            time.sleep(1)
            if process.poll() is not None:
                raise RuntimeError(
                    f"Tray process exited early with code {process.returncode}")
        log_path = work_app / "logs" / "app.log"
        if not log_path.is_file():
            raise RuntimeError("The tray launch created no log file")
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
        if "Traceback" in log_text:
            raise RuntimeError(f"Traceback in tray log:\n{log_text}")
        print("Tray launch probe: process alive for 12s, log clean")
    finally:
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False, capture_output=True,
        )
        deadline = time.monotonic() + 5
        while process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.2)


def package(work_app: Path, version: str) -> tuple[Path, Path]:
    # The distributed folder starts clean: probes only needed writable paths to
    # prove they work; the app recreates them on first real launch.
    for name in ("data", "logs"):
        if (work_app / name).exists():
            remove_tree(work_app / name)
    staged = ROOT / "dist" / f"{APP_NAME}-{version}-win64"
    if staged.exists():
        remove_tree(staged)
    shutil.copytree(work_app, staged)
    archive_base = ROOT / "dist" / f"{APP_NAME}-{version}-win64"
    zip_path = Path(shutil.make_archive(str(archive_base), "zip", ROOT / "dist", staged.name))
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        if f"{staged.name}/{APP_NAME}.exe" not in names:
            raise RuntimeError("The zip is missing the application executable")
    digest = sha256_file(zip_path)
    checksum_path = zip_path.with_suffix(".zip.sha256")
    # Bytes, not text mode: Windows newline translation would make the LF line
    # ending CRLF and break `sha256sum -c` on the published artifact.
    checksum_path.write_bytes(f"{digest}  {zip_path.name}\n".encode("ascii"))
    verified = sha256_file(zip_path)
    if verified != digest:
        raise RuntimeError("Checksum verification failed")
    print(f"Packaged {zip_path}")
    print(f"Wrote {checksum_path} ({digest})")
    return zip_path, checksum_path


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("Build the Windows package on Windows")
    if sys.version_info[:2] != (3, 13):
        raise SystemExit("Python 3.13 is required for the pinned runtime")
    from token_plan_monitor import __version__

    work_app = build_application()
    bundle_notices(work_app)
    run_self_check(work_app)
    probe_tray_launch(work_app)
    package(work_app, __version__)


if __name__ == "__main__":
    main()
