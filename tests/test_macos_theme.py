"""Guarantees the macOS design tokens have to hold, without opening a window.

Contrast uses the same WCAG rule tests/test_appearance.py applies to the Windows
palette, so the two shells are held to one standard.

These tests prove the palette is wired to the values it declares. They cannot
prove the *defect* they were written around: colors registered through
colorWithName:dynamicProvider: are stored by name, and sharing one name made a
recolored label keep its first color -- but only once a control had cached it,
which no pure-color test can see. That case is guarded by assert_status_tone in
macos/preview.py, verified to fail when the shared name is restored.
"""
import pytest

pytest.importorskip("AppKit")

import AppKit as A  # noqa: E402  (after the platform guard)

from token_plan_monitor.macos import theme  # noqa: E402


def linear(value):
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def luminance(rgb):
    return sum(linear(v) * w for v, w in zip(rgb, (0.2126, 0.7152, 0.0722)))


def contrast(foreground, background):
    high, low = max(luminance(foreground), luminance(background)), min(
        luminance(foreground), luminance(background))
    return (high + 0.05) / (low + 0.05)


def flatten(color, background):
    """Composite a translucent text color onto an opaque surface."""
    red, green, blue, alpha = color
    return tuple(c * alpha + s * (1 - alpha)
                 for c, s in zip((red, green, blue), background))


APPEARANCES = {
    "light": (A.NSAppearanceNameAqua, "light"),
    "dark": (A.NSAppearanceNameDarkAqua, "dark"),
}


@pytest.fixture
def in_appearance():
    """Resolve colors as AppKit would while drawing in one appearance."""
    previous = A.NSAppearance.currentAppearance()
    def run(name):
        A.NSAppearance.setCurrentAppearance_(A.NSAppearance.appearanceNamed_(name))
    yield run
    A.NSAppearance.setCurrentAppearance_(previous)


def channel_values(color):
    resolved = color.colorUsingColorSpace_(A.NSColorSpace.sRGBColorSpace())
    return (resolved.redComponent(), resolved.greenComponent(),
            resolved.blueComponent(), resolved.alphaComponent())


@pytest.mark.parametrize("appearance_name", APPEARANCES)
@pytest.mark.parametrize("role", sorted(theme.TONE_VALUES))
def test_status_tones_clear_wcag_aa_on_every_surface(appearance_name, role):
    """Status color must stay readable on a card and on the bare window page."""
    _, key = APPEARANCES[appearance_name]
    light, dark = theme.TONE_VALUES[role]
    foreground = light if key == "light" else dark
    for surface, background in theme.SURFACE_VALUES[key].items():
        assert contrast(foreground, background) >= 4.5, (role, key, surface)


@pytest.mark.parametrize("appearance_name", APPEARANCES)
def test_quiet_text_clears_wcag_aa_on_every_surface(appearance_name):
    """secondaryLabelColor does not (3.95 light / 4.14 dark); this project's does."""
    _, key = APPEARANCES[appearance_name]
    red, green, blue, alpha = theme.SECONDARY_VALUES[0 if key == "light" else 1]
    for surface, background in theme.SURFACE_VALUES[key].items():
        assert contrast(flatten((red, green, blue, alpha), background), background) >= 4.5, (key, surface)


@pytest.mark.parametrize("appearance_name", APPEARANCES)
@pytest.mark.parametrize("role", sorted(theme.TONE_VALUES))
def test_each_tone_resolves_to_its_own_pair(in_appearance, appearance_name, role):
    """Each role resolves to its own declared pair, per appearance."""
    name, key = APPEARANCES[appearance_name]
    in_appearance(name)
    expected = theme.TONE_VALUES[role][0 if key == "light" else 1]
    got = channel_values(theme.tone(role))[:3]
    assert tuple(round(v, 2) for v in got) == tuple(round(v, 2) for v in expected), role


def test_tones_differ_from_each_other_in_both_appearances(in_appearance):
    for name, _ in APPEARANCES.values():
        in_appearance(name)
        resolved = {channel_values(theme.tone(role)) for role in theme.TONE_VALUES}
        assert len(resolved) == len(theme.TONE_VALUES), name


def test_surfaces_stay_ordered_page_card_well(in_appearance):
    """Elevation is the whole layering system now that nothing has a stroke.

    The card must read above the page and the input well below the card, in both
    appearances -- measured orderings, not catalog-color assumptions.
    """
    for name, key in APPEARANCES.values():
        in_appearance(name)
        page = channel_values(theme.PAGE)[0]
        card = channel_values(theme.CARD)[0]
        well = channel_values(theme.FIELD)[0]
        if key == "light":
            assert well < card and page < card, key
        else:
            assert card > page and well < card, key


def test_type_ramp_never_goes_below_the_macOS_floor_and_avoids_true_bold():
    for role, (size, weight) in theme.ROLES.items():
        assert size >= 11, (role, size)
        assert weight != "bold", role  # semibold carries emphasis here


def test_metric_figures_are_tabular_and_rounded_when_available():
    font = theme.metric_sized(20)
    if not theme.ROUNDED_NUMERALS:
        pytest.skip("rounded numerals disabled")
    assert "Rounded" in font.fontName(), font.fontName()
    size = lambda text: A.NSString.stringWithString_(text).sizeWithAttributes_(
        {A.NSFontAttributeName: font}).width
    # Tabular digits: identical widths, so percentages do not reflow on refresh.
    assert size("111111.1%") == size("888888.8%")


def test_cards_use_the_continuous_corner():
    assert theme.CORNER_CURVE == A.NSString.stringWithString_("continuous") or \
        str(theme.CORNER_CURVE) == "continuous"


class _StubField:
    def __init__(self, editor):
        self._editor = editor

    def currentEditor(self):
        return self._editor


class _StubWell:
    def __init__(self):
        self.focused = None

    def set_focused(self, value):
        self.focused = value


class _StubWindow:
    def __init__(self, key, responder):
        self._key, self._responder = key, responder

    def isKeyWindow(self):
        return self._key

    def firstResponder(self):
        return self._responder


class _StubShell:
    """Just the state refresh_field_focus reads, so the rule can be tested
    without a window server -- the pixel result is covered by the smoke run."""

    def __init__(self, key, responder, rows):
        self.settings = _StubWindow(key, responder)
        self.fields = {name: _StubField(editor) for name, editor in rows.items()}
        self.field_wells = {name: _StubWell() for name in rows}


def _lit(shell):
    from token_plan_monitor.macos.ui import MonitorUI
    MonitorUI.refresh_field_focus(shell)
    return sorted(name for name, well in shell.field_wells.items() if well.focused)


def test_only_the_editing_row_on_a_key_window_is_lit():
    editor, other = object(), object()
    shell = _StubShell(True, editor, {"ak": editor, "sk": other, "region": None})
    assert _lit(shell) == ["ak"]


def test_ring_moves_with_focus_and_leaves_nothing_lit_behind():
    from token_plan_monitor.macos.ui import MonitorUI
    first, second = object(), object()
    shell = _StubShell(True, first, {"ak": first, "sk": second})
    MonitorUI.refresh_field_focus(shell)
    assert shell.field_wells["ak"].focused and not shell.field_wells["sk"].focused
    shell.settings = _StubWindow(True, second)
    MonitorUI.refresh_field_focus(shell)
    assert _lit(shell) == ["sk"], "the previous row must be cleared"


def test_nothing_is_lit_when_the_window_is_not_key():
    editor = object()
    shell = _StubShell(False, editor, {"ak": editor, "sk": editor})
    assert _lit(shell) == [], "an inactive window must not draw a focus ring"


def test_nothing_is_lit_without_an_active_editor():
    shell = _StubShell(True, None, {"ak": None, "sk": None})
    assert _lit(shell) == []


SEMIBOLD_ROLES = ("title", "group", "plan", "kind", "status")
REGULAR_ROLES = ("body", "detail", "caption")


def test_role_weights_reach_the_font_not_just_the_table():
    """A role carries size *and* weight.

    ``text_label`` used to read only the size off ROLES and take weight from a
    ``bold`` flag, so the 15pt semibold group headers -- the one thing holding the
    settings hierarchy together -- silently rendered as Regular, indistinguishable
    from the 13pt field labels beneath them.
    """
    for role in SEMIBOLD_ROLES:
        assert "Demi" in theme.label_font(role=role).fontName(), role
    for role in REGULAR_ROLES:
        assert "Demi" not in theme.label_font(role=role).fontName(), role


def test_bold_flag_can_emphasise_a_regular_role():
    assert "Demi" in theme.label_font(role="detail", bold=True).fontName()


def test_numeric_keeps_the_role_weight():
    """Tabular figures change the figure face only: a 12pt note must not turn
    semibold just because it contains digits."""
    face = theme.label_font(role="detail", numeric=True).fontName()
    assert "Rounded" in face and "Demi" not in face, face
