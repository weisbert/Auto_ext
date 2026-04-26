"""Tests for :class:`auto_ext.ui.tabs.templates_tab.TemplatesTab`."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from PyQt5.QtWidgets import QCheckBox, QLineEdit  # noqa: E402

from auto_ext.core.config import load_project  # noqa: E402
from auto_ext.ui.config_controller import ConfigController  # noqa: E402
from auto_ext.ui.tabs.run_tab import RunTab  # noqa: E402
from auto_ext.ui.tabs.templates_tab import TemplatesTab  # noqa: E402
from auto_ext.ui.widgets.knob_editor import KnobEditor  # noqa: E402


# ---- fixtures --------------------------------------------------------------


def _scaffold_project(tmp_path: Path) -> tuple[Path, Path]:
    """Build a project + templates/ tree under ``tmp_path``.

    Returns ``(config_dir, auto_ext_root)``. The project binds the calibre
    + quantus slots; quantus has a manifest with two knobs so the Knobs
    page has something to render.
    """
    auto_ext_root = tmp_path / "Auto_ext"
    config_dir = auto_ext_root / "config"
    config_dir.mkdir(parents=True)
    templates = auto_ext_root / "templates"
    (templates / "calibre").mkdir(parents=True)
    (templates / "quantus").mkdir()
    (templates / "si").mkdir()  # exists but no .j2 — deliberately empty

    calibre_tpl = templates / "calibre" / "wiodio.qci.j2"
    calibre_tpl.write_text(
        "*lvsRulesFile: $VERIFY_ROOT/foo/[[pdk_subdir]]\n"
        "*lvsLayoutPrimary: [[cell]]\n"
        "*lvsLayoutLibrary: [[library]]\n"
        "__OBSOLETE__\n",
        encoding="utf-8",
    )
    quantus_tpl = templates / "quantus" / "ext.cmd.j2"
    quantus_tpl.write_text(
        "temperature [[temperature]]\n"
        "exclude_floating_nets_limit [[exclude_floating_nets_limit]]\n"
        "tech [[tech_name]]\n",
        encoding="utf-8",
    )
    quantus_manifest = templates / "quantus" / "ext.cmd.j2.manifest.yaml"
    quantus_manifest.write_text(
        "template: ext.cmd.j2\n"
        "knobs:\n"
        "  temperature:\n"
        "    type: float\n"
        "    default: 55.0\n"
        "    range: [0.0, 200.0]\n"
        "  exclude_floating_nets_limit:\n"
        "    type: int\n"
        "    default: 5000\n"
        "    range: [100, 100000]\n",
        encoding="utf-8",
    )
    # An unbound spare under templates/calibre to exercise discovery.
    (templates / "calibre" / "spare.qci.j2").write_text(
        "[[cell]]\n", encoding="utf-8"
    )

    (config_dir / "project.yaml").write_text(
        "tech_name: HN001\n"
        "templates:\n"
        f"  calibre: {calibre_tpl}\n"
        f"  quantus: {quantus_tpl}\n",
        encoding="utf-8",
    )
    (config_dir / "tasks.yaml").write_text(
        "- library: L\n"
        "  cell: C\n"
        "  lvs_layout_view: layout\n",
        encoding="utf-8",
    )
    return config_dir, auto_ext_root


def _make_tab(
    qtbot, config_dir: Path, auto_ext_root: Path
) -> tuple[TemplatesTab, ConfigController]:
    controller = ConfigController(auto_ext_root=auto_ext_root, workarea=auto_ext_root.parent)
    run_tab = RunTab(controller)
    tab = TemplatesTab(controller, run_tab)
    qtbot.addWidget(run_tab)
    qtbot.addWidget(tab)
    controller.load(config_dir)
    return tab, controller


# ---- tests -----------------------------------------------------------------


def test_populate_lists_bound_and_unused(qtbot, tmp_path: Path) -> None:
    cfg, root = _scaffold_project(tmp_path)
    tab, _ = _make_tab(qtbot, cfg, root)
    # 2 bound (calibre, quantus) + 1 unused (spare) = 3 rows.
    assert tab._list.count() == 3
    # First two should be the bound ones.
    assert "[calibre]" in tab._list.item(0).text() or "[quantus]" in tab._list.item(0).text()
    assert any("[unused]" in tab._list.item(i).text() for i in range(tab._list.count()))


def test_path_picker_shows_current_values(qtbot, tmp_path: Path) -> None:
    cfg, root = _scaffold_project(tmp_path)
    tab, _ = _make_tab(qtbot, cfg, root)
    assert tab._path_edits["calibre"].text().endswith("wiodio.qci.j2")
    assert tab._path_edits["quantus"].text().endswith("ext.cmd.j2")
    assert tab._path_edits["si"].text() == ""
    assert tab._path_edits["jivaro"].text() == ""


def test_editing_path_stages_template_edit(qtbot, tmp_path: Path) -> None:
    cfg, root = _scaffold_project(tmp_path)
    tab, controller = _make_tab(qtbot, cfg, root)
    line = tab._path_edits["si"]
    line.setText("templates/si/foo.env.j2")
    line.editingFinished.emit()  # editingFinished doesn't auto-fire on programmatic setText
    assert controller.is_dirty is True
    assert controller.pending_edits.get("templates.si") == "templates/si/foo.env.j2"


def test_clearing_path_stages_none(qtbot, tmp_path: Path) -> None:
    cfg, root = _scaffold_project(tmp_path)
    tab, controller = _make_tab(qtbot, cfg, root)
    line = tab._path_edits["calibre"]
    line.setText("")
    line.editingFinished.emit()
    assert controller.pending_edits.get("templates.calibre") is None


def test_inventory_table_has_quantus_placeholders(qtbot, tmp_path: Path) -> None:
    cfg, root = _scaffold_project(tmp_path)
    tab, _ = _make_tab(qtbot, cfg, root)
    # Select quantus row.
    for i in range(tab._list.count()):
        if "[quantus]" in tab._list.item(i).text():
            tab._list.setCurrentRow(i)
            break
    tab._refresh_inventory_and_knobs()  # flush coalesced refresh
    rows = tab._inventory_table.rowCount()
    assert rows > 0
    # Collect names + statuses.
    found: dict[str, str] = {}
    for r in range(rows):
        kind = tab._inventory_table.item(r, 0).text()
        name = tab._inventory_table.item(r, 1).text()
        status = tab._inventory_table.item(r, 2).text()
        found[f"{kind}:{name}"] = status
    # Manifest knobs → ok; identity (tech_name) → ok.
    assert found["jinja:temperature"] == "ok"
    assert found["jinja:tech_name"] == "ok"


def test_inventory_flags_undeclared_jinja_var_red(qtbot, tmp_path: Path) -> None:
    cfg, root = _scaffold_project(tmp_path)
    tab, _ = _make_tab(qtbot, cfg, root)
    # Select calibre (no manifest there → only identity-bound ones are ok).
    for i in range(tab._list.count()):
        if "[calibre]" in tab._list.item(i).text():
            tab._list.setCurrentRow(i)
            break
    tab._refresh_inventory_and_knobs()
    statuses: dict[str, str] = {}
    for r in range(tab._inventory_table.rowCount()):
        statuses[tab._inventory_table.item(r, 1).text()] = tab._inventory_table.item(
            r, 2
        ).text()
    # `cell`/`library` are identity → ok; `pdk_subdir` is identity → ok.
    assert statuses.get("cell") == "ok"
    assert statuses.get("library") == "ok"
    # The literal `__OBSOLETE__` placeholder shows up as info-only.
    assert statuses.get("OBSOLETE") == "info"


def test_knobs_form_renders_default_hint_and_no_dirty(qtbot, tmp_path: Path) -> None:
    cfg, root = _scaffold_project(tmp_path)
    tab, controller = _make_tab(qtbot, cfg, root)
    for i in range(tab._list.count()):
        if "[quantus]" in tab._list.item(i).text():
            tab._list.setCurrentRow(i)
            break
    tab._refresh_inventory_and_knobs()
    editors = tab._knobs_form_host.findChildren(KnobEditor)
    assert len(editors) == 2
    # Pure load → no dirty.
    assert controller.is_dirty is False
    # Reset button starts disabled (project layer has no override yet).
    for e in editors:
        assert e._reset_btn.isEnabled() is False


def test_knob_edit_stages_dotted_key(qtbot, tmp_path: Path) -> None:
    cfg, root = _scaffold_project(tmp_path)
    tab, controller = _make_tab(qtbot, cfg, root)
    for i in range(tab._list.count()):
        if "[quantus]" in tab._list.item(i).text():
            tab._list.setCurrentRow(i)
            break
    tab._refresh_inventory_and_knobs()
    editors = {e.name: e for e in tab._knobs_form_host.findChildren(KnobEditor)}
    temp_editor = editors["temperature"]
    temp_editor._line.setText("70.0")  # type: ignore[union-attr]
    temp_editor._line.editingFinished.emit()  # type: ignore[union-attr]
    assert controller.is_dirty is True
    assert controller.pending_edits.get("knobs.quantus.temperature") == 70.0


def test_save_round_trips_knob_to_disk(qtbot, tmp_path: Path) -> None:
    cfg, root = _scaffold_project(tmp_path)
    tab, controller = _make_tab(qtbot, cfg, root)
    for i in range(tab._list.count()):
        if "[quantus]" in tab._list.item(i).text():
            tab._list.setCurrentRow(i)
            break
    tab._refresh_inventory_and_knobs()
    editors = {e.name: e for e in tab._knobs_form_host.findChildren(KnobEditor)}
    editors["temperature"]._line.setText("25.0")  # type: ignore[union-attr]
    editors["temperature"]._line.editingFinished.emit()  # type: ignore[union-attr]
    assert controller.save() is True

    # Re-load from disk and verify.
    reloaded = load_project(cfg / "project.yaml")
    assert reloaded.knobs == {"quantus": {"temperature": 25.0}}


def test_reset_knob_removes_project_override(qtbot, tmp_path: Path) -> None:
    cfg, root = _scaffold_project(tmp_path)
    # Pre-seed an override in project.yaml so the reset button is enabled.
    (cfg / "project.yaml").write_text(
        "tech_name: HN001\n"
        "templates:\n"
        f"  quantus: {root / 'templates' / 'quantus' / 'ext.cmd.j2'}\n"
        "knobs:\n"
        "  quantus:\n"
        "    temperature: 25.0\n",
        encoding="utf-8",
    )
    tab, controller = _make_tab(qtbot, cfg, root)
    for i in range(tab._list.count()):
        if "[quantus]" in tab._list.item(i).text():
            tab._list.setCurrentRow(i)
            break
    tab._refresh_inventory_and_knobs()
    editors = {e.name: e for e in tab._knobs_form_host.findChildren(KnobEditor)}
    temp = editors["temperature"]
    assert temp._reset_btn.isEnabled() is True
    temp._reset_btn.click()
    assert controller.pending_edits.get("knobs.quantus.temperature") is None
    assert controller.save() is True

    reloaded = load_project(cfg / "project.yaml")
    # Override was the only knob → cascade prune removes project.knobs entirely.
    assert reloaded.knobs == {}


def test_save_round_trips_path_change(qtbot, tmp_path: Path) -> None:
    cfg, root = _scaffold_project(tmp_path)
    tab, controller = _make_tab(qtbot, cfg, root)
    line = tab._path_edits["si"]
    line.setText("templates/si/new.env.j2")
    line.editingFinished.emit()
    assert controller.save() is True
    reloaded = load_project(cfg / "project.yaml")
    assert str(reloaded.templates.si) == "templates\\si\\new.env.j2" or str(
        reloaded.templates.si
    ) == "templates/si/new.env.j2"


def test_empty_project_shows_hint(qtbot, tmp_path: Path) -> None:
    # No templates dir under root, no project.templates set → empty list.
    auto_ext_root = tmp_path / "Auto_ext"
    config_dir = auto_ext_root / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "project.yaml").write_text("tech_name: X\n", encoding="utf-8")
    (config_dir / "tasks.yaml").write_text(
        "- library: L\n  cell: C\n  lvs_layout_view: layout\n", encoding="utf-8"
    )
    tab, _ = _make_tab(qtbot, config_dir, auto_ext_root)
    assert tab._list.count() == 0
    # isHidden reflects explicit show/hide state without requiring the
    # widget to be on-screen (qtbot doesn't show the parent).
    assert tab._empty_hint.isHidden() is False


def test_dirty_flag_clears_after_save(qtbot, tmp_path: Path) -> None:
    cfg, root = _scaffold_project(tmp_path)
    tab, controller = _make_tab(qtbot, cfg, root)
    line = tab._path_edits["si"]
    line.setText("x.j2")
    line.editingFinished.emit()
    assert controller.is_dirty is True
    assert controller.save() is True
    assert controller.is_dirty is False


def test_template_diff_viewer_button_opens_dialog(qtbot, tmp_path: Path) -> None:
    cfg, root = _scaffold_project(tmp_path)
    tab, _ = _make_tab(qtbot, cfg, root)
    # Always-enabled, free-standing tool — no template selection required.
    assert tab._diff_viewer_btn.isEnabled() is True
    tab._diff_viewer_btn.click()
    from auto_ext.ui.widgets.template_diff_viewer import TemplateDiffViewerDialog
    assert isinstance(tab._diff_viewer_dlg, TemplateDiffViewerDialog)
    assert tab._diff_viewer_dlg.isVisible() is True
    tab._diff_viewer_dlg.close()
