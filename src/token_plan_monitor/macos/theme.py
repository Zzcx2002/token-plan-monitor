"""Design tokens for the native shell: appearance-aware color, type ramp, metrics.

This is the only place the macOS windows take color, type and shape from, so the
whole look is retunable here. Three rules the rest of the module obeys:

1. Every color here is *dynamic*: ``colorWithName:dynamicProvider:`` for the
   pairs this file owns, and catalog colors inside those providers. AppKit calls
   the provider with the appearance being drawn into, so a view assigned once at
   build time still changes color the moment the user switches appearance --
   no re-render, and no call site has to know which appearance it was built in.
   (Verified against this PyObjC: the provider returns different RGB under Aqua
   and DarkAqua; ``NSColor.CGColor`` and text colors both follow.)
2. Layer fills still resolve inside ``updateLayer``, because ``CALayer`` stores a
   single resolved ``CGColor``; ``SurfaceView`` marks itself for display when its
   effective appearance changes.
3. Tone and quiet-text colors are hand-picked pairs, not catalog colors, because
   Apple's system tones cannot cover both appearances here. Measured against the
   card each one is actually written on: on the light card all four fail
   (systemGreen 2.22:1, systemOrange 2.31:1, systemBlue 3.52:1, systemRed
   3.57:1); on the dark card systemRed (2.74:1), systemBlue (2.91:1) and
   systemOrange (4.22:1) still fail, and only systemGreen clears at 4.66:1 -- so
   there is no single catalog tone set that works, and mixing one passing tone
   with three failing ones would make "healthy" the only readable state. The
   literals below clear 4.5:1 in every combination (thinnest margin: dark error
   at 4.77:1). ``secondaryLabelColor`` is 3.92:1 on the light card and 4.12:1 on
   the dark one, under the 4.5:1 this project already holds its Windows palette
   to, so the quiet gray is a pair too.

Nothing here draws: no ``drawRect_``, no ``NSBezierPath``. Shape is CALayer
geometry on plain views, keeping the shell's existing all-native-controls rule.
"""
from __future__ import annotations

import AppKit as A
import Foundation as F
import Quartz

# --- Metrics -----------------------------------------------------------------
# Content sits PAGE_INSET from the window edge and GUTTER inside the card behind
# it, so a card's edge is always GUTTER away from its text. The card's own inset
# differs by window on purpose: the settings form's document spans the full
# window, so its cards use CARD_INSET (12); the details document reserves room for
# its scroller on the right, so its cards use PAGE_INSET (24) to stay centered in
# the viewport instead of hugging the left edge. Both are asserted: preview.py's
# assert_details_width pins the card/content width relationship exactly.
PAGE_INSET = 24
CARD_INSET = 12        # settings cards; details uses PAGE_INSET
GUTTER = 12
GROUP_GAP = 16
ROW_GAP = 8
LABEL_GAP = 4

RADIUS_CARD = 10
RADIUS_FIELD = 6
# Apple's continuous corner (the "squircle"): the same radius reads visibly
# softer than a circular arc, and it is the shape every system panel uses.
CORNER_CURVE = Quartz.kCACornerCurveContinuous

FIELD_HEIGHT = 28
WELL_PAD_X = 9
BUTTON_HEIGHT = 32
SEAM_HEIGHT = 1


def _gray(value, alpha=1.0):
    return A.NSColor.colorWithSRGBRed_green_blue_alpha_(value, value, value, alpha)


_NAMES = set()


def dynamic(name, light, dark):
    """A color that resolves per appearance at draw time.

    ``light``/``dark`` may themselves be catalog colors: the provider runs with
    the drawing appearance current, so catalog lookups inside resolve correctly.

    ``name`` must be unique per color. NSColor registers these by name and
    controls that store a color resolve it *by name*, so sharing one name made
    every tone the same color: a label recolored from ok to warning kept
    reading green because both were registered as "TokenPlanMonitor".
    """
    if name in _NAMES:
        raise ValueError(f"duplicate dynamic color name {name!r}: AppKit stores these "
                         "by name, so the second registration would silently make "
                         "controls resolve to whichever color was assigned first")
    _NAMES.add(name)

    def provide(trait):
        return dark if is_dark(trait) else light
    return A.NSColor.colorWithName_dynamicProvider_("TokenPlanMonitor." + name, provide)


# Tone pairs. The light values are v0.1.0's numbers, but restated from
# colorWithCalibratedRGB as sRGB -- the same triple is a slightly different,
# slightly darker color, which raises these ratios from v0.1.0's measured
# 5.7-6.2:1 to 7.3-8.2:1, so this is a change of color space as well as a
# restatement. Dark values are new, sized to clear 4.5:1 on the dark card.
# Kept as plain triples so tests/test_macos_theme.py can verify contrast without
# a display or a running app -- the colors below are built straight from them.
TONE_VALUES = {
    "ok": ((0.08, 0.37, 0.18), (0.41, 0.83, 0.57)),
    "warning": ((0.48, 0.29, 0.02), (1.0, 0.72, 0.37)),
    "error": ((0.66, 0.12, 0.09), (1.0, 0.62, 0.62)),
    "info": ((0.0, 0.30, 0.63), (0.52, 0.76, 1.0)),
}

# The surfaces each tone is written on, as measured from rasterized captures:
# light text sits on a 1.000 card or the 0.925 page, dark on 0.274 / 0.198.
SURFACE_VALUES = {
    "light": {"card": (1.0, 1.0, 1.0), "page": (0.925, 0.925, 0.925)},
    "dark": {"card": (0.274, 0.274, 0.274), "page": (0.198, 0.198, 0.198)},
}

SECONDARY_VALUES = ((0.0, 0.0, 0.0, 0.62), (1.0, 1.0, 1.0, 0.72))

# --- Text colors -------------------------------------------------------------
# labelColor is Apple's own and clears 14.9:1 / 7.4:1 on this app's surfaces, so
# it stays catalog. The quiet gray is not: secondaryLabelColor measures 3.95:1
# on a light card and 4.14:1 on a dark one, and it carries every 11-12pt detail
# line here, so it is replaced with a pair that clears 4.5:1 while staying the
# same neutral hue. There is deliberately no third, fainter gray: timestamps
# are information, not decoration.
TEXT = A.NSColor.labelColor()
SECONDARY = dynamic(
    "secondary",
    A.NSColor.colorWithSRGBRed_green_blue_alpha_(*SECONDARY_VALUES[0]),
    A.NSColor.colorWithSRGBRed_green_blue_alpha_(*SECONDARY_VALUES[1]))
ACCENT = A.NSColor.controlAccentColor()
SEPARATOR = A.NSColor.separatorColor()

# --- Surfaces ----------------------------------------------------------------
# The card must read *raised* against the page in both appearances, and no
# single catalog color does that: controlBackgroundColor is pure white in light
# but 0.090 in dark (below the 0.198 page), while
# unemphasizedSelectedContentBackgroundColor is the inverse. Rasterized check:
# light card 1.000 on page 0.925, dark card 0.274 on page 0.198.
CARD = dynamic("card", A.NSColor.controlBackgroundColor(),
               A.NSColor.unemphasizedSelectedContentBackgroundColor())
# Input wells recess instead: they sit inside a card, and textBackgroundColor is
# the dark recess (0.119 under a 0.274 card) but pure white in light, which is
# exactly the card color -- an invisible field. Light uses a flat near-white.
FIELD = dynamic("field", _gray(0.957), A.NSColor.textBackgroundColor())
PAGE = A.NSColor.windowBackgroundColor()

_TONES = {
    role: dynamic("tone-" + role,
                  A.NSColor.colorWithSRGBRed_green_blue_alpha_(*light, 1),
                  A.NSColor.colorWithSRGBRed_green_blue_alpha_(*dark, 1))
    for role, (light, dark) in TONE_VALUES.items()
}

_WEIGHTS = {
    "regular": A.NSFontWeightRegular,
    "medium": A.NSFontWeightMedium,
    "semibold": A.NSFontWeightSemibold,
    "bold": A.NSFontWeightBold,
}

# Corner radius per surface role. Page and seam fill a whole window band, so
# rounding them would clip the window's own edges.
_RADII = {"card": RADIUS_CARD, "field": RADIUS_FIELD, "page": 0, "seam": 0}


def is_dark(appearance):
    """True when ``appearance`` resolves to dark aqua."""
    return appearance.bestMatchFromAppearancesWithNames_(
        [A.NSAppearanceNameAqua, A.NSAppearanceNameDarkAqua]
    ) == A.NSAppearanceNameDarkAqua


def tone(role):
    """Status tone as a dynamic color: assign it once, it follows the system."""
    return _TONES[role]


# --- Type ramp ---------------------------------------------------------------
# Apple's own UI is semibold/medium at these sizes, never true bold: the all
# bold weight was most of what made the old windows feel heavy. ``detail``/
# ``caption`` stay legal-pinned rather than dynamic because label heights are
# measured from the point size (``text_label`` -> ``max(size + 6, ...)``), which
# the y-cursor layout depends on.
ROLES = {
    # window/page title
    "title": (20, "semibold"),
    # grouped section header (was a 22pt bold shout)
    "group": (15, "semibold"),
    # plan name
    "plan": (15, "semibold"),
    # quota window name ("5 小时") and other in-card headers
    "kind": (13, "semibold"),
    # status line, primary button labels
    "status": (13, "semibold"),
    # body copy and control values: the system default size
    "body": (A.NSFont.systemFontSize(), "regular"),
    # secondary explanations
    "detail": (12, "regular"),
    # timestamps and hints
    "caption": (A.NSFont.smallSystemFontSize(), "regular"),
    # remaining-percentage figures in the details window
    "metric": (20, "semibold"),
    # remaining-percentage figures on the desktop card
    "metric_large": (22, "semibold"),
}


def font(role="body"):
    size, weight = ROLES[role]
    return A.NSFont.systemFontOfSize_weight_(size, _WEIGHTS[weight])


def label_font(size=None, role=None, bold=False, numeric=False):
    """The font a label gets, resolved once so weight is never dropped.

    A role carries both a size *and* a weight. ``text_label`` used to take only
    the size and derive weight from the ``bold`` flag alone, so every role that
    asks for semibold (the 15pt group headers) silently rendered Regular and the
    intended hierarchy collapsed to 2pt of size. ``bold`` still overrides,
    because several call sites emphasize a role that is already semibold.
    """
    if role is not None:
        role_size, weight = ROLES[role]
        size = role_size if size is None else size
        if bold:
            weight = "semibold"
    else:
        size = size if size is not None else ROLES["body"][0]
        weight = "semibold" if bold else "regular"
    return (metric_sized(size, weight) if numeric else sized_font(size, weight))


def sized_font(size, weight="regular"):
    """Ramp entry for a call site that lays out by explicit point size.

    ``text_label`` measures its own height from the size it was handed, so
    labels keep taking a number even though the ramp is role-based.
    """
    return A.NSFont.systemFontOfSize_weight_(size, _WEIGHTS[weight])


# SF Rounded is the softer numeral face the redesign wanted. The obvious API
# for it is a PyObjC trap -- ``systemFontOfSize:weight:design:`` exists in the
# metadata but silently returns Helvetica for every design value -- while the
# descriptor route below measurably works and, applied to the tabular-digit
# face, keeps digits fixed width ("111111.1%" and "888888.8%" render at the
# identical 113.965pt). Turn this off if a future OS breaks the descriptor path.
ROUNDED_NUMERALS = True


def _rounded(face, size):
    if not ROUNDED_NUMERALS:
        return face
    descriptor = face.fontDescriptor().fontDescriptorWithDesign_(
        A.NSFontDescriptorSystemDesignRounded)
    return A.NSFont.fontWithDescriptor_size_(descriptor, size) if descriptor else face


def metric_font(role="metric"):
    """Percentages: tabular so they never reflow, rounded for softness."""
    size, weight = ROLES[role]
    return _rounded(A.NSFont.monospacedDigitSystemFontOfSize_weight_(size, _WEIGHTS[weight]), size)


def metric_sized(size, weight="semibold"):
    """Tabular figures at an explicit size (see ``sized_font`` for why)."""
    return _rounded(
        A.NSFont.monospacedDigitSystemFontOfSize_weight_(size, _WEIGHTS[weight]), size)


def role_size(role):
    return ROLES[role][0]


class SurfaceView(A.NSView):
    """A rounded, borderless panel: the inset-grouped card the shell draws with.

    Cards carry no stroke. Depth comes from the fill being raised above the page
    plus the continuous corner, matching System Settings. The only line the
    shell draws at all is the 1pt seam under the action bar.
    """

    fill = "card"
    corner = None

    def isFlipped(self):
        return True

    def wantsUpdateLayer(self):
        # Opt in explicitly rather than relying on AppKit inferring it from the
        # presence of updateLayer: without this, setNeedsDisplay_ routes to
        # drawRect_ (which this class has none of) and a state change like a
        # focus ring never reaches the layer.
        return True

    def updateLayer(self):
        # Every fill is a dynamic color, and updateLayer runs with this view's
        # appearance current, so CGColor() resolves to the right half of each
        # pair. viewDidChangeEffectiveAppearance below re-runs this on a switch.
        layer = self.layer()
        color = {"seam": SEPARATOR, "field": FIELD, "page": PAGE}.get(self.fill, CARD)
        layer.setBackgroundColor_(color.CGColor())
        layer.setCornerRadius_(self.corner if self.corner is not None
                               else _RADII[self.fill])
        layer.setCornerCurve_(CORNER_CURVE)

    def viewDidChangeEffectiveAppearance(self):
        # Re-resolve the stored CGColor for the new appearance. AppKit runs
        # updateLayer again once the view is marked for display.
        self.setNeedsDisplay_(True)
        A.NSView.viewDidChangeEffectiveAppearance(self)


def surface(parent, x, y, width, height, fill="card", corner=None, behind=True, cls=None):
    """Add a surface panel at ``x, y``.

    ``behind=True`` (the default) pushes the panel under every sibling, which is
    how the details window already places card backgrounds after laying their
    text. Input wells pass ``behind=False`` instead: they are added just before
    the field that sits on top of them, so normal sibling order is enough and
    they stay above the group card behind them.
    """
    view = (cls or SurfaceView).alloc().initWithFrame_(F.NSMakeRect(x, y, width, height))
    view.setWantsLayer_(True)
    view.fill = fill
    view.corner = corner
    if behind:
        parent.addSubview_positioned_relativeTo_(view, A.NSWindowBelow, None)
    else:
        parent.addSubview_(view)
    return view
