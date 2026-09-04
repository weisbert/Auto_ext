"""Catalog row in, control out.

The cases that matter are the ones where a naive form generator gets it
wrong: a guessed enum rendered as a closed dropdown, a made-up range
enforced as a hard limit, and an unconfirmed value that looks exactly like a
confirmed one. Each has its own test here because each is a decision that was
argued for, not a detail.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from PyQt5.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PyQt5.QtGui import QDoubleValidator, QIntValidator, QWheelEvent  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QComboBox,
    QLineEdit,
)

from auto_ext.catalog import (  # noqa: E402
    Confidence,
    Currently,
    LandingSite,
    OptionSpec,
    OptionType,
    Owner,
    builtin_catalog,
)
from auto_ext.model.common import RenderTarget  # noqa: E402
from auto_ext.ui import theme  # noqa: E402
from auto_ext.ui.widgets.option_editor import (  # noqa: E402
    FROZEN_GLYPH,
    NEEDS_CONFIRMATION,
    NOT_SETTABLE,
    LABEL_MIN_WIDTH,
    VALUE_WIDTH_FLOORS,
    VALUE_WIDTH_MAX,
    VALUE_WIDTH_MIN,
    PAIR_MIN_WIDTH,
    QUESTION_GLYPH,
    BoolOptionEditor,
    ChoiceOptionEditor,
    FreeChoiceOptionEditor,
    MultiChoiceOptionEditor,
    EditorKind,
    ElidedLabel,
    ListOptionEditor,
    NumberOptionEditor,
    OptionGrid,
    OptionGroup,
    OptionLabel,
    TextOptionEditor,
    build_option_editor,
    editor_kind,
    group_label,
    hint_text,
    in_advisory_range,
    option_label,
    option_tooltip,
    template_freezes,
    value_width,
)


def spec(**over) -> OptionSpec:
    """A minimal valid recipe-owned catalog row, overridable field by field."""

    fields: dict[str, object] = {
        "key": "demo_key",
        "template_var": "demo_var",
        "context_path": "recipe.extraction.demo",
        "owner": Owner.RECIPE,
        "type": OptionType.STR,
        "choices_confidence": Confidence.CERTAIN,
        "currently": Currently.JINJA_VAR,
        "observed": False,
        "why": "a demo row",
    }
    fields.update(over)
    return OptionSpec(**fields)  # type: ignore[arg-type]


def _make(qtbot, one: OptionSpec):
    editor = build_option_editor(one)
    qtbot.addWidget(editor)
    return editor


# ---- control choice ------------------------------------------------------


def test_bool_becomes_a_check_box(qtbot) -> None:
    editor = _make(qtbot, spec(type=OptionType.BOOL, default=True))
    assert isinstance(editor, BoolOptionEditor)
    assert isinstance(editor.check_box(), QCheckBox)
    assert editor.value() is True


def test_a_confident_enum_becomes_a_closed_dropdown(qtbot) -> None:
    one = spec(
        type=OptionType.ENUM,
        choices=["wodio", "widio"],
        choices_confidence=Confidence.CERTAIN,
        default="wodio",
    )
    editor = _make(qtbot, one)
    assert editor_kind(one) is EditorKind.COMBO
    assert isinstance(editor, ChoiceOptionEditor)
    assert isinstance(editor.combo(), QComboBox)
    assert editor.choices() == ["wodio", "widio"]
    assert editor.value() == "wodio"


def test_a_likely_enum_is_still_a_dropdown(qtbot) -> None:
    one = spec(
        type=OptionType.ENUM,
        choices=["a", "b"],
        choices_confidence=Confidence.LIKELY,
        default="a",
    )
    assert editor_kind(one) is EditorKind.COMBO
    assert isinstance(_make(qtbot, one), ChoiceOptionEditor)


def test_a_guessed_enum_is_an_editable_dropdown(qtbot) -> None:
    """DECISIONS.md #19, REVISED 2026-08-24 on the user's ruling.

    The old rule was a bare text box: never hand the user a menu of invented
    spellings. In use that was worse, not better -- a blank box gives no idea
    what a legal value even looks like, so the user has to invent a spelling
    from nothing. An editable combo is both halves: the guesses are on the
    drop-down, and anything else can still be typed.
    """

    one = spec(
        type=OptionType.ENUM,
        choices=["auCdl", "spectre", "hspiceD"],
        choices_confidence=Confidence.GUESS,
        default="auCdl",
    )
    assert one.free_input is True
    assert editor_kind(one) is EditorKind.COMBO_FREE

    editor = _make(qtbot, one)
    assert isinstance(editor, FreeChoiceOptionEditor)
    assert editor.combo().isEditable() is True
    assert editor.choices() == ["auCdl", "spectre", "hspiceD"]
    assert editor.value() == "auCdl"

    # The hint no longer repeats the members -- they are in the control now --
    # but it must still say the list is not authoritative.
    hint = hint_text(one)
    assert "guessed" in hint and "other values accepted" in hint
    assert "auCdl" in option_tooltip(one)


def test_a_guessed_enum_accepts_a_spelling_no_one_predicted(qtbot) -> None:
    # Two members, because one is a drop-down with nothing to choose between
    # and resolves to a text box instead -- see
    # ``test_a_lone_enum_member_becomes_a_text_box_that_starts_at_that_member``.
    editor = _make(
        qtbot,
        spec(
            type=OptionType.ENUM,
            choices=["auCdl", "auLvs"],
            choices_confidence=Confidence.GUESS,
            default="auCdl",
        ),
    )
    editor.combo().setCurrentText("spectre")
    assert editor.value() == "spectre"
    # Typed, not adopted: this recipe's value is not a new catalog member.
    assert editor.choices() == ["auCdl", "auLvs"]


def test_numbers_get_a_type_validator(qtbot) -> None:
    integer = _make(qtbot, spec(type=OptionType.INT, default=100))
    assert isinstance(integer, NumberOptionEditor)
    assert isinstance(integer.line_edit().validator(), QIntValidator)
    assert integer.value() == 100

    number = _make(qtbot, spec(type=OptionType.FLOAT, default=0.001))
    assert isinstance(number.line_edit().validator(), QDoubleValidator)
    assert number.value() == pytest.approx(0.001)


def test_a_list_is_one_comma_separated_box(qtbot) -> None:
    editor = _make(
        qtbot, spec(type=OptionType.LIST, default=["auCdl", "schematic"])
    )
    assert isinstance(editor, ListOptionEditor)
    assert editor.value() == ["auCdl", "schematic"]
    editor.line_edit().setText("auCdl,  schematic , spectre")
    assert editor.value() == ["auCdl", "schematic", "spectre"]


@pytest.mark.parametrize(
    "one",
    [
        spec(default="SCHEMATIC"),
        spec(type=OptionType.LIST, default=["auCdl", "schematic", "spectre"]),
        spec(type=OptionType.FLOAT, default=1234.5678),
    ],
    ids=["text", "list", "number"],
)
def test_a_value_wider_than_its_field_shows_its_beginning(qtbot, one) -> None:
    """At the 940px floor ``SCHEMATIC`` must not render as ``EMATIC``.

    ``setText`` parks the cursor at the end, and a line edit scrolls to its
    cursor -- so a truncated value looks like a different value rather than a
    clipped one.
    """

    editor = _make(qtbot, one)
    assert editor.line_edit().cursorPosition() == 0

    editor.set_value(one.default)
    assert editor.line_edit().cursorPosition() == 0


def test_a_structural_row_is_shown_but_not_editable(qtbot) -> None:
    editor = _make(qtbot, spec(type=OptionType.STRUCTURAL))
    assert isinstance(editor, TextOptionEditor)
    assert editor.line_edit().isReadOnly()


# ---- ranges are advisory -------------------------------------------------


def test_an_unverified_range_is_painted_not_enforced(qtbot) -> None:
    """Every range the catalog ships is unverified, so none of them may block.

    A guard rail somebody invented on a machine with no Cadence on it must
    not be able to stop a real extraction -- it may only say it is surprised.
    """

    one = spec(type=OptionType.INT, default=5000, range=(100, 100_000))
    assert one.range_verified is False

    editor = _make(qtbot, one)
    validator = editor.line_edit().validator()
    assert isinstance(validator, QIntValidator)
    assert validator.bottom() < 100, "an unverified low bound was enforced"
    assert validator.top() > 100_000, "an unverified high bound was enforced"

    editor.line_edit().setText("7")
    editor.line_edit().textEdited.emit("7")
    assert editor.value() == 7, "the out-of-range value was rejected"
    assert editor.is_advisory_ok() is False
    assert "(unverified)" in hint_text(one)


def test_a_verified_range_is_enforced(qtbot) -> None:
    one = spec(
        type=OptionType.INT, default=50, range=(0, 100), range_verified=True
    )
    editor = _make(qtbot, one)
    validator = editor.line_edit().validator()
    assert (validator.bottom(), validator.top()) == (0, 100)
    assert "(unverified)" not in hint_text(one)


def test_in_advisory_range_ignores_everything_it_cannot_judge() -> None:
    ranged = spec(type=OptionType.FLOAT, default=1.0, range=(0.0, 2.0))
    assert in_advisory_range(ranged, 1.5) is True
    assert in_advisory_range(ranged, 9.0) is False
    assert in_advisory_range(ranged, None) is True
    assert in_advisory_range(spec(), "anything") is True


# ---- the unconfirmed marker ---------------------------------------------


def test_a_row_with_an_open_question_carries_a_visible_marker(qtbot) -> None:
    one = spec(question="Is a resistor shorted below this value or above it?")
    label = OptionLabel(one)
    qtbot.addWidget(label)

    marker = label.marker()
    assert label.needs_confirmation is True
    assert marker is not None, "an unconfirmed row looks exactly like a confirmed one"
    assert marker.text() == QUESTION_GLYPH
    assert NEEDS_CONFIRMATION in marker.toolTip()
    assert "shorted below" in marker.toolTip()


def test_the_marker_is_a_glyph_and_not_only_a_colour(qtbot) -> None:
    """Greyscale print and colour blindness both have to survive this."""

    label = OptionLabel(spec(question="unknown"))
    qtbot.addWidget(label)
    marker = label.marker()
    assert marker is not None
    assert marker.text().strip() != "", "the marker carries no glyph"

    style = marker.styleSheet()
    for accent in theme.accent_colors():
        assert accent not in style, "the confirmation marker borrowed the accent colour"
    assert theme.WARNING_TEXT_ON_WHITE in style


def test_labels_elide_in_the_middle_so_the_leaf_word_survives(qtbot) -> None:
    """``...threshold absolute`` and ``...threshold relative`` differ at the end.

    Right-elision removes exactly the word that tells the two rows apart,
    which is the one thing a form must never do.
    """

    absolute = OptionLabel(
        spec(
            key="cap_abs",
            template_var="cap_abs",
            context_path="recipe.extraction.coupling_cap_threshold_absolute",
        )
    )
    relative = OptionLabel(
        spec(
            key="cap_rel",
            template_var="cap_rel",
            context_path="recipe.extraction.coupling_cap_threshold_relative",
        )
    )
    for label in (absolute, relative):
        qtbot.addWidget(label)
        label.text_label().resize(90, 20)

    shown_absolute = absolute.text_label().text()
    shown_relative = relative.text_label().text()
    assert shown_absolute != shown_relative, "the two rows elided to the same string"
    # Both are shorter than the full name at this width -- the point is that
    # what survives is the tail, which is where they differ.
    assert len(shown_absolute) < len(absolute.text_label().full_text())
    assert shown_absolute.endswith("ute")
    assert shown_relative.endswith("ive")


def test_a_confirmed_row_has_no_marker(qtbot) -> None:
    label = OptionLabel(spec())
    qtbot.addWidget(label)
    assert label.marker() is None
    assert label.needs_confirmation is False


# ---- row furniture -------------------------------------------------------


def test_the_row_shows_unit_and_default_and_explains_itself(qtbot) -> None:
    one = spec(
        type=OptionType.FLOAT,
        default=0.001,
        unit="ohm",
        why="min_res decides which resistors survive extraction",
    )
    editor = _make(qtbot, one)

    unit = editor.unit_label()
    assert unit is not None and unit.text() == "ohm"

    hint = editor.hint_label()
    assert hint is not None and "default 0.001" in hint.full_text()

    assert "min_res decides" in option_tooltip(one)
    assert "min_res decides" in editor.line_edit().toolTip()


def test_a_landing_site_is_named_in_the_tooltip() -> None:
    one = spec(
        lands_in=[LandingSite(section="extraction_setup", option="-min_res", line=12)]
    )
    assert "-min_res" in option_tooltip(one)


def test_option_label_uses_the_field_path_leaf() -> None:
    one = spec(
        key="extraction_coupling_abs",
        context_path="recipe.extraction.coupling_cap_threshold_absolute",
    )
    assert option_label(one) == "coupling cap threshold absolute"
    assert group_label("extracted_view") == "Extracted view"


# ---- signals -------------------------------------------------------------


def test_a_user_edit_emits_and_a_programmatic_load_does_not(qtbot) -> None:
    """Pushing model state in must not look like a user edit.

    If it did, the screen would mark itself dirty the moment it loaded a
    recipe and the Save button would never be trustworthy again.
    """

    editor = _make(qtbot, spec(type=OptionType.BOOL, default=False))
    seen: list[tuple[str, object]] = []
    editor.value_changed.connect(lambda key, value: seen.append((key, value)))

    editor.set_value(True)
    assert seen == []
    assert editor.value() is True

    editor.check_box().setChecked(False)
    assert seen == [("demo_key", False)]


def test_a_combo_keeps_a_value_it_has_never_heard_of(qtbot) -> None:
    editor = _make(
        qtbot,
        spec(
            type=OptionType.ENUM,
            choices=["wodio", "widio"],
            choices_confidence=Confidence.CERTAIN,
            default="wodio",
        ),
    )
    editor.set_value("wodio_v2")
    assert editor.value() == "wodio_v2"
    assert "wodio_v2" in editor.choices()


def test_set_invalid_marks_the_control_and_says_the_recipe_kept_the_old_value(
    qtbot,
) -> None:
    editor = _make(qtbot, spec())
    editor.set_invalid(True, "Input should be a valid integer")
    assert editor.is_invalid() is True
    control = editor.control()
    assert control is not None
    assert theme.STATUS_FAILED in control.styleSheet()
    assert "previous value" in control.toolTip()

    editor.set_invalid(False)
    assert editor.is_invalid() is False
    assert control.styleSheet() == ""


# ---- sizing --------------------------------------------------------------


def test_an_elided_label_never_widens_the_window(qtbot) -> None:
    label = ElidedLabel("a path long enough to push a window past a 1080p screen" * 4)
    qtbot.addWidget(label)
    assert label.minimumSizeHint().width() == 0


def test_value_width_is_measured_not_typed() -> None:
    """Artboard ``M`` section 4 -- the fix for a 340px box holding ``@``.

    Width comes from the value set, so it is right without anybody
    maintaining it, and a row whose choices grow gets a wider control for
    free.
    """

    # One character in, four characters out: the clamp floor, not the 12 a
    # blanket ``str`` floor would give it. Six delimiter rows are like this.
    assert value_width(spec(default="@")) == VALUE_WIDTH_MIN
    assert value_width(spec(default="[]")) == VALUE_WIDTH_MIN

    # A real string measures itself.
    assert value_width(spec(default="AG RC RE RG")) == len("AG RC RE RG")

    # The widest member wins, not the default: the control has to hold
    # whatever the user picks, not only what it opens on.
    corner = spec(
        type=OptionType.ENUM,
        choices=["typical", "rcworst_t", "cbest"],
        default="cbest",
    )
    assert value_width(corner) == len("rcworst_t")

    # Nothing to measure -> the per-type floor, which is the only case it is
    # for. ``netlist.global_power_sig`` defaults to "" and holds a net name.
    assert value_width(spec(default="")) == VALUE_WIDTH_FLOORS[OptionType.STR]
    assert value_width(spec(type=OptionType.PATH)) == VALUE_WIDTH_FLOORS[OptionType.PATH]

    # And nothing is ever wider than the cap.
    assert value_width(spec(default="x" * 200)) == VALUE_WIDTH_MAX


def test_a_closed_member_list_reads_as_a_count(qtbot) -> None:
    """Artboard ``I1``: eight check boxes on one line become ``8 of 8``.

    The point is that the control's width stops tracking its value. Eight
    members spelled out is 81 characters of row, which is why the old row
    still needed an overflow button; the popup overlays instead, so opening
    it reflows nothing.
    """

    one = spec(
        type=OptionType.LIST,
        choices=["CANONICAL_CAP", "PARASITIC_CAP", "DIODE", "MOS"],
        default=["CANONICAL_CAP", "PARASITIC_CAP", "DIODE", "MOS"],
    )
    editor = _make(qtbot, one)
    assert isinstance(editor, MultiChoiceOptionEditor)
    assert editor.summary_button().text() == "4 of 4"

    # The members are still there, and still the value.
    editor.check_boxes()["DIODE"].setChecked(False)
    assert editor.value() == ["CANONICAL_CAP", "PARASITIC_CAP", "MOS"]
    assert editor.summary_button().text() == "3 of 4"

    # A value pushed in from the model updates the count too -- that path
    # mutes the change signal, which is what keeps the form from marking
    # itself dirty on load.
    editor.set_value(["MOS"])
    assert editor.summary_button().text() == "1 of 4"

    # Width is the count's, not the members'. Compare in pixels: spelling the
    # four members out is what the row used to cost.
    button = editor.summary_button()
    spelled = button.fontMetrics().horizontalAdvance("  ".join(one.choices or []))
    assert button.width() < spelled


def _wide_grid(qtbot, count: int = 20) -> OptionGrid:
    grid = OptionGrid()
    qtbot.addWidget(grid)
    for index in range(count):
        grid.add_option(
            spec(
                key=f"k{index}",
                template_var=f"v{index}",
                context_path=f"recipe.extraction.f{index}",
                default="a fairly long default value string",
            )
        )
    return grid


def test_a_squeezed_grid_drops_a_column_instead_of_its_labels(qtbot) -> None:
    """Artboard ``M`` section 4, and the answer to the label-vanishing defect.

    The grid used to keep two pairs on a line at any width, which it could
    only do by letting the label columns shrink to nothing: at 1280px the
    whole ``Output`` section rendered as anonymous check boxes. The contract
    is now the other way round -- the *column count* is what gives way, and a
    label is never narrower than :data:`LABEL_MIN_WIDTH`.
    """

    grid = _wide_grid(qtbot)

    assert grid.columns_for_width(1200) == 2
    assert grid.columns_for_width(2 * PAIR_MIN_WIDTH) == 2
    assert grid.columns_for_width(2 * PAIR_MIN_WIDTH - 1) == 1
    # The form pane at the 940px window floor, minus the recipe list and the
    # page margins. One pair, whole line.
    assert grid.columns_for_width(560) == 1
    # Never zero, however little there is to give.
    assert grid.columns_for_width(0) == 1


def test_labels_keep_their_floor_at_one_column(qtbot) -> None:
    grid = _wide_grid(qtbot)
    # Qt DEFERS the resize event of a widget that has never been shown
    # (``WA_PendingResizeEvent``) rather than sending or posting it, so the
    # fold is driven by showing, not by resizing. The application always
    # shows this widget; a test has to say so.
    grid.resize(560, 400)
    grid.show()
    QApplication.processEvents()

    assert grid.columns() == 1
    for key in grid.keys():
        label = grid.label(key)
        assert label is not None
        assert label.minimumWidth() == LABEL_MIN_WIDTH
    # A single pair fits inside the narrowest form pane the window allows.
    assert grid.minimumSizeHint().width() <= 560

    # And it folds back: the surplus at 1280 buys the second column again.
    grid.resize(1200, 400)
    QApplication.processEvents()
    assert grid.columns() == 2


# ---- grid and group ------------------------------------------------------


def test_the_grid_lays_two_pairs_to_a_row(qtbot) -> None:
    grid = OptionGrid()
    qtbot.addWidget(grid)
    for index in range(5):
        grid.add_option(
            spec(
                key=f"k{index}",
                template_var=f"v{index}",
                context_path=f"recipe.extraction.f{index}",
            )
        )
    assert grid.option_count() == 5
    assert grid.layout().rowCount() == 3
    assert grid.keys() == [f"k{i}" for i in range(5)]
    assert grid.editor("k3") is not None
    assert grid.editor("nope") is None


def test_the_grid_reports_which_rows_need_confirmation(qtbot) -> None:
    grid = OptionGrid()
    qtbot.addWidget(grid)
    grid.add_option(spec(key="plain", template_var="p", context_path="recipe.extraction.p"))
    grid.add_option(
        spec(
            key="unsure",
            template_var="u",
            context_path="recipe.extraction.u",
            question="what unit is this?",
        )
    )
    assert grid.needs_confirmation_keys() == ["unsure"]


def test_the_grid_forwards_one_signal_for_every_editor(qtbot) -> None:
    grid = OptionGrid()
    qtbot.addWidget(grid)
    grid.add_option(spec(type=OptionType.BOOL, default=False))
    seen: list[tuple[str, object]] = []
    grid.value_changed.connect(lambda key, value: seen.append((key, value)))
    grid.editor("demo_key").check_box().setChecked(True)
    assert seen == [("demo_key", True)]


def test_a_group_header_names_the_group_and_the_file(qtbot) -> None:
    group = OptionGroup("Extraction", "quantus/ext.cmd.j2")
    qtbot.addWidget(group)
    assert "Extraction" in group.header_text()
    assert "quantus/ext.cmd.j2" in group.header_text()
    assert group.title() == "Extraction"
    assert group.subtitle() == "quantus/ext.cmd.j2"


def test_a_group_forwards_its_grid_signal(qtbot) -> None:
    group = OptionGroup("Extraction")
    qtbot.addWidget(group)
    group.add_option(spec(type=OptionType.BOOL, default=False))
    seen: list[str] = []
    group.value_changed.connect(lambda key, _value: seen.append(key))
    group.grid.editor("demo_key").check_box().setChecked(True)
    assert seen == ["demo_key"]


# ---- a row the template still freezes -------------------------------------
#
# The population is empty in the shipped catalog and
# ``tests/catalog/test_catalog.py::test_no_owned_row_is_left_hardcoded`` keeps
# it that way, so these cases build the row themselves. That is the right way
# round: the display has to exist *before* somebody adds such a row, or the
# first person to do it ships an editable field that fails at run time.


def frozen_spec(**over) -> OptionSpec:
    """A recipe row whose value the shipped template types in as a literal."""

    fields: dict[str, object] = {
        "key": "frozen_key",
        "template_var": "frozen_var",
        "context_path": "recipe.extraction.frozen",
        "currently": Currently.HARDCODED_LITERAL,
        "default": "TYPICAL",
        "lands_in": [
            LandingSite(
                target=RenderTarget.QUANTUS_EXT,
                section="process_technology",
                option="-technology_corner",
            )
        ],
    }
    fields.update(over)
    return spec(**fields)


def test_a_row_the_template_freezes_is_recognised_from_the_catalog() -> None:
    assert template_freezes(frozen_spec()) is True
    assert template_freezes(spec()) is False


def test_a_frozen_row_disables_its_control_instead_of_hiding_the_row(qtbot) -> None:
    """The whole point: the refusal happens on the page, not mid-run.

    ``check_representable`` still refuses this value at render time and must
    -- that is the last line of defence. It is a bad first one, because it
    arrives after the form is filled, the recipe saved and the run started.
    """

    editor = _make(qtbot, frozen_spec())
    assert editor.is_frozen is True
    assert editor.control().isEnabled() is False
    assert editor.line_edit().isReadOnly() is True
    # ...showing the literal the run will actually write.
    assert editor.value() == "TYPICAL"


def test_a_frozen_row_says_so_in_the_hint_and_the_tooltip(qtbot) -> None:
    one = frozen_spec()
    assert hint_text(one).startswith(NOT_SETTABLE.lower())
    assert NOT_SETTABLE in option_tooltip(one)
    # The tooltip names the file, so the user knows where the literal lives.
    assert "quantus.ext.cmd" in option_tooltip(one)
    editor = _make(qtbot, one)
    assert NOT_SETTABLE in editor.control().toolTip()


def test_a_frozen_row_is_marked_grey_not_amber(qtbot) -> None:
    """A different mark from the ``?``, because it is a different sentence.

    ``?`` means "nobody has confirmed this value against a real tool". ``=``
    means "the tool cannot be told anything else yet". Borrowing the amber
    would put a caution mark on a row whose value is correct.
    """

    label = OptionLabel(frozen_spec())
    qtbot.addWidget(label)
    marker = label.frozen_marker()
    assert marker is not None
    assert marker.text() == FROZEN_GLYPH
    assert NOT_SETTABLE in marker.toolTip()
    assert theme.TEXT_DISABLED in marker.styleSheet()
    assert theme.WARNING_TEXT_ON_WHITE not in marker.styleSheet()
    assert label.is_frozen is True
    assert OptionLabel(spec()).frozen_marker() is None


def test_a_frozen_row_carrying_both_marks_keeps_them_apart(qtbot) -> None:
    label = OptionLabel(frozen_spec(question="is TYPICAL really the name?"))
    qtbot.addWidget(label)
    assert label.frozen_marker() is not None
    assert label.marker() is not None
    assert label.frozen_marker().text() != label.marker().text()


def test_a_frozen_field_shows_the_literal_not_what_the_recipe_holds(qtbot) -> None:
    """Otherwise the disabled control lies as loudly as the editable one did.

    A recipe carrying ``RCWORST`` for a row the template freezes to
    ``TYPICAL`` renders ``TYPICAL``. A field reading ``RCWORST`` -- disabled
    or not -- tells the user the run will use RCWORST, which is the original
    bug wearing a grey border.
    """

    editor = _make(qtbot, frozen_spec())
    seen: list[tuple] = []
    editor.value_changed.connect(lambda *args: seen.append(args))

    editor.set_value("RCWORST")
    assert editor.value() == "TYPICAL"
    assert editor.frozen_override() == "RCWORST"
    assert "RCWORST" in editor.control().toolTip()
    assert "RCWORST" in editor.hint_label().full_text()
    assert seen == [], "pushing state in from the model is not a user edit"

    # ...and it clears again when the recipe agrees with the literal.
    editor.set_value("TYPICAL")
    assert editor.frozen_override() is None
    assert NOT_SETTABLE.lower() in editor.hint_label().full_text()


def test_a_settable_row_keeps_taking_values_from_the_model(qtbot) -> None:
    """The guard above must not have swallowed the normal path."""

    editor = _make(qtbot, spec(default="rc_coupled"))
    editor.set_value("r_only")
    assert editor.value() == "r_only"
    assert editor.frozen_override() is None


def test_the_grid_reports_which_rows_are_frozen_and_which_diverge(qtbot) -> None:
    grid = OptionGrid()
    qtbot.addWidget(grid)
    grid.add_option(spec(key="live", template_var="live_var", default="a"))
    grid.add_option(frozen_spec(key="frozen", template_var="frozen_var"))

    assert grid.frozen_keys() == ["frozen"]
    assert grid.frozen_overrides() == {}
    grid.set_value("frozen", "RCWORST")
    assert grid.frozen_overrides() == {"frozen": "RCWORST"}
    assert grid.values()["frozen"] == "TYPICAL"


def test_every_editor_kind_honours_the_freeze(qtbot) -> None:
    """One test rather than five, because the guard lives in the base class and
    a subclass that re-implements ``set_value`` would slip past it."""

    rows = [
        frozen_spec(key="b", template_var="b_var", type=OptionType.BOOL, default=True),
        frozen_spec(
            key="c",
            template_var="c_var",
            type=OptionType.ENUM,
            choices=["TYPICAL", "RCWORST"],
            choices_confidence=Confidence.CERTAIN,
            default="TYPICAL",
        ),
        frozen_spec(key="n", template_var="n_var", type=OptionType.FLOAT, default=1.0),
        frozen_spec(key="l", template_var="l_var", type=OptionType.LIST, default=["vdd"]),
        frozen_spec(key="t", template_var="t_var"),
    ]
    others = {"b": False, "c": "RCWORST", "n": 2.5, "l": ["vss"], "t": "OTHER"}
    for one in rows:
        editor = _make(qtbot, one)
        assert editor.is_frozen is True
        assert editor.control().isEnabled() is False
        before = editor.value()
        editor.set_value(others[one.key])
        assert editor.value() == before, one.key
        assert editor.frozen_override() == others[one.key], one.key


# ---- against the real catalog --------------------------------------------


def test_every_recipe_owned_catalog_row_builds_a_control(qtbot) -> None:
    """No row in the shipped catalog may be un-renderable.

    This is the test that fails when somebody adds an option type, or a
    combination of type and confidence, that the form does not know about --
    which is the exact moment it should fail, rather than at the office.
    """

    catalog = builtin_catalog()
    rows = [opt for opt in catalog.by_owner(Owner.RECIPE) if opt.recipe_field_path]
    assert rows, "the catalog has no recipe-owned rows at all"

    grid = OptionGrid()
    qtbot.addWidget(grid)
    for one in rows:
        editor = grid.add_option(one)
        assert editor.kind() is editor_kind(one)
    assert grid.option_count() == len(rows)


def test_the_catalog_has_guessed_enums_and_every_one_is_an_editable_dropdown() -> None:
    catalog = builtin_catalog()
    guessed = [
        opt
        for opt in catalog.by_owner(Owner.RECIPE)
        if opt.type is OptionType.ENUM
        and opt.choices_confidence is Confidence.GUESS
        # A guessed list of one is not a list. It resolves to a text box
        # holding that one spelling; the rule below covers those.
        and len(opt.choices or []) >= 2
    ]
    assert guessed, "the fixture this rule exists for has disappeared"
    assert all(editor_kind(opt) is EditorKind.COMBO_FREE for opt in guessed)


def test_a_closed_list_is_a_row_of_check_boxes(qtbot) -> None:
    """"General里面的stage也是blank填写...应该是checkbox才对"."""

    one = spec(
        type=OptionType.LIST,
        choices=["si", "strmout", "calibre"],
        choices_confidence=Confidence.CERTAIN,
        default=["si", "calibre"],
    )
    assert editor_kind(one) is EditorKind.CHECKS

    editor = _make(qtbot, one)
    assert isinstance(editor, MultiChoiceOptionEditor)
    assert list(editor.check_boxes()) == ["si", "strmout", "calibre"]
    assert editor.value() == ["si", "calibre"]
    assert editor.other_edit() is None, "a closed set needs no escape hatch"

    editor.check_boxes()["strmout"].setChecked(True)
    # Catalog order, not click order: the runner reads stages as a sequence.
    assert editor.value() == ["si", "strmout", "calibre"]


def test_a_guessed_list_gets_boxes_plus_a_field_for_what_nobody_predicted(
    qtbot,
) -> None:
    one = spec(
        type=OptionType.LIST,
        choices=["CANONICAL_RES", "DIODE"],
        choices_confidence=Confidence.GUESS,
        default=["CANONICAL_RES"],
    )
    editor = _make(qtbot, one)
    assert isinstance(editor, MultiChoiceOptionEditor)
    other = editor.other_edit()
    assert other is not None

    other.setText("MOSCAP, BIPOLAR")
    assert editor.value() == ["CANONICAL_RES", "MOSCAP", "BIPOLAR"]

    # And a value from a hand-written recipe round-trips into that field
    # rather than being dropped or turned into a new box.
    editor.set_value(["DIODE", "SOMETHING_ELSE"])
    assert editor.value() == ["DIODE", "SOMETHING_ELSE"]
    assert list(editor.check_boxes()) == ["CANONICAL_RES", "DIODE"]


def test_a_list_with_no_member_table_stays_a_text_field(qtbot) -> None:
    """There is nothing to draw boxes for, so free text is the honest control."""

    one = spec(
        type=OptionType.LIST,
        choices=None,
        choices_confidence=Confidence.LIKELY,
        default=["auCdl", "schematic"],
    )
    assert editor_kind(one) is EditorKind.LIST
    assert isinstance(_make(qtbot, one), ListOptionEditor)


def test_a_nullable_row_says_what_leaving_it_empty_means(qtbot) -> None:
    """The fallback existed in the model and was invisible in the form.

    ``temperature_c`` shows 55.0 and nothing told the user that clearing the
    box hands the decision to the corner.
    """

    one = spec(
        type=OptionType.FLOAT,
        choices_confidence=Confidence.CERTAIN,
        default=55.0,
        nullable=True,
        placeholder="the temperature this corner suggests",
    )
    hint = hint_text(one)
    assert "default 55" in hint
    assert "empty = the temperature this corner suggests" in hint


def test_an_empty_string_default_is_spelled_out_not_left_blank(qtbot) -> None:
    """"default " with nothing after it is a blank hint beside a blank box."""

    one = spec(type=OptionType.STR, choices_confidence=Confidence.CERTAIN, default="")
    assert "default (empty)" in hint_text(one)


def test_no_enum_anywhere_in_the_catalog_is_a_bare_text_box(qtbot) -> None:
    """The office report this rule was rewritten for.

    "很多参数你选择的是 blank 填写" -- a value with a finite set of legal
    spellings was being asked for as free text, so a user who does not already
    know the spelling has no way to find it out from the form.

    **Bare** is the operative word, and it is what the rule turns on rather
    than the control class. A one-member list resolves to a text box now
    (W-36), and that does not reopen this defect: the box carries the one
    spelling the catalog knows, so the spelling is still on screen. What is
    forbidden is an enum whose control shows the user nothing.
    """

    catalog = builtin_catalog()
    enums = [opt for opt in catalog.options if opt.type is OptionType.ENUM]
    blank = []
    for one in enums:
        if editor_kind(one) is not EditorKind.TEXT:
            continue
        editor = build_option_editor(one)
        qtbot.addWidget(editor)
        edit = editor.line_edit()
        if not edit.text() and not edit.placeholderText():
            blank.append(one.key)
    assert blank == [], (
        f"these enums ask for a spelling and offer none: {blank}"
    )


# ---- artboard I1: the popup's two shortcuts ------------------------------------


def test_all_and_none_tick_every_member_in_one_action(qtbot) -> None:
    """With eight members the common edit is "everything except one".

    Doing that by hand is eight clicks to get back to where you started.
    """

    from auto_ext.catalog import builtin_catalog

    spec = builtin_catalog().option("output_xy")
    editor = build_option_editor(spec)
    qtbot.addWidget(editor)

    seen: list[object] = []
    editor.value_changed.connect(lambda _k, v: seen.append(v))

    editor.none_button().click()
    assert editor.value() == []
    editor.all_button().click()
    assert len(editor.value()) == len(spec.choices)

    # One emission per click, not one per member: eight validations and
    # eight repaints for one user action is the difference between instant
    # and visibly slow over a forwarded X11 link.
    assert len(seen) == 2


def test_the_popup_names_the_list_its_all_and_none_belong_to(qtbot) -> None:
    """Three ``all`` / ``none`` pairs on one screen and no way to tell them apart.

    The buttons live inside a ``QMenu`` overlay; the row label that would
    identify them stays outside it, on the form the overlay is covering. So
    the popup for ``requested_stages``, the popup for ``output_xy`` and the
    popup for ``output_form`` are three identical panels of check boxes, and
    clicking ``all`` in one of them is a five-stage flow change or an
    eight-column output change depending on which one is open.

    ``requested_stages`` stands in for the retired ``stages`` row here: it is
    the surviving stage list, drawn by the run bar rather than by this form,
    and the widget contract it exercises is the same one.
    """

    catalog = builtin_catalog()
    headers = {}
    for key in ("requested_stages", "output_xy", "output_form"):
        editor = build_option_editor(catalog.option(key))
        qtbot.addWidget(editor)
        editor.summary_button().menu().popup(editor.mapToGlobal(editor.rect().topLeft()))
        try:
            header = editor.popup_header()
            assert header.isVisible(), f"{key}: the popup opened with no header"
            headers[key] = header.text()
        finally:
            editor.summary_button().menu().close()

    # The label, which is the key with its underscores opened up.
    assert headers["requested_stages"] == "requested stages"
    # The row label and the catalog key differ here, and error messages quote
    # the key -- so this one has to carry both.
    assert "emit" in headers["output_form"]
    assert "output_form" in headers["output_form"]
    assert len(set(headers.values())) == 3, (
        f"two popups are still indistinguishable: {headers}"
    )


def test_an_open_member_list_can_still_take_a_value_nobody_predicted(qtbot) -> None:
    """The other half of I1: type a value the list does not have."""

    # Built here rather than found in the catalog: no shipped row is a
    # GUESSED member list today, and the affordance has to keep working for
    # the day one is added -- that is precisely when a value nobody predicted
    # turns up.
    one = spec(
        type=OptionType.LIST,
        choices=["a", "b"],
        default=["a"],
        choices_confidence=Confidence.GUESS,
    )
    assert one.free_input, "a guessed member list must stay open"
    editor = build_option_editor(one)
    qtbot.addWidget(editor)

    assert editor.other_edit() is not None
    editor.other_edit().setText("a_value_nobody_predicted")
    editor.other_edit().textEdited.emit("a_value_nobody_predicted")
    assert "a_value_nobody_predicted" in editor.value()


# ---- W-10: a wheel over the form must not edit anything -------------------


def _wheel(widget) -> QWheelEvent:
    """One notch down, delivered where the cursor is."""

    centre = QPointF(widget.rect().center())
    return QWheelEvent(
        centre,
        QPointF(widget.mapToGlobal(widget.rect().center())),
        QPoint(0, -120),
        QPoint(0, -120),
        Qt.NoButton,
        Qt.NoModifier,
        Qt.NoScrollPhase,
        False,
    )


def _combo_spec() -> OptionSpec:
    return spec(
        type=OptionType.ENUM,
        choices=["rc_coupled", "c_only_coupled", "r_only"],
        choices_confidence=Confidence.CERTAIN,
        default="rc_coupled",
    )


def test_a_wheel_over_an_unfocused_combo_does_not_change_the_recipe(qtbot) -> None:
    """Scrolling the 87-row form must not silently pick another value.

    A ``QComboBox`` defaults to ``Qt.WheelFocus``: it takes focus FROM the
    wheel and then steps its own current index, whether or not it is the
    thing the user was working in. One flick down the form therefore rewrote
    whichever rows the cursor crossed -- the extract-rule type among them --
    with no click, no keystroke and nothing on screen naming what moved.
    """

    editor = _make(qtbot, _combo_spec())
    seen: list[object] = []
    editor.value_changed.connect(lambda _k, v: seen.append(v))

    combo = editor.combo()
    assert not combo.hasFocus()
    QApplication.sendEvent(combo, _wheel(combo))

    assert editor.value() == "rc_coupled", "a wheel notch edited an unfocused combo"
    assert seen == [], "an unfocused combo reported an edit nobody made"


def test_a_wheel_still_works_once_the_user_is_in_the_combo(qtbot) -> None:
    """The guard is about focus, not about taking the wheel away.

    Somebody who has clicked into a combo means to change it, and refusing
    them the wheel there would be a second defect rather than a fix.
    """

    editor = _make(qtbot, _combo_spec())
    editor.show()
    qtbot.waitExposed(editor)

    combo = editor.combo()
    # ``setFocus`` alone is not enough: ``hasFocus`` is also false while some
    # other window is active, which in a full-suite run is whichever widget
    # the previous test left on screen. Claiming the active window here is
    # what makes the assertion about the guard rather than about the window
    # manager's mood.
    QApplication.setActiveWindow(editor)
    combo.setFocus()
    qtbot.waitUntil(combo.hasFocus, timeout=2000)

    QApplication.sendEvent(combo, _wheel(combo))
    assert editor.value() == "c_only_coupled"


def test_an_unfocused_combo_passes_the_wheel_on_rather_than_eating_it(qtbot) -> None:
    """Ignoring is not swallowing: the form still scrolls under the cursor.

    Consuming the event instead would trade a silent edit for a form that
    stops scrolling wherever a combo happens to be, which is the same class
    of surprise pointed the other way. Qt propagates a wheel event to the
    parent only while the receiver leaves it un-accepted.
    """

    editor = _make(qtbot, _combo_spec())
    combo = editor.combo()

    assert combo.focusPolicy() == Qt.StrongFocus, (
        "a WheelFocus combo takes focus from the wheel itself, which is how "
        "the notch that should have scrolled the form became an edit"
    )

    event = _wheel(combo)
    event.accept()
    combo.wheelEvent(event)
    assert event.isAccepted() is False, (
        "the unfocused combo accepted the wheel, so the scroll area under it "
        "never sees the notch"
    )


def test_an_extract_rule_combo_is_guarded_the_same_way(qtbot) -> None:
    """The sub-form's two combos sit in the same scrolled column.

    ``extract -type`` is the single most consequential value on the screen --
    RC-coupled against C-only is the difference between a real extraction and
    a cheap one -- and it was the row a wheel was most likely to cross.
    """

    from auto_ext.ui.widgets.extract_rules import ExtractRuleRow

    catalog = builtin_catalog()
    row = ExtractRuleRow(
        selection_spec=catalog.option("extract_selection"),
        type_spec=catalog.option("extract_type"),
    )
    qtbot.addWidget(row)

    changes: list[object] = []
    row.changed.connect(lambda: changes.append(row.value()))

    for combo in (row.selection_combo(), row.type_combo()):
        assert combo.focusPolicy() == Qt.StrongFocus, combo
        before = combo.currentText()
        assert not combo.hasFocus()
        QApplication.sendEvent(combo, _wheel(combo))
        assert combo.currentText() == before, "a wheel notch edited an extract rule"

    assert changes == [], "the sub-form reported a rule change nobody made"


# ---- W-9, W-34, W-35: one vocabulary in the grey hint ---------------------


def _hint_parts(one: OptionSpec) -> list[str]:
    """The hint as the user reads it: one clause per separator."""

    text = hint_text(one)
    return [part.strip() for part in text.split("·") if part.strip()]


def test_no_hint_claims_a_default_the_row_does_not_have() -> None:
    """"default" with nothing after it is a contract with no terms.

    Seven nullable ``bool`` rows rendered exactly that -- ``default  · empty
    = (tool default)`` -- because the hint took the bool branch whether or
    not there WAS a default. A reader has to decide whether the blank means
    "off", "nothing is sent" or "we did not write it down", and those are
    three different command files.
    """

    for one in builtin_catalog().options:
        for part in _hint_parts(one):
            if part.startswith("default"):
                assert part != "default" and part != "default ", (
                    f"{one.key}: the hint says 'default' and then stops"
                )
                assert part[len("default") :].strip(), (
                    f"{one.key}: 'default' with no value after it -- {part!r}"
                )


def test_every_nullable_row_says_what_leaving_it_empty_does() -> None:
    """``default X`` and ``unset -- X`` are two contracts, and the row must pick.

    On a box showing ``auto`` under the hint ``default auto``, a user cannot
    tell whether ``auto`` is a literal being sent to Quantus or "I have not
    chosen yet", and those render different command files. The rule that
    settles it is structural rather than per-row: a row whose hint carries no
    empty-clause always sends what is in the box, and a nullable row is
    therefore obliged to carry one.
    """

    silent = []
    for one in builtin_catalog().options:
        if not one.nullable:
            continue
        hint = hint_text(one)
        if "empty = " not in hint and "unset " not in hint:
            silent.append(one.key)
    assert silent == [], (
        "these nullable rows never say what an empty box does, so the user "
        f"cannot tell a literal from an unmade choice: {silent}"
    )


def test_a_nullable_row_with_a_default_says_both_halves() -> None:
    """``temperature_c`` is the shape every nullable row has to have.

    It shows 55.0 and clearing it hands the decision to the corner. Saying
    only one of those two things is what made the fallback -- which existed
    in the model the whole time -- unreachable in the one place it could be
    used.
    """

    both = [
        one
        for one in builtin_catalog().options
        if one.nullable and one.default is not None
    ]
    assert both, "the fixture this rule exists for has disappeared"
    for one in both:
        hint = hint_text(one)
        assert f"default {one.default}" in hint, one.key
        assert f"empty = {one.placeholder}" in hint, one.key


def test_an_unverified_range_says_the_RANGE_is_what_nobody_checked() -> None:
    """"-55 - 175 (unverified)" -- unverified by whom, the range or the value?

    ``range_verified`` is a statement about the bounds: they were invented on
    a machine with no Cadence on it. Reading it as a statement about the
    VALUE says the opposite of what the field does, which is to accept the
    number as typed.
    """

    one = spec(type=OptionType.INT, default=5000, range=(100, 100_000))
    hint = hint_text(one)
    assert "advisory range" in hint, (
        f"the hint qualifies something, but never says it is the range: {hint!r}"
    )
    assert "(unverified)" in hint

    checked = spec(
        type=OptionType.INT, default=50, range=(0, 100), range_verified=True
    )
    assert "advisory" not in hint_text(checked)
    assert "range 0 " in hint_text(checked)


def test_every_offered_list_says_how_far_it_can_be_trusted() -> None:
    """The absence of "guessed list" carried no information at all.

    Nine drop-downs said their list was a guess and the other twenty said
    nothing, which reads either as "verified" or as "nobody labelled this
    one" -- and there was no way to tell which from the form. Confidence is
    a column in the catalog; it has to reach the hint, not only the tooltip.
    """

    unmarked = []
    for one in builtin_catalog().options:
        if not one.choices:
            continue
        hint = hint_text(one)
        if "list" not in hint:
            unmarked.append((one.key, one.choices_confidence.value))
    assert unmarked == [], (
        "these rows offer a value set and never say how far it can be "
        f"trusted: {unmarked}"
    )


def test_a_confirmed_list_is_marked_positively_not_only_in_the_tooltip() -> None:
    certain = spec(
        type=OptionType.ENUM,
        choices=["wodio", "widio"],
        choices_confidence=Confidence.CERTAIN,
        default="wodio",
    )
    guessed = spec(
        type=OptionType.ENUM,
        choices=["standard"],
        choices_confidence=Confidence.GUESS,
        default="standard",
    )
    likely = spec(
        type=OptionType.ENUM,
        choices=["a", "b"],
        choices_confidence=Confidence.LIKELY,
        default="a",
    )

    assert "confirmed list" in hint_text(certain)
    assert "guessed list" in hint_text(guessed)
    assert "not confirmed" in hint_text(likely)
    # Three different claims, three different sentences: a reader must never
    # have to work out which one they are looking at from what is missing.
    assert len({hint_text(certain), hint_text(guessed), hint_text(likely)}) == 3


# ---- W-36: a one-item drop-down is a text box wearing a costume ----------


def test_no_editor_is_a_drop_down_with_nothing_to_choose_between(qtbot) -> None:
    """``reduction_criterion`` offers exactly one member, and it is a guess.

    The project already ruled on this shape for lists -- "with no members to
    draw, a text box is the honest control" (``UX_VALIDATION.md``) -- and an
    enum with one member is the same claim with an extra arrow drawn on it:
    it looks like a menu, opens onto a single line, and hides the fact that
    the catalog carries an open question about what else the tool takes.
    """

    grid = OptionGrid()
    qtbot.addWidget(grid)
    costumes = []
    for one in builtin_catalog().options:
        if one.type is not OptionType.ENUM:
            continue
        editor = build_option_editor(one)
        combo = getattr(editor, "combo", None)
        if combo is not None and combo().count() < 2:
            costumes.append((one.key, list(one.choices or [])))
        editor.deleteLater()
    assert costumes == [], (
        f"these drop-downs have nothing to choose between: {costumes}"
    )


def test_a_lone_enum_member_becomes_a_text_box_that_starts_at_that_member(
    qtbot,
) -> None:
    """Honest, not empty: the one spelling we do know is still the value.

    Turning it into a blank box would trade a misleading menu for the exact
    defect the editable combo was introduced to fix -- a field with no idea
    what a legal value even looks like.
    """

    one = builtin_catalog().option("reduction_criterion")
    assert one.choices == ["standard"], "the fixture this rule exists for moved"
    assert editor_kind(one) is EditorKind.TEXT

    editor = _make(qtbot, one)
    assert isinstance(editor, TextOptionEditor)
    assert editor.value() == "standard"


def test_a_nullable_enum_keeps_its_drop_down_because_unset_is_a_choice(
    qtbot,
) -> None:
    """"(from the profile)" is a second entry a user genuinely picks between."""

    one = spec(
        type=OptionType.ENUM,
        choices=["LT"],
        choices_confidence=Confidence.CERTAIN,
        nullable=True,
        placeholder="(not emitted)",
    )
    assert editor_kind(one) is EditorKind.COMBO
    editor = _make(qtbot, one)
    assert editor.combo().count() == 2


def test_a_profile_sourced_enum_keeps_its_drop_down(qtbot) -> None:
    """Its real list arrives with the PdkProfile, not from the catalog.

    The demo profile has one corner and the shipped PDK has nine, so a rule
    that counted the catalog's frozen list would turn the corner picker into
    a text box on exactly the machines where it matters most.
    """

    one = builtin_catalog().option("lvs_deck_variant")
    assert one.choices_from is not None
    assert editor_kind(one) is EditorKind.COMBO
