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

from PyQt5.QtGui import QDoubleValidator, QIntValidator  # noqa: E402
from PyQt5.QtWidgets import QCheckBox, QComboBox, QLineEdit  # noqa: E402

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
    editor = _make(
        qtbot,
        spec(
            type=OptionType.ENUM,
            choices=["auCdl"],
            choices_confidence=Confidence.GUESS,
            default="auCdl",
        ),
    )
    editor.combo().setCurrentText("auLvs")
    assert editor.value() == "auLvs"
    # Typed, not adopted: this recipe's value is not a new catalog member.
    assert editor.choices() == ["auCdl"]


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


def test_a_full_form_of_labels_stays_narrow(qtbot) -> None:
    grid = OptionGrid()
    qtbot.addWidget(grid)
    for index in range(20):
        grid.add_option(
            spec(
                key=f"k{index}",
                template_var=f"v{index}",
                context_path=f"recipe.extraction.f{index}",
                default="a fairly long default value string",
            )
        )
    assert grid.minimumSizeHint().width() <= 500


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
        if opt.type is OptionType.ENUM and opt.choices_confidence is Confidence.GUESS
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


def test_no_enum_anywhere_in_the_catalog_is_a_bare_text_box() -> None:
    """The office report this rule was rewritten for.

    "很多参数你选择的是 blank 填写" -- a value with a finite set of legal
    spellings was being asked for as free text, so a user who does not already
    know the spelling has no way to find it out from the form.
    """

    catalog = builtin_catalog()
    enums = [opt for opt in catalog.options if opt.type is OptionType.ENUM]
    assert [o.key for o in enums if editor_kind(o) is EditorKind.TEXT] == []
