"""Native window effects: solid-by-default ClearType path, DWM corners, acrylic gating.

The Win32 surface is faked through ctypes: every call lands on a NativeCall
recorder, so assertions can inspect the exact window message, region handle or
accent tint that production code would hand to Windows.
"""
import sys
import pytest

if sys.platform != "win32":
    pytest.skip("Windows native effects", allow_module_level=True)

import ctypes
import tkinter as tk
from types import SimpleNamespace

from token_plan_monitor import ui_graphics as graphics
from token_plan_monitor.appearance import ACRYLIC_TINT_ALPHA, DARK, LIGHT, RADIUS


class NativeCall:
    def __init__(self, result=0):
        self.result = result
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.result


class FakeTk:
    """Minimal stand-in for the Tcl interpreter used by tk scaling queries."""

    def __init__(self, scaling=96 / 72):
        self.scaling = scaling
        self.set_calls = []

    def call(self, *args):
        if args[:2] == ("tk", "scaling"):
            if len(args) == 2:
                return self.scaling
            self.set_calls.append(args[2])
            self.scaling = args[2]
            return None
        raise AssertionError(f"unexpected tk call: {args}")

    def getboolean(self, value):
        # Mirrors Tcl's boolean coercion for the bool/"1" forms Tk may return.
        return str(value) in ("1", "true", "yes", "on", "True")


class Window:
    def __init__(self, *, reject_colour=False, width=400, height=300,
                 scaling=96 / 72):
        self.options = {"-transparentcolor": "old-colour", "-alpha": 1.0}
        self.reject_colour = reject_colour
        self._width, self._height = width, height
        self.tk = FakeTk(scaling)
        self.binds = []
        self.idle = []
        self.cancelled = []
        self.viewable = False

    def update_idletasks(self):
        pass

    def winfo_id(self):
        return 123

    def winfo_exists(self):
        return True

    def winfo_viewable(self):
        return int(self.viewable)

    def winfo_width(self):
        return self._width

    def winfo_height(self):
        return self._height

    def attributes(self, key, value):
        if self.reject_colour and value:
            raise tk.TclError("colour key unavailable")
        self.options[key] = value

    def overrideredirect(self):
        return "0"

    def bind(self, event, callback, add=False):
        self.binds.append((event, callback))

    def after_idle(self, callback):
        self.idle.append(callback)
        return f"idle-{len(self.idle)}"

    def after_cancel(self, token):
        self.cancelled.append(token)


class StrictWindow(Window):
    """Rejects every colour-key write, including the empty-string probe."""

    def attributes(self, key, value):
        if key == "-transparentcolor":
            raise tk.TclError("colour key unavailable")
        super().attributes(key, value)


class OverrideRedirectWindow(Window):
    """Stands in for the frameless card/hover surfaces (wm overrideredirect 1)."""

    def overrideredirect(self):
        return "1"


class RtlGetVersion:
    def __init__(self, build=22631, status=0):
        self.build = build
        self.status = status

    def __call__(self, reference):
        if self.status:
            return self.status
        reference._obj.dwBuildNumber = self.build
        return 0


@pytest.fixture(autouse=True)
def _clear_version_cache():
    graphics.windows_build.cache_clear()
    yield
    graphics.windows_build.cache_clear()


def native(monkeypatch, *, build=22631, composition=1, ex_style=0, dpi=96):
    user = SimpleNamespace(
        GetAncestor=NativeCall(456),
        SetWindowCompositionAttribute=NativeCall(composition),
        GetWindowLongPtrW=NativeCall(ex_style),
        SetWindowLongPtrW=NativeCall(0),
        SetWindowPos=NativeCall(),
        RedrawWindow=NativeCall(),
        SetWindowRgn=NativeCall(1),
        GetDpiForWindow=NativeCall(dpi),
        GetDpiForSystem=NativeCall(96),
        CallWindowProcW=NativeCall(0),
    )
    dwm = SimpleNamespace(
        DwmSetWindowAttribute=NativeCall(),
        DwmExtendFrameIntoClientArea=NativeCall(),
    )
    gdi = SimpleNamespace(
        CreateRoundRectRgn=NativeCall(789),
        DeleteObject=NativeCall(),
    )
    ntdll = SimpleNamespace(RtlGetVersion=RtlGetVersion(build))
    monkeypatch.setattr(
        ctypes, "windll",
        SimpleNamespace(user32=user, dwmapi=dwm, gdi32=gdi, ntdll=ntdll),
    )
    graphics.windows_build.cache_clear()
    return user, dwm, gdi


def accent_from(call):
    data = call[1]._obj
    return ctypes.cast(data.data, ctypes.POINTER(graphics._Accent)).contents, data


# ---------------------------------------------------------------- versions

def test_windows_build_reads_rtl_get_version(monkeypatch):
    native(monkeypatch, build=22631)
    assert graphics.windows_build() == 22631


def test_windows_build_failure_returns_zero(monkeypatch):
    native(monkeypatch)
    ctypes.windll.ntdll.RtlGetVersion.status = 0xC0000001
    graphics.windows_build.cache_clear()
    assert graphics.windows_build() == 0


def test_missing_ntdll_returns_zero(monkeypatch):
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(
        user32=SimpleNamespace(), dwmapi=SimpleNamespace()))
    graphics.windows_build.cache_clear()
    assert graphics.windows_build() == 0
    assert graphics.is_windows_11() is False


@pytest.mark.parametrize("build,expected", [(21999, False), (22000, True), (26100, True)])
def test_windows_11_boundary(monkeypatch, build, expected):
    native(monkeypatch, build=build)
    assert graphics.is_windows_11() is expected


# ------------------------------------------------------------- solid path

def test_solid_path_clears_colour_key_layered_bit_and_composition(monkeypatch):
    user, dwm, _ = native(monkeypatch, ex_style=0x00080000 | 0x100)
    window = Window()
    effects = graphics.apply_window_effects(window, "#F2F3F6", material="solid")
    assert effects == graphics.WindowEffects(
        rounded=True, glass=False, shadow=True, material="solid", layered=False)
    assert window.options["-transparentcolor"] == ""
    style_call = next(call for call in user.SetWindowLongPtrW.calls if call[1] == -20)
    assert style_call[2] & 0x00080000 == 0
    assert any(call[6] == 0x27 for call in user.SetWindowPos.calls)
    accent, data = accent_from(user.SetWindowCompositionAttribute.calls[-1])
    assert accent.state == 0 and data.attribute == 19
    assert dwm.DwmExtendFrameIntoClientArea.calls
    assert user.RedrawWindow.calls[-1][3] == 0x485


def test_solid_path_without_layered_bit_skips_style_write(monkeypatch):
    user, _, _ = native(monkeypatch, ex_style=0x100)
    graphics.apply_window_effects(Window(), "#F2F3F6")
    assert not [call for call in user.SetWindowLongPtrW.calls if call[1] == -20]


# --------------------------------------- taskbar exclusion for frameless surfaces

def test_override_redirect_solid_reasserts_toolwindow_bit(monkeypatch):
    # Tk's colour-key handler drops the Map-time WS_EX_TOOLWINDOW; the card
    # would otherwise surface as a taskbar button whose close kills the card.
    user, _, _ = native(monkeypatch, ex_style=0x00000008)
    graphics.apply_window_effects(OverrideRedirectWindow(), "#F2F3F6", material="solid")
    style_writes = [call[2] for call in user.SetWindowLongPtrW.calls if call[1] == -20]
    assert style_writes and style_writes[-1] & graphics.WS_EX_TOOLWINDOW
    assert any(call[6] == 0x37 for call in user.SetWindowPos.calls)


def test_framed_window_never_gets_toolwindow_bit(monkeypatch):
    # Popup/settings/history windows keep their ordinary taskbar button.
    user, _, _ = native(monkeypatch, ex_style=0x100)
    graphics.apply_window_effects(Window(), "#F2F3F6")
    style_writes = [call[2] for call in user.SetWindowLongPtrW.calls if call[1] == -20]
    assert not any(value & graphics.WS_EX_TOOLWINDOW for value in style_writes)


def test_toolwindow_bit_already_present_skips_exstyle_rewrite(monkeypatch):
    user, _, _ = native(monkeypatch, ex_style=graphics.WS_EX_TOOLWINDOW)
    graphics.apply_window_effects(OverrideRedirectWindow(), "#F2F3F6")
    assert not [call for call in user.SetWindowLongPtrW.calls if call[1] == -20]


def test_override_redirect_acrylic_keeps_layered_and_adds_toolwindow(monkeypatch):
    user, _, _ = native(monkeypatch, composition=1, ex_style=0x00080000)
    monkeypatch.setattr(graphics, "transparency_enabled", lambda: True)
    graphics.apply_window_effects(OverrideRedirectWindow(), "#F2F3F6", material="acrylic")
    style_writes = [call[2] for call in user.SetWindowLongPtrW.calls if call[1] == -20]
    assert style_writes[-1] == 0x00080000 | graphics.WS_EX_TOOLWINDOW


@pytest.mark.parametrize("surface,expected", [("#18191D", 1), ("#F2F3F6", 0)])
def test_dwm_dark_mode_attribute_follows_surface_luminance(monkeypatch, surface, expected):
    _, dwm, _ = native(monkeypatch)
    graphics.apply_window_effects(Window(), surface)
    dark_call = next(call for call in dwm.DwmSetWindowAttribute.calls if call[1] == 20)
    assert dark_call[2]._obj.value == expected


def test_windows11_requests_dwm_corners_and_clears_stale_gdi_region(monkeypatch):
    user, dwm, gdi = native(monkeypatch)
    effects = graphics.apply_window_effects(Window(), "#18191D")
    corner_calls = [call for call in dwm.DwmSetWindowAttribute.calls if call[1] == 33]
    assert corner_calls and corner_calls[0][2]._obj.value == 2
    assert effects.rounded is True
    assert any(call[1] is None and call[2] == 1 for call in user.SetWindowRgn.calls)
    assert not gdi.CreateRoundRectRgn.calls


def test_dwm_corner_failure_reports_unrounded_but_stays_solid(monkeypatch):
    _, dwm, _ = native(monkeypatch)
    dwm.DwmSetWindowAttribute.result = 0x80070057
    effects = graphics.apply_window_effects(Window(), "#F2F3F6")
    assert effects.rounded is False
    assert effects.material == "solid" and effects.shadow is True


def test_windows10_skips_dwm_corners_and_keeps_native_shadow(monkeypatch):
    _, dwm, _ = native(monkeypatch, build=19045)
    effects = graphics.apply_window_effects(Window(), "#F2F3F6")
    assert not [call for call in dwm.DwmSetWindowAttribute.calls if call[1] == 33]
    assert effects.rounded is False and effects.shadow is True


# ----------------------------------------------------- GDI region fallback

def test_set_rounded_region_is_noop_on_windows11(monkeypatch):
    user, _, gdi = native(monkeypatch, build=22631)
    graphics.set_rounded_region(Window(), RADIUS["window"])
    assert not gdi.CreateRoundRectRgn.calls
    assert not user.SetWindowRgn.calls


def test_set_rounded_region_scales_dip_radius_on_windows10(monkeypatch):
    user, _, gdi = native(monkeypatch, build=19045)
    window = Window(scaling=144 / 72)  # 150% DPI
    graphics.set_rounded_region(window, RADIUS["window"])
    # round(8 DIP * 1.5) = 12 px radius -> 24 px ellipse
    assert gdi.CreateRoundRectRgn.calls[-1] == (0, 0, 401, 301, 24, 24)
    assert user.SetWindowRgn.calls[-1][1] == 789


def test_set_rounded_region_ignores_unrealized_window(monkeypatch):
    _, _, gdi = native(monkeypatch, build=19045)
    graphics.set_rounded_region(Window(width=1, height=1), RADIUS["window"])
    assert not gdi.CreateRoundRectRgn.calls


def test_set_rounded_region_deletes_handle_when_apply_fails(monkeypatch):
    user, _, gdi = native(monkeypatch, build=19045)
    user.SetWindowRgn.result = 0
    graphics.set_rounded_region(Window(), RADIUS["window"])
    assert gdi.DeleteObject.calls[-1][0] == 789


# ------------------------------------------------------------- acrylic

def test_resolve_material_requires_windows11_and_system_transparency(monkeypatch):
    native(monkeypatch, build=22631)
    monkeypatch.setattr(graphics, "transparency_enabled", lambda: True)
    assert graphics.resolve_material("acrylic") == "acrylic"
    assert graphics.resolve_material("solid") == "solid"
    assert graphics.resolve_material("mica") == "solid"
    monkeypatch.setattr(graphics, "transparency_enabled", lambda: False)
    assert graphics.resolve_material("acrylic") == "solid"


def test_acrylic_is_unavailable_on_windows10(monkeypatch):
    native(monkeypatch, build=19045)
    monkeypatch.setattr(graphics, "transparency_enabled", lambda: True)
    assert graphics.resolve_material("acrylic") == "solid"


def test_acrylic_path_sets_tint_colour_key_and_layered_effect(monkeypatch):
    user, dwm, _ = native(monkeypatch, composition=1)
    monkeypatch.setattr(graphics, "transparency_enabled", lambda: True)
    window = Window()
    effects = graphics.apply_window_effects(window, "#F2F3F6", material="acrylic")
    assert effects == graphics.WindowEffects(
        rounded=True, glass=True, shadow=False, material="acrylic", layered=True)
    assert window.options["-transparentcolor"] == "#F2F3F6"
    accent, data = accent_from(user.SetWindowCompositionAttribute.calls[-1])
    red, green, blue = 0xF2, 0xF3, 0xF6
    expected_tint = (ACRYLIC_TINT_ALPHA << 24) | (blue << 16) | (green << 8) | red
    assert accent.state == 4 and accent.colour == expected_tint
    assert data.attribute == 19 and data.size == ctypes.sizeof(graphics._Accent)
    assert dwm.DwmExtendFrameIntoClientArea.calls


def test_acrylic_composition_failure_falls_back_to_solid(monkeypatch):
    user, _, _ = native(monkeypatch, composition=0)
    monkeypatch.setattr(graphics, "transparency_enabled", lambda: True)
    window = Window()
    effects = graphics.apply_window_effects(window, "#F2F3F6", material="acrylic")
    assert effects.material == "solid" and not effects.glass and not effects.layered
    assert window.options["-transparentcolor"] == ""
    accent, _ = accent_from(user.SetWindowCompositionAttribute.calls[-1])
    assert accent.state == 0


def test_colour_key_failure_after_acrylic_rolls_back_accent(monkeypatch):
    user, _, _ = native(monkeypatch, composition=1)
    monkeypatch.setattr(graphics, "transparency_enabled", lambda: True)
    # The accent struct is mutated in place on rollback, so snapshot the state
    # at call time instead of reading the shared pointer afterwards.
    states = []
    recorder = user.SetWindowCompositionAttribute

    def snapshot_compose(hwnd, data_ref):
        states.append(accent_from((None, data_ref))[0].state)
        return recorder(hwnd, data_ref)

    user.SetWindowCompositionAttribute = snapshot_compose
    window = Window(reject_colour=True)
    effects = graphics.apply_window_effects(window, "#F2F3F6", material="acrylic")
    assert effects.material == "solid"
    assert states == [4, 0, 0]  # acrylic applied, explicit rollback, solid reset
    assert window.options["-transparentcolor"] == ""


def test_colour_key_probe_failure_never_enables_acrylic(monkeypatch):
    user, _, _ = native(monkeypatch, composition=1)
    monkeypatch.setattr(graphics, "transparency_enabled", lambda: True)
    effects = graphics.apply_window_effects(StrictWindow(), "#F2F3F6", material="acrylic")
    assert effects.material == "solid"
    assert all(accent_from(call)[0].state == 0
               for call in user.SetWindowCompositionAttribute.calls)


def test_acrylic_requested_but_system_switch_off_uses_solid(monkeypatch):
    user, _, _ = native(monkeypatch)
    monkeypatch.setattr(graphics, "transparency_enabled", lambda: False)
    effects = graphics.apply_window_effects(Window(), "#F2F3F6", material="acrylic")
    assert effects.material == "solid" and not effects.layered
    assert all(accent_from(call)[0].state == 0
               for call in user.SetWindowCompositionAttribute.calls)


# ------------------------------------------------------- fail-closed baseline

def test_unavailable_native_api_returns_default_effects(monkeypatch):
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace())
    window = Window()
    assert graphics.apply_window_effects(window, "#F2F3F6") == graphics.WindowEffects()


# -------------------------------------------------- install-time orchestration

def test_install_applies_immediately_and_rechecks_on_map(monkeypatch):
    native(monkeypatch)
    applied = []
    region_calls = []

    def fake_apply(window, surface, *, material=None):
        applied.append((surface, material))
        window._window_effects = graphics.WindowEffects(
            rounded=True, material=material or "solid")
        window._window_effects_key = (
            graphics.native_handle(window), surface, window._window_effects.material,
        )
        return window._window_effects

    monkeypatch.setattr(graphics, "apply_window_effects", fake_apply)
    monkeypatch.setattr(graphics, "set_rounded_region",
                        lambda *args: region_calls.append(args))
    window = Window()
    graphics.install_window_effects(window, "#F2F3F6", RADIUS["window"], material="solid")
    assert applied == [("#F2F3F6", "solid")]
    assert window._window_effects.rounded
    assert region_calls == []  # Win11: DWM owns corners, no GDI fallback
    map_handler = next(callback for event, callback in window.binds if event == "<Map>")
    window.viewable = True
    map_handler(SimpleNamespace(widget=window))
    assert window.idle
    window.idle[0]()
    assert len(applied) == 2  # first Map always stabilizes native frame state

    map_handler(SimpleNamespace(widget=window))
    window.idle[1]()
    assert len(applied) == 2  # unchanged retained re-show is deduplicated

    window._material_surface = "#101218"
    map_handler(SimpleNamespace(widget=window))
    window.idle[2]()
    assert applied[-1] == ("#101218", "solid")
    assert len(applied) == 3


def test_install_falls_back_to_gdi_region_when_unrounded(monkeypatch):
    native(monkeypatch, build=19045)
    monkeypatch.setattr(graphics, "apply_window_effects",
                        lambda *args, **kwargs: graphics.WindowEffects(rounded=False))
    region_calls = []
    monkeypatch.setattr(graphics, "set_rounded_region",
                        lambda window, radius: region_calls.append(radius))
    window = Window()
    graphics.install_window_effects(window, "#F2F3F6", RADIUS["panel"])
    assert region_calls == [RADIUS["panel"]]


def test_install_without_radius_never_sets_region(monkeypatch):
    native(monkeypatch, build=19045)
    monkeypatch.setattr(graphics, "apply_window_effects",
                        lambda *args, **kwargs: graphics.WindowEffects(rounded=False))
    region_calls = []
    monkeypatch.setattr(graphics, "set_rounded_region",
                        lambda window, radius: region_calls.append(radius))
    window = Window()
    graphics.install_window_effects(window, "#F2F3F6")
    assert region_calls == []


def test_install_destroy_cancels_pending_map_recheck(monkeypatch):
    native(monkeypatch)
    monkeypatch.setattr(graphics, "apply_window_effects",
                        lambda *args, **kwargs: graphics.WindowEffects(rounded=True))
    window = Window()
    graphics.install_window_effects(window, "#F2F3F6", RADIUS["window"])
    map_handler = next(callback for event, callback in window.binds if event == "<Map>")
    destroy_handler = next(callback for event, callback in window.binds
                           if event == "<Destroy>")
    map_handler(SimpleNamespace(widget=window))
    token = f"idle-{len(window.idle)}"
    destroy_handler(SimpleNamespace(widget=window))
    assert token in window.cancelled


# --------------------------------------------------------------- DPI helpers

def test_native_dpi_prefers_window_dpi(monkeypatch):
    user, _, _ = native(monkeypatch, dpi=144)
    assert graphics.native_dpi(Window()) == 144
    assert user.GetDpiForWindow.calls


def test_native_dpi_falls_back_to_system_then_96(monkeypatch):
    user, _, _ = native(monkeypatch, dpi=0)
    user.GetDpiForSystem.result = 120
    assert graphics.native_dpi(Window()) == 120
    user.GetDpiForSystem.result = 0
    assert graphics.native_dpi(Window()) == 96


def test_calibrate_tk_scaling_pins_scaling_to_native_dpi(monkeypatch):
    native(monkeypatch, dpi=144)
    window = Window(scaling=96 / 72)
    assert graphics.calibrate_tk_scaling(window) == 1.5
    assert window.tk.scaling == pytest.approx(144 / 72)
    assert window.tk.set_calls == [pytest.approx(144 / 72)]


def test_calibrate_keeps_scaling_when_dpi_unchanged(monkeypatch):
    native(monkeypatch, dpi=96)
    window = Window(scaling=96 / 72)
    assert graphics.calibrate_tk_scaling(window) == 1.0
    assert window.tk.set_calls == []


def test_invalidate_scaled_caches_drops_raster_caches():
    graphics.invalidate_scaled_caches()  # clean baseline; other tests share the caches
    graphics._gradient_strip(10, "#FFFFFF", "#000000", True)
    graphics.rounded_image(5, 5, 2, "#FFFFFF")
    assert graphics._gradient_strip.cache_info().currsize >= 1
    assert graphics.rounded_image.cache_info().currsize == 1
    graphics.invalidate_scaled_caches()
    assert graphics._gradient_strip.cache_info().currsize == 0
    assert graphics.rounded_image.cache_info().currsize == 0


# -------------------------------------------------- override-redirect DPI hook

def test_dpi_handler_applies_suggested_rect_and_defers_callback(monkeypatch):
    user, _, _ = native(monkeypatch)
    window = Window()
    events = []
    graphics.install_dpi_handler(window, lambda dpi: events.append(dpi))
    assert window._dpi_handler is not None
    rect = graphics._SuggestedRect(100, 200, 400, 500)
    lparam = ctypes.cast(ctypes.byref(rect), ctypes.c_void_p).value
    window._dpi_handler["proc"](456, 0x02E0, 144 | (144 << 16), lparam)
    assert user.SetWindowPos.calls[-1][2:7] == (100, 200, 300, 300, 0x0014)
    assert len(window.idle) == 1
    window.idle[0]()
    assert events == [144]


def test_dpi_handler_restores_previous_proc_on_ncdestroy(monkeypatch):
    user, _, _ = native(monkeypatch)
    user.SetWindowLongPtrW.result = 999
    window = Window()
    graphics.install_dpi_handler(window, lambda dpi: None)
    window._dpi_handler["proc"](456, 0x82, 0, 0)
    assert user.SetWindowLongPtrW.calls[-1][1:] == (-4, 999)
    assert user.CallWindowProcW.calls[-1][0] == 999


def test_dpi_handler_passes_other_messages_to_old_proc(monkeypatch):
    user, _, _ = native(monkeypatch)
    user.SetWindowLongPtrW.result = 999
    window = Window()
    graphics.install_dpi_handler(window, lambda dpi: None)
    window._dpi_handler["proc"](456, 0x000F, 1, 2)
    assert user.CallWindowProcW.calls[-1] == (999, 456, 0x000F, 1, 2)


def test_dpi_handler_installs_only_once(monkeypatch):
    user, _, _ = native(monkeypatch)
    window = Window()
    graphics.install_dpi_handler(window, lambda dpi: None)
    first = window._dpi_handler
    graphics.install_dpi_handler(window, lambda dpi: None)
    assert window._dpi_handler is first
    assert sum(1 for call in user.SetWindowLongPtrW.calls if call[1] == -4) == 1


def test_dpi_handler_reinstalls_on_map_when_wrapper_hwnd_changes(monkeypatch):
    user, _, _ = native(monkeypatch)
    window = Window()
    graphics.install_dpi_handler(window, lambda dpi: None)
    map_handler = next(callback for event, callback in window.binds
                       if event == "<Map>")
    # Tk can expose a different outer HWND after the first Map than at construction.
    user.GetAncestor.result = 999
    map_handler(None)
    assert any(call[0] == 999 and call[1] == -4
               for call in user.SetWindowLongPtrW.calls)
    proc_address = ctypes.cast(window._dpi_handler["proc"], ctypes.c_void_p).value
    user.GetWindowLongPtrW.result = proc_address
    calls_before = len(user.SetWindowLongPtrW.calls)
    map_handler(None)
    assert len(user.SetWindowLongPtrW.calls) == calls_before


def test_dpi_handler_missing_api_is_benign(monkeypatch):
    monkeypatch.setattr(ctypes, "windll",
                        SimpleNamespace(user32=SimpleNamespace()))
    window = Window()
    graphics.install_dpi_handler(window, lambda dpi: None)
    assert getattr(window, "_dpi_handler", None) is None


# -------------------------------------------------- Tcl service-mode guards

def test_service_mode_binding_exports_on_windows():
    setter, getter = graphics._tcl_service_mode_functions()
    assert setter is not None and getter is not None
    original = getter()
    try:
        token = graphics.defer_tcl_events()
        assert isinstance(token, int)
        assert getter() == graphics.TCL_SERVICE_NONE
        graphics.restore_tcl_events(token)
        assert getter() == original
    finally:
        setter(original)


def test_defer_restore_preserves_an_ambient_all_mode():
    # TCL_SERVICE_ALL is the Tk native size/move modal-loop state whose
    # reentrancy aborts the interpreter; guards must restore it, not clobber.
    setter, getter = graphics._tcl_service_mode_functions()
    original = getter()
    try:
        setter(1)
        token = graphics.defer_tcl_events()
        try:
            assert getter() == graphics.TCL_SERVICE_NONE
        finally:
            graphics.restore_tcl_events(token)
        assert getter() == 1
    finally:
        setter(original)


def test_tcl_events_deferred_restores_on_exception():
    setter, getter = graphics._tcl_service_mode_functions()
    original = getter()
    try:
        with pytest.raises(RuntimeError):
            with graphics.tcl_events_deferred():
                assert getter() == graphics.TCL_SERVICE_NONE
                raise RuntimeError("boom")
        assert getter() == original
    finally:
        setter(original)


def test_service_mode_guard_fails_closed_without_binding(monkeypatch):
    monkeypatch.setattr(graphics, "_tcl_service_mode_functions",
                        lambda: (None, None))
    monkeypatch.setattr(graphics.sys, "platform", "win32")
    with pytest.raises(graphics.TclServiceGuardUnavailable):
        graphics.defer_tcl_events()
    graphics.restore_tcl_events(None)  # unsupported non-Windows platforms stay no-op


def test_service_mode_binding_uses_the_loaded_tcl_module(monkeypatch, tmp_path):
    """Do not let a same-named PATH DLL replace the Tcl already used by Tk."""
    loaded_path = tmp_path / "tcl86t.dll"
    loaded_path.write_bytes(b"")
    calls = []
    setter = SimpleNamespace(argtypes=None, restype=None)
    getter = SimpleNamespace(argtypes=None, restype=None)

    class Kernel32:
        @staticmethod
        def GetModuleHandleW(name):
            return 0x1234 if name == "tcl86t.dll" else 0

        @staticmethod
        def GetModuleFileNameW(handle, buffer, length):
            if handle != 0x1234:
                return 0
            buffer.value = str(loaded_path)
            return len(str(loaded_path))

    def load(path):
        calls.append(path)
        return SimpleNamespace(Tcl_SetServiceMode=setter, Tcl_GetServiceMode=getter)

    monkeypatch.setattr(graphics, "_TCL_SERVICE_FUNCTIONS", None)
    monkeypatch.setattr(graphics.sys, "platform", "win32")
    monkeypatch.setattr(graphics.ctypes, "WinDLL", load)
    monkeypatch.setattr(graphics.ctypes, "windll", SimpleNamespace(kernel32=Kernel32()))
    assert graphics._tcl_service_mode_functions() == (setter, getter)
    assert calls == [str(loaded_path)]
    assert graphics.tcl_service_guard_status() == (True, str(loaded_path))


# ------------------------------------------------------------- registry gate

class _FakeKey:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_transparency_enabled_reads_registry_switch(monkeypatch):
    registry = SimpleNamespace(
        HKEY_CURRENT_USER=1,
        OpenKey=lambda *args: _FakeKey(),
        QueryValueEx=lambda key, name: (1, 4),
    )
    monkeypatch.setattr(graphics, "winreg", registry)
    assert graphics.transparency_enabled() is True
    registry.QueryValueEx = lambda key, name: (0, 4)
    assert graphics.transparency_enabled() is False


def test_transparency_enabled_missing_key_returns_false(monkeypatch):
    def raise_oserror(*args):
        raise OSError("missing key")

    registry = SimpleNamespace(
        HKEY_CURRENT_USER=1,
        OpenKey=raise_oserror,
        QueryValueEx=lambda key, name: (1, 4),
    )
    monkeypatch.setattr(graphics, "winreg", registry)
    assert graphics.transparency_enabled() is False


# ----------------------------------------------------- rounded image pixels

def test_rounded_image_corners_are_transparent_and_centre_is_fill():
    image = graphics.rounded_image(40, 30, radius=8, fill="#FFFFFF")
    assert image.getpixel((0, 0))[3] == 0
    assert image.getpixel((39, 29))[3] == 0
    assert image.getpixel((20, 15))[:3] == (255, 255, 255)


def test_rounded_image_outline_paints_edge_only_when_requested():
    def reddish(img):
        hits = []
        for y in range(img.height):
            for x in range(img.width):
                pixel = img.getpixel((x, y))
                if pixel[3] > 0 and pixel[0] > pixel[1] + 25 and pixel[0] > pixel[2] + 25:
                    hits.append((x, y))
        return hits

    outlined = graphics.rounded_image(60, 40, radius=10, fill="#FFFFFF",
                                      outline="#E12D39")
    plain = graphics.rounded_image(60, 40, radius=10, fill="#FFFFFF")
    assert reddish(outlined)
    assert not reddish(plain)


# ------------------------------------------------------------- scrollbars

# clam's built-in warm-grey scrollbar colours; the themed palette must never
# leak them back in (the dark-mode black/grey clash came from these).
CLAM_SCROLLBAR_COLORS = {"#bab5ab", "#eeebe7", "#cfcdc8", "#9e9a91"}


def test_scrollbar_palette_light_derives_from_theme_tokens():
    palette = graphics.scrollbar_palette(LIGHT)
    assert set(palette.values()).isdisjoint(CLAM_SCROLLBAR_COLORS)
    assert palette["trough"] == LIGHT.colors["trough"]
    assert palette["thumb"] == graphics.mix_color(
        LIGHT.colors["muted"], LIGHT.colors["trough"], 0.35)
    assert palette["thumb"] == "#91959f"
    assert palette["arrow"] == palette["thumb"]
    assert palette["active"] == LIGHT.colors["accent"]
    assert palette["pressed"] == LIGHT.colors["accent_active"]
    assert palette["disabled"] == LIGHT.colors["border"]


def test_scrollbar_palette_dark_derives_from_theme_tokens():
    palette = graphics.scrollbar_palette(DARK)
    assert set(palette.values()).isdisjoint(CLAM_SCROLLBAR_COLORS)
    assert palette["trough"] == DARK.colors["trough"]
    assert palette["thumb"] == "#838892"
    assert palette["arrow"] == palette["thumb"]
    assert palette["active"] == DARK.colors["accent"]
    assert palette["pressed"] == DARK.colors["accent_active"]
    assert palette["disabled"] == DARK.colors["border"]


# -------------------------------------------------------------- comboboxes

# clam's readonly combobox map leaks these light/system colors; the dark-mode
# white/gray dropdown came from exactly these lookups.
CLAM_COMBOBOX_LEAKS = {"#dcdad5", "#4a6984", "#999999", "#eeebe7", "#cfcdc8"}


def test_combobox_palette_light_derives_from_theme_tokens():
    palette = graphics.combobox_palette(LIGHT)
    assert set(palette.values()).isdisjoint(CLAM_COMBOBOX_LEAKS)
    assert palette["field"] == LIGHT.colors["card"]
    assert palette["foreground"] == LIGHT.colors["text"]
    assert palette["border"] == LIGHT.colors["border"]
    assert palette["arrow"] == LIGHT.colors["text"]
    assert palette["muted"] == LIGHT.colors["muted"]
    assert palette["hover"] == LIGHT.colors["hover"]
    assert palette["pressed"] == LIGHT.colors["pressed"]
    assert palette["disabled_field"] == LIGHT.colors["trough"]
    assert palette["select_background"] == LIGHT.colors["accent"]
    assert palette["select_foreground"] == LIGHT.colors["button_text"]


def test_combobox_palette_dark_derives_from_theme_tokens():
    palette = graphics.combobox_palette(DARK)
    # In dark mode even white/black system defaults must never come back.
    assert set(palette.values()).isdisjoint(CLAM_COMBOBOX_LEAKS | {"#ffffff", "#000000"})
    assert palette["field"] == DARK.colors["card"] == "#24262B"
    assert palette["foreground"] == DARK.colors["text"]
    assert palette["border"] == DARK.colors["border"]
    assert palette["arrow"] == DARK.colors["text"]
    assert palette["select_background"] == DARK.colors["accent"]
    assert palette["select_foreground"] == DARK.colors["button_text"]
    assert palette["disabled_field"] == DARK.colors["trough"]
