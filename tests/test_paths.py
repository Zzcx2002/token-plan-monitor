"""Portable path resolution for source runs and the frozen Windows bundle."""
import sys

from token_plan_monitor import paths


def test_source_layout_uses_project_root():
    assert not getattr(sys, "frozen", False)
    assert paths.data_directory("win32") == paths.PROJECT_ROOT / "data"
    assert paths.log_directory("win32") == paths.PROJECT_ROOT / "logs"
    assert (paths.resource_path("assets", "app.ico")
            == paths.PROJECT_ROOT / "assets" / "app.ico")


def test_frozen_layout_sits_next_to_the_executable(monkeypatch, tmp_path):
    exe_dir = tmp_path / "TokenPlanMonitor"
    exe_dir.mkdir()
    exe = exe_dir / "TokenPlanMonitor.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert paths.data_directory("win32") == exe_dir / "data"
    assert paths.log_directory("win32") == exe_dir / "logs"


def test_resource_path_uses_the_meipass_bundle(monkeypatch, tmp_path):
    bundle = tmp_path / "_internal"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    assert (paths.resource_path("assets", "app.ico")
            == bundle / "assets" / "app.ico")


def test_frozen_without_meipass_falls_back_to_project_root(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    assert paths.resource_path("assets") == paths.PROJECT_ROOT / "assets"


def test_macos_paths_ignore_the_frozen_flag(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    home = tmp_path / "home"
    assert paths.data_directory("darwin", home) == home / (
        "Library/Application Support/TokenPlanMonitor")
    assert paths.log_directory("darwin", home) == home / (
        "Library/Logs/TokenPlanMonitor")
