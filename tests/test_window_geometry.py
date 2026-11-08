import json
from unittest.mock import Mock

import pytest

from token_plan_monitor.models import CacheError
from token_plan_monitor.storage import Storage
from token_plan_monitor.window_geometry import WindowGeometry


@pytest.mark.parametrize("value", [None, [], [1], [True, 200], [0, 20],
                                      [-1, 20], ["400", 200], [float('inf'), 200]])
def test_invalid_geometry_is_ignored(tmp_path, value):
    storage = Storage(tmp_path)
    storage.geometry_path.write_text(json.dumps({"popup": value}), encoding="utf-8")
    assert storage.load_window_size("popup") is None


def test_geometry_roundtrip_preserves_other_windows(tmp_path):
    storage = Storage(tmp_path)
    storage.save_window_size("popup", (430.5, 500))
    storage.save_window_size("widget", (320, 160))
    assert Storage(tmp_path).load_window_size("popup") == (430.5, 500)
    assert storage.load_window_size("widget") == (320, 160)
    assert not storage.config_path.exists()
    assert not storage.widget_path.exists()


def test_restore_scales_and_clamps_to_work_area(tmp_path, monkeypatch):
    from token_plan_monitor import window_geometry as module
    storage = Storage(tmp_path)
    storage.save_window_size("popup", (1.7e308, 1600))
    window = Mock()
    monkeypatch.setattr(module, "dpi_scale", lambda _: 1.5)
    monkeypatch.setattr(module, "window_work_area", lambda _: (0, 0, 1200, 900))
    controller = WindowGeometry(window, storage, "popup", (360, 220))
    controller.fit((440, 500))
    window.geometry.assert_called_once_with("1180x840")
    controller.fit((500, 700))
    assert window.geometry.call_count == 1


def test_save_failure_retains_session_size(tmp_path, monkeypatch, caplog):
    from token_plan_monitor import window_geometry as module
    window = Mock()
    window.winfo_width.return_value = 600
    window.winfo_height.return_value = 400
    monkeypatch.setattr(module, "dpi_scale", lambda _: 1.5)
    storage = Storage(tmp_path)
    storage.save_window_size = Mock(side_effect=CacheError("disk full"))
    controller = WindowGeometry(window, storage, "popup", (360, 220))
    controller.initialized = True
    controller.flush()
    assert controller.size == (400, 400/1.5)
    assert "disk full" in caplog.text


@pytest.mark.parametrize("state", ["zoomed", "iconic"])
def test_non_normal_window_does_not_overwrite_restored_size(tmp_path, state):
    storage = Storage(tmp_path)
    storage.save_window_size("popup", (440, 500))
    window = Mock()
    window.state.return_value = state
    window.tk.call.return_value = 96/72
    controller = WindowGeometry(window, storage, "popup", (360, 220))
    controller.initialized = True
    controller.flush()
    assert storage.load_window_size("popup") == (440, 500)
