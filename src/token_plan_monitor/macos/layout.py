"""Pure helpers shared by the native shell and offline tests.

Settings validation rules moved to ``token_plan_monitor.settings_rules`` so the
Windows Tk shell can reuse them without importing the macOS package. The names
are re-exported here to keep every existing ``macos.layout`` import working.
"""
from __future__ import annotations

from ..models import (
    CARD_MAX_DIMENSION,
    CARD_MIN_HEIGHT,
    CARD_MIN_WIDTH,
    MonitorViewState,
)
from ..presentation import lowest_windows
from ..settings_rules import (
    FieldValidationError,
    resolve_claude_seed,
    validate_claude_configuration,
    validate_configuration,
)

__all__ = [
    "CUSTOM_MODEL_MARKER",
    "FieldValidationError",
    "clamp_card",
    "fit_card_size",
    "lowest_windows",
    "model_popup_index",
    "model_popup_restore",
    "model_popup_value",
    "primary_action",
    "resize_card_frame",
    "resolve_claude_seed",
    "validate_claude_configuration",
    "validate_configuration",
]


def primary_action(state: MonitorViewState) -> tuple[str, str]:
    if state.status == "unconfigured":
        return "连接账号", "check-credentials"
    if state.error_category in ("authentication", "configuration"):
        return "检查凭据", "check-credentials"
    if state.status in ("stale", "error"):
        return "重试", "refresh"
    return "立即刷新", "refresh"


#: Trailing entry of every model popup; its value lives in the custom text
#: field, never in the popup selection.
CUSTOM_MODEL_MARKER = "自定义模型 ID…"


def model_popup_index(known, model_id, custom, offset=0, empty_index=0):
    """Selection index for a model popup.

    Layout: [leading placeholder when offset=1] + known models + custom
    marker. A non-empty custom field wins (the marker); a known id selects
    its slot; anything else lands on ``empty_index`` -- callers pick 0 (first
    model / placeholder) or the marker, depending on the site.
    """
    if custom:
        return offset + len(known)
    if model_id in known:
        return offset + known.index(model_id)
    return empty_index


def model_popup_value(known, index, offset=0):
    """Model id at a popup index; placeholder and custom marker read as ""."""
    position = index - offset
    return known[position] if 0 <= position < len(known) else ""


def model_popup_restore(known, model_id, custom, offset=0):
    """(index, demoted_id) after a catalogue refresh.

    A non-empty id the plan no longer offers is demoted to the custom field
    (returned as ``demoted_id`` for the caller to write) rather than lost.
    Everything else follows model_popup_index with empty -> index 0.
    """
    if not custom and model_id and model_id not in known:
        return offset + len(known), model_id
    return model_popup_index(known, model_id, custom, offset), ""


ScreenRect = tuple[float, float, float, float]
FrameRect = tuple[float, float, float, float]


def _nearest_screen(x: float | None, y: float | None, width: float, height: float,
                    screens: list[ScreenRect]) -> ScreenRect:
    if x is None or y is None:
        return screens[0]
    cx, cy = x + width / 2, y + height / 2

    def distance(screen: ScreenRect) -> float:
        sx, sy, sw, sh = screen
        return max(sx - cx, 0, cx - sx - sw) ** 2 + max(sy - cy, 0, cy - sy - sh) ** 2

    return min(screens, key=distance)


def fit_card_size(x: float | None, y: float | None, width: float, height: float,
                  screens: list[ScreenRect]) -> tuple[int, int]:
    """Restore the minimum size without letting a card exceed its nearest display."""
    width = max(CARD_MIN_WIDTH, min(round(width), CARD_MAX_DIMENSION))
    height = max(CARD_MIN_HEIGHT, min(round(height), CARD_MAX_DIMENSION))
    if not screens:
        return width, height
    _, _, screen_width, screen_height = _nearest_screen(x, y, width, height, screens)
    return (
        min(width, max(CARD_MIN_WIDTH, round(screen_width))),
        min(height, max(CARD_MIN_HEIGHT, round(screen_height))),
    )


def resize_card_frame(frame: FrameRect, dx: float, dy: float, edges: frozenset[str],
                      screen: ScreenRect | None = None) -> tuple[int, int, int, int]:
    """Resize a Cocoa frame while anchoring the edges opposite the dragged ones."""
    x, y, width, height = frame
    right, top = x + width, y + height
    if "left" in edges:
        maximum = right - screen[0] if screen else CARD_MAX_DIMENSION
        width = min(max(CARD_MIN_WIDTH, width - dx), max(CARD_MIN_WIDTH, maximum))
        x = right - width
    elif "right" in edges:
        maximum = screen[0] + screen[2] - x if screen else CARD_MAX_DIMENSION
        width = min(max(CARD_MIN_WIDTH, width + dx), max(CARD_MIN_WIDTH, maximum))
    if "bottom" in edges:
        maximum = top - screen[1] if screen else CARD_MAX_DIMENSION
        height = min(max(CARD_MIN_HEIGHT, height - dy), max(CARD_MIN_HEIGHT, maximum))
        y = top - height
    elif "top" in edges:
        maximum = screen[1] + screen[3] - y if screen else CARD_MAX_DIMENSION
        height = min(max(CARD_MIN_HEIGHT, height + dy), max(CARD_MIN_HEIGHT, maximum))
    return round(x), round(y), round(width), round(height)


def clamp_card(x: float | None, y: float | None, width: float, height: float,
               screens: list[ScreenRect]) -> tuple[int, int]:
    """Cocoa point coordinates, including displays left of/below the main display."""
    if not screens:
        return int(x or 0), int(y or 0)
    if x is None or y is None:
        sx, sy, sw, sh = screens[0]
        x, y = sx + sw - width - 24, sy + sh - height - 24
    sx, sy, sw, sh = _nearest_screen(x, y, width, height, screens)
    return round(max(sx, min(x, sx + max(0, sw - width)))), round(max(sy, min(y, sy + max(0, sh - height))))
