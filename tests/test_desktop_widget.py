"""Pure geometry behind the desktop card's refresh button.

The card itself needs a live Tk interpreter, but its hit-testing and layout
math does not; these functions keep the button/column collision rules
exercisable without a windowing session.
"""
import sys

import pytest

if sys.platform != "win32":
    pytest.skip("Windows desktop widget", allow_module_level=True)

from token_plan_monitor.desktop_widget import (
    hit_refresh_button,
    refresh_button_frame,
    refresh_button_hit_bounds,
    refresh_progress_limit,
)


def test_refresh_button_frame_is_bottom_right_at_96_dpi():
    assert refresh_button_frame(320, 124, 1.0) == (276, 80, 308, 112)


def test_refresh_button_hit_bounds_inset_six_dip():
    # 32 DIP frame minus 6 DIP on every side -> a 20 DIP clickable circle.
    assert refresh_button_hit_bounds(320, 124, 1.0) == (282, 86, 302, 106)


def test_refresh_button_geometry_scales_with_dpi():
    frame = refresh_button_frame(480, 186, 1.5)
    assert frame == (414, 120, 462, 168)
    hit = refresh_button_hit_bounds(480, 186, 1.5)
    assert hit == (423, 129, 453, 159)
    assert (hit[2] - hit[0], hit[3] - hit[1]) == (30, 30)


def test_hit_refresh_button_includes_boundary_and_rejects_outside_points():
    args = (320, 124, 1.0)
    # Corners of the hit box are inclusive.
    assert hit_refresh_button(282, 86, *args)
    assert hit_refresh_button(302, 106, *args)
    assert hit_refresh_button(292, 96, *args)
    assert not hit_refresh_button(281, 86, *args)
    assert not hit_refresh_button(303, 106, *args)
    assert not hit_refresh_button(40, 40, *args)


def test_refresh_progress_limit_leaves_a_six_dip_gap():
    assert refresh_progress_limit(320, 1.0) == 276
    hit_left = refresh_button_hit_bounds(480, 186, 1.5)[0]
    assert refresh_progress_limit(480, 1.5) == hit_left - round(6 * 1.5)


def test_refresh_progress_limit_stays_inside_supported_and_legacy_widths():
    for width in (280, 320, 520):
        limit = refresh_progress_limit(width, 1.0)
        assert 0 < limit < width
