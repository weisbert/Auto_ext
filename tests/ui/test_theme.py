"""Design-token tests: the rules the canvas states, asserted.

These need no Qt -- :mod:`auto_ext.ui.theme` is a pure token module plus a
string builder -- so they run even where PyQt5 is unavailable.

The headline case is :func:`test_the_accent_is_never_a_status`. The design
canvas calls it out in bold: ``#1f5fa8`` means "selected" or "this is the
primary action", never "passed", "failed" or "warning". A blue that
sometimes means "go" and sometimes means "you clicked this" is unreadable
at a glance, which is the entire job of a status column.
"""

from __future__ import annotations

import re

from auto_ext.ui import theme
from auto_ext.ui.models import STATUS_COLOR

#: Every glyph the design permits. Everything here is in DejaVu, so an
#: X11-forwarded CentOS 7 session renders it rather than a tofu box.
ALLOWED_GLYPHS = frozenset("✓✗▶■–·⇆▾▴▼…")

_HEX = re.compile(r"#[0-9a-fA-F]{6}")


# ---- inheritance from ui/models.py --------------------------------------


def test_status_scale_is_inherited_not_copied() -> None:
    """Every status colour comes from ``STATUS_COLOR``, running excepted.

    Copying the palette into a second dict is how two truths start
    drifting: the old tabs would keep painting one green and the new
    screens another.
    """

    assert set(theme.STATUS_TEXT) == set(STATUS_COLOR)
    for status, color in STATUS_COLOR.items():
        if status == "running":
            continue
        assert theme.STATUS_TEXT[status] == color, status


def test_running_is_the_only_deviation_and_it_is_darker() -> None:
    """``#0080ff`` fails contrast as small text on white; ``#0f6fd1`` does not."""

    assert STATUS_COLOR["running"] == "#0080ff"
    assert theme.STATUS_TEXT["running"] == theme.RUNNING_TEXT == "#0f6fd1"

    deviations = [
        status
        for status, color in theme.STATUS_TEXT.items()
        if STATUS_COLOR.get(status) != color
    ]
    assert deviations == ["running"]

    # Darker, and the same hue family: red and green channels drop, blue
    # stays dominant.
    old = _rgb(STATUS_COLOR["running"])
    new = _rgb(theme.RUNNING_TEXT)
    assert _relative_luminance(new) < _relative_luminance(old)
    assert _contrast(new, _rgb(theme.SURFACE_CARD)) >= 4.5


def test_named_status_handles_point_at_the_inherited_map() -> None:
    assert theme.STATUS_PASSED == theme.STATUS_TEXT["passed"]
    assert theme.STATUS_FAILED == theme.STATUS_TEXT["failed"]
    assert theme.STATUS_WARNING == theme.STATUS_TEXT["cancelled"]
    assert theme.STATUS_RUNNING == theme.STATUS_TEXT["running"]
    assert theme.STATUS_SKIPPED == theme.STATUS_TEXT["skipped"]
    assert theme.STATUS_DRY_RUN == theme.STATUS_TEXT["dry_run"]


def test_status_color_falls_back_to_neutral() -> None:
    """An unmodelled status is painted neutral, never guessed into red."""

    assert theme.status_color("passed") == theme.STATUS_PASSED
    assert theme.status_color("something_new") == theme.TEXT_SECONDARY


# ---- the accent is not a status -----------------------------------------


def test_the_accent_is_never_a_status() -> None:
    """No accent value may appear anywhere in a status-carrying token.

    This is the canvas rule that the pre-redesign code broke, and the one
    most likely to be broken again by someone reaching for "a nice blue".
    """

    accents = theme.accent_colors()
    assert theme.ACCENT in accents

    status_values = set(theme.STATUS_TEXT.values())
    status_values |= set(theme.STATUS_FILL.values())
    status_values |= set(theme.STATUS_LINE.values())
    status_values |= set(theme.FAILURE_CODE_COLOR.values())
    status_values |= {theme.WARNING_TEXT_ON_WHITE, theme.RUNNING_TEXT}

    overlap = accents & status_values
    assert not overlap, f"accent colours leaked into the status scale: {sorted(overlap)}"


def test_the_global_stylesheet_paints_no_status_colour() -> None:
    """Separation in the other direction: status is never a global rule.

    ``build_qss`` styles surfaces, text, selection and focus. A status hue
    belongs to the one widget that knows the status, applied when it knows
    it -- not baked into a selector that would then be the second place to
    edit when ``STATUS_COLOR`` moves.
    """

    qss = theme.build_qss()
    status_values = (
        set(theme.STATUS_TEXT.values())
        | set(theme.STATUS_FILL.values())
        | set(theme.STATUS_LINE.values())
        | {theme.WARNING_TEXT_ON_WHITE}
    )
    leaked = sorted(c for c in status_values if c in qss)
    assert not leaked, f"status colours hard-coded into the global QSS: {leaked}"


def test_failure_classes_share_two_hues_and_differ_by_code() -> None:
    """LIC/CFG amber ("fix the environment"), LVS/CRS red ("fix the design").

    Four classes, two colours: greyscale printouts and colour-blind readers
    get the three-letter code, which is the real discriminator.
    """

    codes = theme.FAILURE_CODE_COLOR
    assert set(codes) == {"LIC", "CFG", "LVS", "CRS"}
    assert all(len(code) == 3 and code.isupper() for code in codes)

    assert codes["LIC"] == codes["CFG"] == theme.STATUS_WARNING
    assert codes["LVS"] == codes["CRS"] == theme.STATUS_FAILED
    assert theme.STATUS_WARNING != theme.STATUS_FAILED
    assert len(set(codes.values())) == 2


# ---- glyphs, type, metrics ----------------------------------------------


def test_glyphs_stay_inside_the_dejavu_set() -> None:
    for status, glyph in theme.STATUS_GLYPH.items():
        assert len(glyph) == 1, status
        assert glyph in ALLOWED_GLYPHS, f"{status} uses {glyph!r}, outside the design set"


def test_only_two_font_families_and_both_ship_with_centos7() -> None:
    assert theme.FONT_SANS_FAMILIES == ("DejaVu Sans", "Liberation Sans", "sans-serif")
    assert theme.FONT_MONO_FAMILIES == (
        "DejaVu Sans Mono",
        "Liberation Mono",
        "monospace",
    )
    qss = theme.build_qss()
    for declaration in re.findall(r"font-family:\s*([^;]+);", qss):
        assert declaration.strip() in (theme.FONT_SANS, theme.FONT_MONO), declaration


def test_nothing_renders_below_eleven_pixels() -> None:
    assert theme.FONT_SIZE_MIN == 11
    sizes = [
        theme.FONT_SIZE_TITLE,
        theme.FONT_SIZE_SECTION,
        theme.FONT_SIZE_BODY,
        theme.FONT_SIZE_META,
        theme.FONT_SIZE_MONO,
        theme.FONT_SIZE_MONO_HERO,
    ]
    assert min(sizes) >= theme.FONT_SIZE_MIN

    qss = theme.build_qss()
    declared = [int(px) for px in re.findall(r"font-size:\s*(\d+)px", qss)]
    assert declared, "the stylesheet should set a base font size"
    assert min(declared) >= theme.FONT_SIZE_MIN


def test_metrics_match_the_canvas() -> None:
    assert theme.ROW_HEIGHT == 24
    assert theme.STAGE_CHIP_ROW_HEIGHT == 26
    assert theme.TABLE_HEADER_HEIGHT == 22
    assert theme.TOOLBAR_HEIGHT == 32
    assert (theme.TITLEBAR_HEIGHT, theme.STATUSBAR_HEIGHT) == (34, 22)
    assert theme.NAV_ITEM_HEIGHT == 30
    assert (theme.NAV_RAIL_WIDTH, theme.NAV_RAIL_COLLAPSED_WIDTH) == (132, 44)
    assert theme.SELECTED_BAR_WIDTH == 3
    assert theme.FOCUS_BORDER_WIDTH == 1
    assert theme.FOCUS_BORDER_COLOR == theme.ACCENT
    assert (theme.WINDOW_MIN_WIDTH, theme.WINDOW_MIN_HEIGHT) == (940, 560)


def test_spacing_is_a_closed_ramp() -> None:
    assert theme.SPACING_RAMP == (2, 4, 6, 8, 10, 12, 16)
    assert [
        theme.SPACE_XXS,
        theme.SPACE_XS,
        theme.SPACE_SM,
        theme.SPACE_MD,
        theme.SPACE_LG,
        theme.SPACE_XL,
        theme.SPACE_XXL,
    ] == list(theme.SPACING_RAMP)


def test_corners_are_square_except_push_buttons() -> None:
    assert theme.RADIUS == 0
    assert theme.RADIUS_BUTTON == 2
    radii = {int(px) for px in re.findall(r"border-radius:\s*(\d+)px", theme.build_qss())}
    assert radii <= {theme.RADIUS, theme.RADIUS_BUTTON}


# ---- the stylesheet itself ----------------------------------------------


def test_build_qss_returns_a_stable_non_empty_string() -> None:
    first = theme.build_qss()
    assert isinstance(first, str) and first.strip()
    assert theme.build_qss() == first


def test_stylesheet_has_no_gradient_shadow_animation_or_emoji() -> None:
    """X11 forwarding makes every large repaint visible. None of these ship."""

    qss = theme.build_qss()
    for banned in ("gradient", "box-shadow", "animation", "transition", "image:"):
        assert banned not in qss, banned
    assert qss.isascii(), "the stylesheet must stay ASCII -- no emoji, no glyphs"


def test_stylesheet_avoids_a_universal_background_rule() -> None:
    """``QWidget { background: ... }`` repaints the entire tree on restyle."""

    for rule in re.findall(r"QWidget\s*\{([^}]*)\}", theme.build_qss()):
        assert "background" not in rule


def test_every_colour_in_the_stylesheet_is_a_declared_token() -> None:
    """No hex literal in the QSS that is not one of the tokens above."""

    declared = {
        value
        for name, value in vars(theme).items()
        if name.isupper() and isinstance(value, str) and _HEX.fullmatch(value)
    }
    for group in (theme.STATUS_TEXT, theme.STATUS_FILL, theme.STATUS_LINE):
        declared |= set(group.values())

    used = set(_HEX.findall(theme.build_qss()))
    assert used <= declared, f"undeclared colours in QSS: {sorted(used - declared)}"


def test_object_names_are_exported_for_the_screens_to_target() -> None:
    """The screens style their own chrome against these, never a literal."""

    names = {
        theme.OBJ_TITLEBAR,
        theme.OBJ_STATUSBAR,
        theme.OBJ_NAV_RAIL,
        theme.OBJ_NAV_ITEM,
        theme.OBJ_NAV_LABEL,
        theme.OBJ_NAV_COUNT,
        theme.OBJ_STACK,
        theme.OBJ_HEALTH_BADGE,
        theme.OBJ_SETUP_DRAWER,
    }
    qss = theme.build_qss()
    for name in names:
        assert f"#{name}" in qss, name


def test_public_names_are_all_exported() -> None:
    missing = [name for name in theme.__all__ if not hasattr(theme, name)]
    assert not missing


# ---- helpers ------------------------------------------------------------


def _rgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    """WCAG 2.x relative luminance."""

    channels = []
    for raw in rgb:
        c = raw / 255.0
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, lb = _relative_luminance(a), _relative_luminance(b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)
