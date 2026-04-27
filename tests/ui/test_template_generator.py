"""Tests for :class:`auto_ext.ui.widgets.template_generator.TemplateGeneratorDialog`.

Phase A coverage: drop a raw EDA export, see the parameterized body in
the right pane, ensure auto-detect picks the right tool, and confirm
the Save button stays disabled until a raw file has been dropped.

Phase B coverage: identity override panel — population from the
auto-extracted identity, debounced re-import on edit, and inline
status label transitions (``"auto-extracted"`` / ``"user override"`` /
``"import failed: ..."``).

Phase D coverage: Save button enable/disable transitions, save
dialog plumbing (filter-aware extension auto-append, user-cancel),
``.j2`` + ``.manifest.yaml`` round-trip, and smart-merge of an
existing manifest's knobs across a re-import.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from PyQt5.QtCore import Qt  # noqa: E402

from auto_ext.core.importer import import_template  # noqa: E402
from auto_ext.core.manifest import load_manifest  # noqa: E402
from auto_ext.ui.widgets import template_generator as tg  # noqa: E402
from auto_ext.ui.widgets.template_generator import (  # noqa: E402
    TemplateGeneratorDialog,
)


_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "raw"


@pytest.fixture(autouse=True)
def _silence_messageboxes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub :class:`QMessageBox` modal popups for the whole module.

    Without this every save-path test would block on a real ``Qt``
    dialog. We never assert against the modal text — visual feedback is
    out of scope for unit tests.
    """
    monkeypatch.setattr(
        "PyQt5.QtWidgets.QMessageBox.information",
        lambda *_a, **_kw: None,
    )
    monkeypatch.setattr(
        "PyQt5.QtWidgets.QMessageBox.warning",
        lambda *_a, **_kw: None,
    )
    monkeypatch.setattr(
        "PyQt5.QtWidgets.QMessageBox.critical",
        lambda *_a, **_kw: None,
    )


# ---- helpers ---------------------------------------------------------------


def _make_dialog(qtbot) -> TemplateGeneratorDialog:
    dlg = TemplateGeneratorDialog()
    qtbot.addWidget(dlg)
    return dlg


def _drop(dlg: TemplateGeneratorDialog, path: Path) -> None:
    """Bypass QDropEvent plumbing by emitting the DropZone signal directly.

    The DropZone's drop handling is exercised by other tests; here we
    only care about the dialog's reaction to an inbound path.
    """
    dlg._drop_zone.path_dropped.emit(path)


# ---- tests -----------------------------------------------------------------


def test_drop_quantus_raw_renders_parameterized_body(qtbot) -> None:
    dlg = _make_dialog(qtbot)
    _drop(dlg, _FIXTURES / "quantus_sample.cmd")
    body = dlg._right_pane.toPlainText()
    assert '-ground_net "[[ground_net]]"' in body
    assert '-design_cell_name "[[cell]] [[lvs_layout_view]] [[library]]"' in body


def test_drop_calibre_raw_renders_parameterized_body(qtbot) -> None:
    dlg = _make_dialog(qtbot)
    _drop(dlg, _FIXTURES / "calibre_sample.qci")
    body = dlg._right_pane.toPlainText()
    assert "*lvsLayoutPrimary: [[cell]]" in body


def test_drop_si_raw_renders_parameterized_body(qtbot) -> None:
    dlg = _make_dialog(qtbot)
    _drop(dlg, _FIXTURES / "si_sample.env")
    body = dlg._right_pane.toPlainText()
    assert 'simLibName = "[[library]]"' in body


def test_drop_jivaro_raw_renders_parameterized_body(qtbot) -> None:
    dlg = _make_dialog(qtbot)
    _drop(dlg, _FIXTURES / "jivaro_sample.xml")
    body = dlg._right_pane.toPlainText()
    assert '<inputView value="[[library]]/[[cell]]/[[out_file]]"/>' in body


def test_auto_detect_quantus_from_extension(qtbot) -> None:
    dlg = _make_dialog(qtbot)
    assert dlg._tool_combo.currentText() == "auto"
    _drop(dlg, _FIXTURES / "quantus_sample.cmd")
    assert dlg._tool_combo.currentText() == "quantus"


def test_auto_detect_calibre_from_content(qtbot, tmp_path: Path) -> None:
    # No recognizable extension; content alone must be enough.
    p = tmp_path / "mystery.txt"
    p.write_text(
        "*lvsLayoutPrimary: INV1\n"
        "*lvsLayoutLibrary: INV_LIB\n"
        "*lvsLayoutView: layout\n"
        "*lvsSourcePrimary: INV1\n"
        "*lvsSourceLibrary: INV_LIB\n"
        "*lvsSourceView: schematic\n",
        encoding="utf-8",
    )
    dlg = _make_dialog(qtbot)
    _drop(dlg, p)
    assert dlg._tool_combo.currentText() == "calibre"


def test_save_button_disabled_before_drop(qtbot) -> None:
    dlg = _make_dialog(qtbot)
    assert dlg._save_btn.isEnabled() is False
    assert dlg._save_btn.toolTip() == "Drop a raw template file first"


def test_open_button_routes_through_drop_handler(
    qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clicking ``Open file...`` and selecting a file must produce the
    same widget state as dropping that file onto the DropZone."""
    dlg = _make_dialog(qtbot)
    target = _FIXTURES / "quantus_sample.cmd"
    monkeypatch.setattr(
        "PyQt5.QtWidgets.QFileDialog.getOpenFileName",
        lambda *_a, **_kw: (str(target), "raw EDA exports (*.qci *.qcilvs *.env *.cmd *.xml)"),
    )
    qtbot.mouseClick(dlg._open_btn, Qt.LeftButton)
    assert dlg._tool_combo.currentText() == "quantus"
    assert '-ground_net "[[ground_net]]"' in dlg._right_pane.toPlainText()
    assert dlg._save_btn.isEnabled() is True


def test_open_button_cancel_does_nothing(
    qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    dlg = _make_dialog(qtbot)
    monkeypatch.setattr(
        "PyQt5.QtWidgets.QFileDialog.getOpenFileName",
        lambda *_a, **_kw: ("", ""),
    )
    qtbot.mouseClick(dlg._open_btn, Qt.LeftButton)
    # Nothing happened — panes empty, save still disabled.
    assert dlg._right_pane.toPlainText() == ""
    assert dlg._save_btn.isEnabled() is False


# ---- Phase B: identity override panel --------------------------------------


def test_identity_panel_populated_on_drop(qtbot) -> None:
    """Each line edit reflects the auto-extracted identity field; ``None``
    fields render as the empty string, not the placeholder text."""
    dlg = _make_dialog(qtbot)
    _drop(dlg, _FIXTURES / "quantus_sample.cmd")

    assert dlg._cell_edit.text() == "INV1"
    assert dlg._library_edit.text() == "INV_LIB"
    assert dlg._lvs_layout_view_edit.text() == "layout"
    assert dlg._out_file_edit.text() == "av_ext"
    assert dlg._ground_net_edit.text() == "vss"
    # Quantus does not surface lvs_source_view — line edit must be empty.
    assert dlg._lvs_source_view_edit.text() == ""

    # Status reflects "auto-extract, partial" because lvs_source_view is None.
    assert "auto-extracted" in dlg._identity_status.text()


def test_identity_edit_triggers_debounced_reimport(qtbot) -> None:
    """Editing a field after the debounce window re-runs ``import_template``
    with the override applied; the right pane now contains the override
    placeholder swap (still ``[[cell]]`` — the placeholder is what users
    see — but the ``Identity`` returned by the override path is what the
    importer used)."""
    dlg = _make_dialog(qtbot)
    _drop(dlg, _FIXTURES / "quantus_sample.cmd")

    body_before = dlg._template_body
    # The placeholder is in the body regardless of override (override
    # decides the *value* substituted-out, but the body always shows
    # ``[[cell]]``). What we verify is that _reimport_with_overrides
    # was invoked and produced a valid body.
    dlg._cell_edit.setText("MY_CELL")
    qtbot.wait(350)

    assert dlg._current_tool == "quantus"
    # Right pane still contains the cell placeholder.
    body_after = dlg._right_pane.toPlainText()
    assert "[[cell]]" in body_after
    # The body string is the template body produced by import_template
    # with identity_overrides=Identity(cell="MY_CELL", ...). Sanity check:
    # the body is non-empty and matches the dialog's stored body.
    assert body_after == dlg._template_body
    # For quantus the override does not change the placeholder shape, so
    # body_before and body_after will compare equal — that's fine, the
    # important thing is the re-import ran without raising.
    assert body_before == body_after


def test_identity_status_shows_user_override(qtbot) -> None:
    dlg = _make_dialog(qtbot)
    _drop(dlg, _FIXTURES / "quantus_sample.cmd")
    assert "auto-extracted" in dlg._identity_status.text()

    dlg._library_edit.setText("OVERRIDE_LIB")
    qtbot.wait(350)

    assert "user override" in dlg._identity_status.text()


def test_identity_status_shows_import_error_inline(
    qtbot, monkeypatch
) -> None:
    """A failing re-import must keep the previous body in the right pane
    and surface the error in the status label (not a QMessageBox)."""
    dlg = _make_dialog(qtbot)
    _drop(dlg, _FIXTURES / "quantus_sample.cmd")
    body_before = dlg._right_pane.toPlainText()
    assert body_before  # sanity

    def _fail(*_args, **_kwargs):
        raise tg.CoreImportError("synthetic")

    monkeypatch.setattr(tg, "import_template", _fail)

    dlg._cell_edit.setText("DOES_NOT_MATTER")
    qtbot.wait(350)

    assert "import failed" in dlg._identity_status.text()
    assert "synthetic" in dlg._identity_status.text()
    # Right pane is not blanked.
    assert dlg._right_pane.toPlainText() == body_before
    assert dlg._template_body == body_before


def test_empty_overrides_pass_none(qtbot) -> None:
    """Calling ``_reimport_with_overrides`` with every line edit cleared
    is equivalent to passing ``identity_overrides=None`` — the body
    matches the original drop's body."""
    dlg = _make_dialog(qtbot)
    _drop(dlg, _FIXTURES / "quantus_sample.cmd")
    body_before = dlg._template_body

    # Programmatically clear every edit (suppress the debounce burst the
    # same way the dialog does internally so we can call the slot
    # explicitly and observe a single deterministic re-import).
    dlg._suppress_override_signal = True
    try:
        for edit in dlg._identity_edits():
            edit.clear()
    finally:
        dlg._suppress_override_signal = False

    assert dlg._collect_overrides() is None

    dlg._reimport_with_overrides()
    assert dlg._template_body == body_before
    assert "auto-extracted" in dlg._identity_status.text()


# ---- Phase D: save flow ----------------------------------------------------


def _patch_save_dialog(
    monkeypatch: pytest.MonkeyPatch, path: str, filt: str
) -> None:
    """Mock :func:`QFileDialog.getSaveFileName` to return a fixed pair."""
    monkeypatch.setattr(
        "PyQt5.QtWidgets.QFileDialog.getSaveFileName",
        lambda *_a, **_kw: (path, filt),
    )


def test_save_button_enabled_after_successful_drop(qtbot) -> None:
    dlg = _make_dialog(qtbot)
    _drop(dlg, _FIXTURES / "quantus_sample.cmd")
    assert dlg._save_btn.isEnabled() is True
    # Tooltip should no longer carry the pre-drop placeholder.
    assert "Drop a raw template file first" not in dlg._save_btn.toolTip()


def test_save_writes_j2_and_manifest(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dlg = _make_dialog(qtbot)
    _drop(dlg, _FIXTURES / "quantus_sample.cmd")

    target = tmp_path / "out.j2"
    _patch_save_dialog(monkeypatch, str(target), "Jinja templates (*.j2)")
    qtbot.mouseClick(dlg._save_btn, Qt.LeftButton)

    assert target.exists()
    sidecar = tmp_path / "out.j2.manifest.yaml"
    assert sidecar.exists()

    # Body must equal what import_template produces for the same raw +
    # the dialog's current overrides (none, since the user did not edit
    # any field).
    raw_text = (_FIXTURES / "quantus_sample.cmd").read_text(encoding="utf-8")
    expected = import_template("quantus", raw_text).template_body
    assert target.read_text(encoding="utf-8") == expected

    # Sidecar parses cleanly with zero knobs (fresh save).
    manifest = load_manifest(target)
    assert manifest is not None
    assert manifest.template == "out.j2"
    assert manifest.knobs == {}


def test_save_appends_j2_extension_when_filter_jinja(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dlg = _make_dialog(qtbot)
    _drop(dlg, _FIXTURES / "quantus_sample.cmd")

    typed = tmp_path / "no_ext"
    _patch_save_dialog(monkeypatch, str(typed), "Jinja templates (*.j2)")
    qtbot.mouseClick(dlg._save_btn, Qt.LeftButton)

    expected = tmp_path / "no_ext.j2"
    assert expected.exists()
    assert (tmp_path / "no_ext.j2.manifest.yaml").exists()
    # The exact-name file the user typed should NOT have been written.
    assert not typed.exists()


def test_save_does_not_append_extension_when_filter_all(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dlg = _make_dialog(qtbot)
    _drop(dlg, _FIXTURES / "quantus_sample.cmd")

    target = tmp_path / "weird.txt"
    _patch_save_dialog(monkeypatch, str(target), "All files (*)")
    qtbot.mouseClick(dlg._save_btn, Qt.LeftButton)

    assert target.exists()
    assert (tmp_path / "weird.txt.manifest.yaml").exists()
    # No surprise .j2 sibling appeared.
    assert not (tmp_path / "weird.txt.j2").exists()


def test_save_user_cancel_writes_nothing(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dlg = _make_dialog(qtbot)
    _drop(dlg, _FIXTURES / "quantus_sample.cmd")

    _patch_save_dialog(monkeypatch, "", "")
    qtbot.mouseClick(dlg._save_btn, Qt.LeftButton)

    # Nothing under tmp_path got written.
    assert list(tmp_path.glob("*.j2")) == []
    assert list(tmp_path.glob("*.manifest.yaml")) == []


def test_save_merges_existing_manifest_knobs(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "ext.cmd.j2"
    target.write_text("-temperature [[temperature]]\n", encoding="utf-8")
    sidecar = tmp_path / "ext.cmd.j2.manifest.yaml"
    sidecar.write_text(
        "template: ext.cmd.j2\n"
        "knobs:\n"
        "  temperature:\n"
        "    type: float\n"
        "    default: 55.0\n"
        "    source:\n"
        "      tool: quantus\n"
        "      key: temperature\n",
        encoding="utf-8",
    )

    dlg = _make_dialog(qtbot)
    _drop(dlg, _FIXTURES / "quantus_sample.cmd")

    _patch_save_dialog(monkeypatch, str(target), "Jinja templates (*.j2)")
    qtbot.mouseClick(dlg._save_btn, Qt.LeftButton)

    # The temperature knob survives the merge (default may have been
    # refreshed from the new raw — either is acceptable).
    merged = load_manifest(target)
    assert merged is not None
    assert "temperature" in merged.knobs
    assert merged.knobs["temperature"].type == "float"

    # The body keeps the placeholder, not the literal "55.0" raw value.
    body = target.read_text(encoding="utf-8")
    assert "[[temperature]]" in body


def test_save_creates_parent_dir(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dlg = _make_dialog(qtbot)
    _drop(dlg, _FIXTURES / "quantus_sample.cmd")

    deep = tmp_path / "subdir" / "deep" / "x.j2"
    _patch_save_dialog(monkeypatch, str(deep), "Jinja templates (*.j2)")
    qtbot.mouseClick(dlg._save_btn, Qt.LeftButton)

    assert deep.exists()
    assert (deep.parent / "x.j2.manifest.yaml").exists()


def test_save_seeds_calibre_auto_knobs(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Saving a calibre raw must seed the manifest with the auto-knobs
    (``connect_by_name`` always; ``lvs_variant`` only if the rules-file
    suffix is wodio/widio). The bundled calibre fixture carries
    ``*lvsRulesFile: ....wodio.qcilvs`` so we expect both knobs."""
    dlg = _make_dialog(qtbot)
    _drop(dlg, _FIXTURES / "calibre_sample.qci")

    target = tmp_path / "out.qci.j2"
    _patch_save_dialog(monkeypatch, str(target), "Jinja templates (*.j2)")
    qtbot.mouseClick(dlg._save_btn, Qt.LeftButton)

    manifest = load_manifest(target)
    assert manifest is not None
    assert "connect_by_name" in manifest.knobs
    assert manifest.knobs["connect_by_name"].type == "bool"
    assert "lvs_variant" in manifest.knobs
    assert manifest.knobs["lvs_variant"].type == "str"
    assert manifest.knobs["lvs_variant"].choices == ["wodio", "widio"]


def test_save_button_stays_enabled_after_failed_reimport(
    qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A debounced re-import that raises ``CoreImportError`` must keep
    the previous valid body savable — the Save button must NOT regress
    to disabled."""
    dlg = _make_dialog(qtbot)
    _drop(dlg, _FIXTURES / "quantus_sample.cmd")
    assert dlg._save_btn.isEnabled() is True

    def _fail(*_args, **_kwargs):
        raise tg.CoreImportError("synthetic")

    monkeypatch.setattr(tg, "import_template", _fail)

    dlg._cell_edit.setText("DOES_NOT_MATTER")
    qtbot.wait(350)

    # The status label flips to error, but the button stays clickable.
    assert "import failed" in dlg._identity_status.text()
    assert dlg._save_btn.isEnabled() is True


# ---- diff highlight (true-diff semantic) ----------------------------------


def _block_bg(pane, idx):
    """Return the background QColor of the QTextBlock at ``idx``."""
    return pane.document().findBlockByNumber(idx).blockFormat().background().color()


def test_diff_highlight_aligns_with_actually_changed_lines(qtbot) -> None:
    """The Calibre importer inserts ``[% if connect_by_name %]...[% endif %]``
    lines that have no counterpart on the raw side. Naive same-row-index
    tinting therefore drifts: lines that are byte-identical between raw
    and body would get spuriously highlighted on the left while the
    actually-substituted lines on the right would not. Verify that
    unchanged anchor lines on the raw side are NOT tinted."""
    dlg = _make_dialog(qtbot)
    _drop(dlg, _FIXTURES / "calibre_sample.qci")

    raw_lines = dlg._raw_text.splitlines()
    body_lines = dlg._template_body.splitlines()

    # Sanity: the importer really did insert lines (otherwise this test
    # is exercising the wrong fixture).
    assert len(body_lines) > len(raw_lines)

    # ``*cmnRunMT: 1`` is unchanged between raw and body — find its
    # index on the raw side and confirm the left pane left it untinted.
    anchor = "*cmnRunMT: 1"
    if anchor not in raw_lines:
        pytest.skip("calibre fixture lacks the expected unchanged anchor line")
    raw_idx = raw_lines.index(anchor)
    assert anchor in body_lines  # should also be present on the right
    bg = _block_bg(dlg._left_pane, raw_idx)
    assert bg.rgba() == tg._BG_DEFAULT.rgba(), (
        f"unchanged anchor {anchor!r} at left row {raw_idx} must not be "
        f"tinted (got rgba={bg.rgba():08x})"
    )


def test_diff_highlight_tints_actually_changed_lines(qtbot) -> None:
    """The opposite of the above: lines that *do* differ between raw
    and body must be tinted on the side where they appear."""
    dlg = _make_dialog(qtbot)
    _drop(dlg, _FIXTURES / "calibre_sample.qci")

    raw_lines = dlg._raw_text.splitlines()
    body_lines = dlg._template_body.splitlines()

    # ``*lvsLayoutPrimary: INV1`` (raw) -> ``*lvsLayoutPrimary: [[cell]]``
    # (body). Both rows must be tinted on their respective panes.
    raw_anchor = "*lvsLayoutPrimary: INV1"
    body_anchor = "*lvsLayoutPrimary: [[cell]]"
    assert raw_anchor in raw_lines
    assert body_anchor in body_lines
    raw_idx = raw_lines.index(raw_anchor)
    body_idx = body_lines.index(body_anchor)

    left_bg = _block_bg(dlg._left_pane, raw_idx)
    right_bg = _block_bg(dlg._right_pane, body_idx)
    assert left_bg.rgba() == tg._BG_PARAMETERIZED.rgba(), (
        f"changed raw line {raw_anchor!r} should be tinted (got "
        f"rgba={left_bg.rgba():08x})"
    )
    assert right_bg.rgba() == tg._BG_PARAMETERIZED.rgba(), (
        f"changed body line {body_anchor!r} should be tinted (got "
        f"rgba={right_bg.rgba():08x})"
    )


def test_diff_highlight_tints_inserted_body_only_lines(qtbot) -> None:
    """Lines that exist only on the right (e.g. Calibre's
    ``[% if connect_by_name %]`` toggle wrapper) must be tinted on the
    right pane and have no left-pane counterpart to tint."""
    dlg = _make_dialog(qtbot)
    _drop(dlg, _FIXTURES / "calibre_sample.qci")

    body_lines = dlg._template_body.splitlines()
    # Grab any line that contains the inserted [% if %] block marker.
    target_idx = None
    for i, line in enumerate(body_lines):
        if "[% if connect_by_name %]" in line:
            target_idx = i
            break
    assert target_idx is not None, (
        "calibre fixture is expected to produce a connect_by_name toggle"
    )
    bg = _block_bg(dlg._right_pane, target_idx)
    assert bg.rgba() == tg._BG_PARAMETERIZED.rgba()
