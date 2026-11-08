"""Native effects fail closed: no transparent holes or faded foreground."""
import sys
import pytest

if sys.platform != "win32":
    pytest.skip("Windows native effects", allow_module_level=True)

import ctypes
import tkinter as tk
from types import SimpleNamespace

from token_plan_monitor import ui_graphics as graphics


class NativeCall:
    def __init__(self, result=0):
        self.result = result
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.result


class Window:
    def __init__(self, reject_colour=False):
        self.options = {"-transparentcolor": "old-colour", "-alpha": 1.0}
        self.reject_colour = reject_colour

    def update_idletasks(self):
        pass

    def winfo_id(self):
        return 123

    def attributes(self, key, value):
        if self.reject_colour and value:
            raise tk.TclError("colour key unavailable")
        self.options[key] = value


def native(monkeypatch, *, composition=1):
    user = SimpleNamespace(GetAncestor=NativeCall(456), GetClassLongPtrW=NativeCall(),
                           SetClassLongPtrW=NativeCall(),
                           SetWindowCompositionAttribute=NativeCall(composition))
    dwm = SimpleNamespace(DwmSetWindowAttribute=NativeCall(),
                          DwmExtendFrameIntoClientArea=NativeCall())
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=user, dwmapi=dwm))
    return user, dwm


def test_uses_outer_hwnd_and_never_fades_foreground(monkeypatch):
    user, dwm = native(monkeypatch)
    monkeypatch.setattr(graphics, "transparency_enabled", lambda: True)
    window = Window()
    result = graphics.apply_window_effects(window, "#F2F3F6")
    assert result.glass and result.rounded
    assert dwm.DwmSetWindowAttribute.calls[0][0].value == 456
    assert window.options == {"-transparentcolor": "#F2F3F6", "-alpha": 1.0}


def test_disabled_transparency_restores_solid_background(monkeypatch):
    user, _ = native(monkeypatch)
    monkeypatch.setattr(graphics, "transparency_enabled", lambda: False)
    window = Window()
    assert not graphics.apply_window_effects(window, "#F2F3F6").glass
    assert window.options["-transparentcolor"] == ""
    assert not user.SetWindowCompositionAttribute.calls


def test_rejected_composition_keeps_solid_background(monkeypatch):
    native(monkeypatch, composition=0)
    monkeypatch.setattr(graphics, "transparency_enabled", lambda: True)
    window = Window()
    assert not graphics.apply_window_effects(window, "#F2F3F6").glass
    assert window.options["-transparentcolor"] == ""


def test_colour_key_failure_disables_composition(monkeypatch):
    user, _ = native(monkeypatch)
    monkeypatch.setattr(graphics, "transparency_enabled", lambda: True)
    window = Window(reject_colour=True)
    assert not graphics.apply_window_effects(window, "#F2F3F6").glass
    assert len(user.SetWindowCompositionAttribute.calls) == 2
    assert window.options["-transparentcolor"] == ""


def test_unavailable_native_api_keeps_solid_background(monkeypatch):
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace())
    window = Window()
    assert graphics.apply_window_effects(window, "#F2F3F6") == graphics.WindowEffects()
    assert window.options["-transparentcolor"] == ""
