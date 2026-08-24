"""Dump every control the GUI puts in front of a user, as plain text.

Why
---
The first real office session found eight defects in one sitting, and the two
worst classes of them are invisible to the test suite by construction:

* **unreachable** -- a setting with no control at all. ``extraction.corner``
  had none, and the screen's own docstring documented the omission as
  deliberate, so nobody was going to notice by reading the code.
* **unusable** -- a control that exists and is the wrong kind. ``stages`` was
  asked for as a comma-separated string; fourteen options with a closed set of
  legal spellings were asked for as blank text boxes. Both are reachable, both
  pass every assertion, and both are unanswerable by a user who does not
  already know the answer.

``tests/ui/test_reachability.py`` catches the first class mechanically. The
second is a judgement about what a user can be expected to know, and no
assertion catches it -- it needs someone who does not already know the system
to try to use it and report where they got stuck.

This script produces the input for that: the full control inventory, with no
source code attached. Hand the output to a reviewer -- or to an agent briefed
as a first-time user with a concrete task and no repository access -- and ask
what they cannot figure out. Everything they name is a real defect, because
the inventory is literally all a user has.

Usage
-----
::

    python scripts/ui_inventory.py                     # empty project
    python scripts/ui_inventory.py --config-dir <dir>  # a real one
    python scripts/ui_inventory.py --screen recipes

Runs off-screen (``QT_QPA_PLATFORM=offscreen``), so it needs no display and is
safe over a slow X11 link or in CI.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# The hints carry "·" and "—". On a Windows console the default codepage
# mangles both, and this output exists to be read by someone judging whether
# the wording is clear -- so it must arrive as written.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _control_kind(editor: object) -> str:
    """What the user sees, in the words a user would use."""

    name = type(editor).__name__
    return {
        "BoolOptionEditor": "checkbox",
        "ChoiceOptionEditor": "dropdown (closed)",
        "FreeChoiceOptionEditor": "dropdown (editable)",
        "MultiChoiceOptionEditor": "checkbox row",
        "NumberOptionEditor": "number box",
        "ListOptionEditor": "text box, comma separated",
        "TextOptionEditor": "text box",
    }.get(name, name)


def _describe_editor(editor: object) -> list[str]:
    """One control, as a reader with no source access would meet it."""

    from auto_ext.ui.widgets.option_editor import hint_text

    spec = editor.spec  # type: ignore[attr-defined]
    lines = [f"    {spec.key}"]
    lines.append(f"      control : {_control_kind(editor)}")
    lines.append(f"      value   : {editor.value()!r}")  # type: ignore[attr-defined]
    choices = getattr(editor, "choices", None)
    if callable(choices):
        lines.append(f"      offers  : {choices()}")
    boxes = getattr(editor, "check_boxes", None)
    if callable(boxes):
        lines.append(f"      offers  : {list(boxes())}")
    hint = hint_text(spec)
    if hint:
        lines.append(f"      hint    : {hint}")
    if not editor.isEnabled():  # type: ignore[attr-defined]
        lines.append("      DISABLED")
    return lines


def dump_recipes(window: object) -> str:
    from auto_ext.ui.widgets.option_editor import group_label

    screen = window.shell.page("recipes")  # type: ignore[attr-defined]
    out = ["=== Recipes screen ===", ""]
    out.append(f"  title field : {screen.name_edit().text()!r} (editable)")
    out.append(f"  buttons     : {_buttons(screen)}")
    out.append("")
    for name, group in screen.groups().items():
        out.append(f"  [{group_label(name)}]")
        for key in group.grid.keys():
            editor = screen.editor(key)
            if editor is not None:
                out += _describe_editor(editor)
        out.append("")
    return "\n".join(out)


def dump_cells(window: object) -> str:
    from auto_ext.ui.screens.cells_screen import COLUMN_TITLES, EDITABLE_FIELDS

    screen = window.shell.page("cells")  # type: ignore[attr-defined]
    table = screen.table
    out = ["=== Cells screen ===", "", f"  mode    : {screen.column_mode()}"]
    out.append(f"  buttons : {_buttons(screen)}")
    out.append("")
    for column, title in enumerate(COLUMN_TITLES):
        if table.isColumnHidden(column):
            continue
        editable = "editable" if column in EDITABLE_FIELDS else "read-only"
        out.append(f"  column {column}: {title or '(check)'!r:22} {editable}")
    out.append("")
    return "\n".join(out)


def dump_setup(window: object) -> str:
    screen = window.setup_drawer  # type: ignore[attr-defined]
    out = ["=== Setup drawer ===", "", f"  buttons : {_buttons(screen)}", ""]
    return "\n".join(out)


def dump_project(window: object) -> str:
    """The Project screen: workspace.yaml + the PDK profile, field by field.

    Prints the help line as well as the label. Both objects are full of terms
    a first-time reader has no way to guess ("dir_expr", "filename_pattern",
    "preserve cell list"), so the sentence under the control IS the control's
    usability -- which is exactly the class of defect this inventory is for.
    """

    from auto_ext.ui.screens.project_screen import field_editors

    screen = window.shell.page("project")  # type: ignore[attr-defined]
    out = ["=== Project screen ===", "", f"  buttons : {_buttons(screen)}", ""]
    group = None
    for spec in field_editors():
        if spec.group != group:
            group = spec.group
            out.append(f"  [{group}]")
        row = screen.row(spec.path)
        state = "enabled" if row is not None and row.isEnabled() else "disabled"
        value = "" if row is None else _repr_value(row)
        out.append(f"    {spec.label:22} {spec.kind.value:8} {state:8} {value}")
        out.append(f"      {spec.help}")
        if spec.unset_means:
            out.append(f"      unset: {spec.unset_means}")
    out.append("")
    return "\n".join(out)


def _repr_value(row: object) -> str:
    """One short rendering of what a row currently shows."""

    try:
        value = row.value()  # type: ignore[attr-defined]
    except Exception as exc:  # a half-typed mapping line, for instance
        return f"<unreadable: {exc}>"
    text = repr(value)
    return text if len(text) <= 60 else text[:57] + "..."


def _buttons(widget: object) -> list[str]:
    from PyQt5.QtWidgets import QPushButton

    return [
        b.text()
        for b in widget.findChildren(QPushButton)  # type: ignore[attr-defined]
        if b.text() and b.isVisible() or b.text()
    ]


_DUMPERS = {
    "recipes": dump_recipes,
    "cells": dump_cells,
    "project": dump_project,
    "setup": dump_setup,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=None)
    parser.add_argument("--auto-ext-root", type=Path, default=None)
    parser.add_argument("--screen", choices=sorted(_DUMPERS), action="append")
    args = parser.parse_args(argv)

    from PyQt5.QtWidgets import QApplication

    from auto_ext.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(config_dir=args.config_dir, auto_ext_root=args.auto_ext_root)
    wanted = args.screen or sorted(_DUMPERS)
    print("\n".join(_DUMPERS[name](window) for name in wanted))
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
