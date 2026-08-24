"""The Setup drawer (design canvas artboard 1h).

Setup is not a tab. It is a 520px drawer that slides in from the title-bar
health badge, and it exists to answer one question -- *can I run right now,
and if not, what exactly do I type?* Every row is one check; a check that
failed carries its fix on the same screen, in the same block, so the user
never has to go and look the answer up.

The verdict is never computed here. :mod:`auto_ext.core.health` owns it:
:func:`~auto_ext.core.health.check_profile` evaluates the checks and every
:class:`~auto_ext.model.pdk.PdkCheckResult` already carries the ``fix_hint``
that names the exact command to source or the exact YAML field to edit. This
module renders that report and nothing more.

What it asks the host to do
---------------------------
The drawer performs no I/O of its own:

* :attr:`SetupDrawer.recheck_requested` -- re-run the checks (the host has the
  profile; the drawer only has a report),
* :attr:`SetupDrawer.close_requested` -- close the drawer (the shell owns the
  open/closed flag),
* :attr:`SetupDrawer.override_requested` -- pin an environment variable, which
  means writing ``env_overrides`` in a profile,
* :attr:`SetupDrawer.edit_field_requested` -- open the profile field a fix
  hint names, on the Project screen. The drawer says *what* to change and only
  the host can show *where*.

Both of those are offered **only when a receiver is connected**, and the pin
row explains itself in a tooltip when it is not. A button that silently does
nothing is worse than a disabled one -- and worse than absent, which is what
the edit button is without a host.

Assumptions
-----------
* **Grouping comes from the ``check_id`` prefix.**
  :func:`auto_ext.core.health.default_checks` builds ids as ``env.*``,
  ``pdk.*``, ``lvs.*``, ``qrc.*`` and ``tool.*``, and
  :class:`~auto_ext.model.pdk.PdkCheckResult` does not carry the
  :class:`~auto_ext.model.pdk.PdkCheckKind` that produced it. The prefix is
  therefore the only grouping key available to a reader of the report or of
  the cached ``<profile>.health.json``. An unrecognised prefix lands in
  "Other checks" rather than being dropped.
* **``WARN`` is drawn with the exchange glyph, not a cross.** Canvas 1h uses
  it for an env var that came from an override rather than from the shell,
  which is the same "you deliberately deviated" semantic the amber tone
  carries everywhere else in this app.
* **The two-way fix block of canvas 1h is rendered for env-var checks only.**
  The canvas's second option ("pin it here") is meaningful only for something
  that has a value to pin. A missing deck directory is fixed in the profile
  YAML, which the ``fix_hint`` spells out and the Edit button now opens.
* **Which field a check is about comes from a map, not from ``PdkCheck.target``.**
  ``target`` holds a field name only for ``FIELD_SET`` checks; for the rest it
  is a resolved path expression or an executable. The map is
  :data:`auto_ext.ui.project_fields.CHECK_FIELDS`, next to the field inventory
  it makes claims about.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from PyQt5.QtCore import QSize, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from auto_ext.model.pdk import CheckStatus, PdkCheckResult, PdkHealthReport
from auto_ext.ui import theme
from auto_ext.ui.project_fields import field_for_check
from auto_ext.ui.widgets.failure_chip import PathLabel

__all__ = [
    "GROUP_ORDER",
    "GROUP_OTHER",
    "GROUP_TITLES",
    "SetupDrawer",
    "SetupGroup",
    "group_for",
    "group_results",
    "row_label",
    "status_glyph",
    "summary_text",
]


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------

#: ``check_id`` prefix -> section key. See the module Assumptions.
_PREFIX_GROUP: dict[str, str] = {
    "env": "env",
    "pdk": "paths",
    "lvs": "paths",
    "qrc": "paths",
    "tool": "tools",
}

#: Everything whose prefix is not in :data:`_PREFIX_GROUP`.
GROUP_OTHER = "other"

#: Sections in the order canvas 1h stacks them.
GROUP_ORDER: tuple[str, ...] = ("env", "paths", "tools", GROUP_OTHER)

GROUP_TITLES: dict[str, str] = {
    "env": "Shell environment",
    "paths": "Paths and PDK files",
    "tools": "Tools on PATH",
    GROUP_OTHER: "Other checks",
}

#: Glyph per check status. All four are on the project whitelist.
_STATUS_GLYPH: dict[CheckStatus, str] = {
    CheckStatus.OK: theme.STATUS_GLYPH["passed"],
    CheckStatus.FAIL: theme.STATUS_GLYPH["failed"],
    CheckStatus.WARN: "⇆",
    CheckStatus.UNKNOWN: theme.STATUS_GLYPH["pending"],
}

_STATUS_COLOR: dict[CheckStatus, str] = {
    CheckStatus.OK: theme.STATUS_PASSED,
    CheckStatus.FAIL: theme.STATUS_FAILED,
    CheckStatus.WARN: theme.STATUS_WARNING,
    CheckStatus.UNKNOWN: theme.TEXT_DISABLED,
}


def group_for(check_id: str) -> str:
    """Section key for ``check_id``, from its dotted prefix."""

    prefix = str(check_id).split(".", 1)[0]
    return _PREFIX_GROUP.get(prefix, GROUP_OTHER)


class SetupGroup(NamedTuple):
    """One section of the drawer."""

    key: str
    title: str
    results: list[PdkCheckResult]


def group_results(report: PdkHealthReport | None) -> list[SetupGroup]:
    """Bucket a report's results into the drawer's sections, in canvas order.

    Empty sections are dropped; the order inside a section is the report's,
    which is the order :func:`auto_ext.core.health.default_checks` declared
    them in and therefore the order the user's setup script establishes them.
    """

    if report is None:
        return []
    buckets: dict[str, list[PdkCheckResult]] = {key: [] for key in GROUP_ORDER}
    for result in report.results:
        buckets[group_for(result.check_id)].append(result)
    return [
        SetupGroup(key=key, title=GROUP_TITLES[key], results=buckets[key])
        for key in GROUP_ORDER
        if buckets[key]
    ]


def row_label(result: PdkCheckResult) -> str:
    """The short name for the 152px title column (canvas 1h).

    The declared ``title`` is a sentence -- "Environment variable
    PDK_LAYER_MAP_FILE", "calibre binary (calibre)" -- and a sentence does not
    fit a fixed column. The canvas puts the *thing being checked* there, so
    this pulls it out: the variable name for an ``env.*`` check, the stage
    name for a ``tool.*`` one, and the title itself for everything else, where
    no shorter form exists. The full title always survives as the tooltip.
    """

    check_id = str(result.check_id)
    title = (result.title or check_id).strip()
    group = group_for(check_id)
    if group == "env" and title:
        # ``check_id`` lowercases the name (slugs must), so the title is the
        # only place the real, case-sensitive variable name survives.
        return title.split()[-1]
    if check_id.startswith("tool."):
        return check_id.split(".", 1)[1]
    return title


def status_glyph(status: CheckStatus) -> tuple[str, str]:
    """``(glyph, colour)`` for one check status."""

    status = CheckStatus(status)
    return (
        _STATUS_GLYPH.get(status, theme.STATUS_GLYPH["pending"]),
        _STATUS_COLOR.get(status, theme.TEXT_DISABLED),
    )


def summary_text(report: PdkHealthReport | None) -> str:
    """``"11 checks - 10 ok - 1 failing"`` for the drawer header.

    Counts blocking results as "failing" and non-blocking ones as "warning",
    which is the split :class:`~auto_ext.model.pdk.PdkHealthReport` itself
    makes -- an optional tool that is missing warns, it does not fail.
    """

    if report is None:
        return "not checked yet"
    total = len(report.results)
    if total == 0:
        return "no checks declared for this profile"
    blocking = len(report.blocking)
    warnings = len(report.warnings)
    ok = total - blocking - warnings
    parts = [f"{total} check{'s' if total != 1 else ''}", f"{ok} ok"]
    if warnings:
        parts.append(f"{warnings} warning{'s' if warnings != 1 else ''}")
    if blocking:
        parts.append(f"{blocking} failing")
    return " - ".join(parts)


# ---------------------------------------------------------------------------
# Widget
# ---------------------------------------------------------------------------

#: Width of the title column, so every check title lines up (canvas 1h).
TITLE_COLUMN_WIDTH = 152

#: What the drawer reports as its minimum width whatever it holds. The shell
#: pins the drawer at :data:`auto_ext.ui.theme.SETUP_DRAWER_WIDTH`; this floor
#: only has to keep a long observed value from widening the window.
MIN_WIDTH = 260
MIN_HEIGHT = 200


class SetupDrawer(QWidget):
    """The Setup drawer: one row per health check, fixes written next to them."""

    #: The user pressed "Re-check". The host owns the profile and re-runs
    #: :func:`auto_ext.core.health.check_profile`.
    recheck_requested = pyqtSignal()
    #: The user pressed the close control.
    close_requested = pyqtSignal()
    #: ``(env var name, absolute path)`` the user wants pinned in the
    #: profile's ``env_overrides``. Only ever emitted for ``env.*`` checks.
    override_requested = pyqtSignal(str, str)
    #: A profile field path the user wants to edit, e.g. ``lvs_decks.dir_expr``.
    #: The drawer says WHAT to change; only the host can show WHERE, so this
    #: asks it to bring the Project screen up scrolled to that field.
    edit_field_requested = pyqtSignal(str)
    #: Emitted with text that was just put on the clipboard.
    copy_requested = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._report: PdkHealthReport | None = None
        self._rows: list[tuple[str, QWidget]] = []
        self._build_ui()
        self.set_report(None)

    # ---- public API ---------------------------------------------------

    def set_report(self, report: PdkHealthReport | None) -> None:
        """Render ``report``. ``None`` renders the "not checked yet" state."""

        self._report = report
        self._summary.setText(summary_text(report))
        self._rebuild()

    def report(self) -> PdkHealthReport | None:
        """The report currently displayed."""

        return self._report

    def summary(self) -> str:
        """The header's one-line count, as shown."""

        return self._summary.text()

    def groups(self) -> list[SetupGroup]:
        """The sections currently rendered."""

        return group_results(self._report)

    def row_ids(self) -> list[str]:
        """``check_id`` of every rendered row, in display order."""

        return [check_id for check_id, _ in self._rows]

    def row_widget(self, check_id: str) -> QWidget | None:
        """The widget rendering ``check_id``, for tests and for scrolling to it."""

        for candidate, widget in self._rows:
            if candidate == check_id:
                return widget
        return None

    def scroll_to(self, check_id: str) -> bool:
        """Bring ``check_id``'s row into view. False when it is not rendered."""

        widget = self.row_widget(check_id)
        if widget is None:
            return False
        self._scroll.ensureWidgetVisible(widget)
        return True

    def copy_text(self, text: str) -> None:
        """Put ``text`` on the clipboard and announce it."""

        from PyQt5.QtWidgets import QApplication

        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)
        self.copy_requested.emit(text)

    def can_pin_overrides(self) -> bool:
        """True when something is listening for :attr:`override_requested`.

        The drawer refuses to offer an action nobody implements; see the
        module docstring.
        """

        return self.receivers(self.override_requested) > 0

    def can_edit_fields(self) -> bool:
        """True when something is listening for :attr:`edit_field_requested`.

        Same rule as :meth:`can_pin_overrides`: a button that silently does
        nothing is worse than one that is not there.
        """

        return self.receivers(self.edit_field_requested) > 0

    # ---- construction --------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget(self._scroll)
        body.setObjectName("setupDrawerBody")
        body.setStyleSheet(
            f"QWidget#setupDrawerBody {{ background: {theme.SURFACE_PAGE}; }}"
        )
        self._body_layout = QVBoxLayout(body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(0)
        self._scroll.setWidget(body)
        root.addWidget(self._scroll, stretch=1)

        footer = QLabel(
            "Checks re-run on load, on Setup open, and before every run. The "
            "badge is the only place this lives - it is not a tab.",
            self,
        )
        footer.setWordWrap(True)
        footer.setStyleSheet(
            f"background: {theme.SURFACE_TOOLBAR}; color: {theme.TEXT_SECONDARY}; "
            f"font-size: {theme.FONT_SIZE_META}px; "
            f"border-top: 1px solid {theme.LINE_PANEL}; padding: 6px 10px;"
        )
        root.addWidget(footer)

    def _build_header(self) -> QWidget:
        header = QFrame(self)
        header.setFrameShape(QFrame.NoFrame)
        header.setStyleSheet(
            f"QFrame {{ background: {theme.SURFACE_TOOLBAR}; border: none; "
            f"border-bottom: 1px solid {theme.LINE_STRUCTURAL}; }}"
        )
        layout = QHBoxLayout(header)
        layout.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_SM, theme.SPACE_MD, theme.SPACE_SM
        )
        layout.setSpacing(theme.SPACE_MD)

        title = QLabel("Setup", header)
        title.setStyleSheet(
            f"font-size: {theme.FONT_SIZE_TITLE}px; "
            f"font-weight: {theme.FONT_WEIGHT_BOLD};"
        )
        self._summary = QLabel("", header)
        self._summary.setStyleSheet(
            f"font-family: {theme.FONT_MONO}; font-size: {theme.FONT_SIZE_META}px; "
            f"color: {theme.TEXT_SECONDARY};"
        )
        self._summary.setWordWrap(True)
        self._recheck_btn = QPushButton("Re-check", header)
        self._recheck_btn.setToolTip("Run every check again against the live shell.")
        self._recheck_btn.clicked.connect(self.recheck_requested.emit)
        self._close_btn = QPushButton(theme.STATUS_GLYPH["failed"], header)
        self._close_btn.setToolTip("Close the Setup drawer.")
        self._close_btn.clicked.connect(self.close_requested.emit)

        layout.addWidget(title)
        layout.addWidget(self._summary, stretch=1)
        layout.addWidget(self._recheck_btn)
        layout.addWidget(self._close_btn)
        return header

    # ---- rendering -----------------------------------------------------

    def _clear(self) -> None:
        self._rows = []
        layout = self._body_layout
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _rebuild(self) -> None:
        self._clear()
        groups = self.groups()
        if not groups:
            empty = QLabel(
                "No PDK health report yet. Press Re-check to run the checks "
                "against this shell.",
                self,
            )
            empty.setWordWrap(True)
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet(
                f"color: {theme.TEXT_SECONDARY}; padding: {theme.SPACE_XL}px;"
            )
            self._body_layout.addWidget(empty)
            self._body_layout.addStretch(1)
            return

        for group in groups:
            self._body_layout.addWidget(self._section_header(group.title))
            for result in group.results:
                widget = (
                    self._problem_row(result)
                    if result.status is not CheckStatus.OK
                    else self._ok_row(result)
                )
                self._rows.append((result.check_id, widget))
                self._body_layout.addWidget(widget)
        self._body_layout.addStretch(1)

    def _section_header(self, title: str) -> QWidget:
        label = QLabel(title, self)
        label.setFixedHeight(theme.TABLE_HEADER_HEIGHT)
        label.setStyleSheet(
            f"background: {theme.SURFACE_TABLE_HEADER}; color: {theme.TEXT_SECONDARY}; "
            f"font-size: {theme.FONT_SIZE_META}px; "
            f"border-bottom: 1px solid {theme.LINE_PANEL}; padding-left: 10px;"
        )
        return label

    def _ok_row(self, result: PdkCheckResult) -> QWidget:
        """A one-line row: glyph, title, what was observed, where it came from."""

        row = QFrame(self)
        row.setFrameShape(QFrame.NoFrame)
        row.setStyleSheet(
            f"QFrame {{ background: {theme.SURFACE_PAGE}; border: none; "
            f"border-bottom: 1px solid {theme.LINE_ROW}; }}"
        )
        layout = QHBoxLayout(row)
        layout.setContentsMargins(theme.SPACE_MD, theme.SPACE_XS, theme.SPACE_MD, theme.SPACE_XS)
        layout.setSpacing(theme.SPACE_SM)

        glyph, color = status_glyph(result.status)
        layout.addWidget(self._glyph_label(glyph, color, row))

        layout.addWidget(self._title_label(result, row, bold=False))

        observed = PathLabel(row)
        observed.set_placeholder(result.observed or "", reason=result.message or "")
        layout.addWidget(observed, stretch=1)

        if result.message:
            source = QLabel(result.message, row)
            source.setStyleSheet(
                f"font-size: {theme.FONT_SIZE_META}px; color: {color};"
            )
            layout.addWidget(source)
        return row

    def _problem_row(self, result: PdkCheckResult) -> QWidget:
        """A failing or warning check, with the fix written next to it."""

        blocking = result.blocking
        line = theme.STATUS_LINE["failed"] if blocking else theme.STATUS_LINE["warning"]
        fill = theme.STATUS_FILL["failed"] if blocking else theme.STATUS_FILL["warning"]

        row = QFrame(self)
        row.setFrameShape(QFrame.NoFrame)
        row.setStyleSheet(
            f"QFrame#setupProblem {{ background: {fill}; "
            f"border: none; border-top: 1px solid {line}; "
            f"border-bottom: 1px solid {line}; }}"
        )
        row.setObjectName("setupProblem")
        layout = QVBoxLayout(row)
        layout.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_SM, theme.SPACE_MD, theme.SPACE_SM
        )
        layout.setSpacing(theme.SPACE_XS + 1)

        glyph, color = status_glyph(result.status)
        head = QHBoxLayout()
        head.setSpacing(theme.SPACE_SM)
        head.addWidget(self._glyph_label(glyph, color, row))
        head.addWidget(self._title_label(result, row, bold=True))
        state = QLabel(result.message or result.status.value, row)
        state.setWordWrap(True)
        state.setStyleSheet(f"color: {color};")
        head.addWidget(state, stretch=1)
        layout.addLayout(head)

        if result.observed:
            observed = PathLabel(row)
            observed.set_placeholder(result.observed, reason="what the check saw")
            layout.addWidget(observed)

        fix = QFrame(row)
        fix.setFrameShape(QFrame.NoFrame)
        fix.setStyleSheet(
            f"QFrame {{ background: {theme.SURFACE_CARD}; "
            f"border: 1px solid {line}; }}"
        )
        fix_layout = QVBoxLayout(fix)
        fix_layout.setContentsMargins(
            theme.SPACE_SM, theme.SPACE_XS + 2, theme.SPACE_SM, theme.SPACE_XS + 2
        )
        fix_layout.setSpacing(theme.SPACE_XS)

        hint = result.fix_hint or "No fix hint was recorded for this check."
        hint_label = QLabel(hint, fix)
        hint_label.setWordWrap(True)
        hint_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        hint_label.setStyleSheet(f"font-size: {theme.FONT_SIZE_META}px;")
        fix_layout.addWidget(hint_label)

        copy_row = QHBoxLayout()
        copy_row.setSpacing(theme.SPACE_SM)
        copy_btn = QPushButton("Copy the fix", fix)
        copy_btn.setToolTip("Copy this fix hint so it can be pasted into a shell.")
        copy_btn.clicked.connect(lambda _c=False, t=hint: self.copy_text(t))
        copy_row.addWidget(copy_btn)

        # Most hints end "...in config/profiles/<id>.yaml", naming a field.
        # Until the Project screen existed that sentence was the whole answer,
        # because there was nowhere to send the user. Same rule as the pin row:
        # offered only when a host is listening, never as a dead button.
        field = field_for_check(result.check_id)
        if field is not None and self.can_edit_fields():
            edit_btn = QPushButton("Edit the field", fix)
            edit_btn.setProperty("primary", True)
            edit_btn.setToolTip(f"Open {field} on the Project screen.")
            edit_btn.clicked.connect(
                lambda _c=False, path=field: self.edit_field_requested.emit(path)
            )
            copy_row.addWidget(edit_btn)

        copy_row.addStretch(1)
        fix_layout.addLayout(copy_row)

        if group_for(result.check_id) == "env":
            fix_layout.addWidget(self._pin_row(result, fix))

        layout.addWidget(fix)
        return row

    def _pin_row(self, result: PdkCheckResult, parent: QWidget) -> QWidget:
        """Canvas 1h's second fix: pin the value instead of sourcing a script."""

        row = QWidget(parent)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.SPACE_SM)

        var = self._env_var_name(result)
        edit = QLineEdit(row)
        edit.setPlaceholderText("/abs/path/to/value")
        edit.setStyleSheet(f"font-family: {theme.FONT_MONO};")
        browse = QPushButton("Browse", row)
        pin = QPushButton("Set", row)
        pin.setProperty("primary", True)

        available = self.can_pin_overrides() and bool(var)
        for widget in (edit, browse, pin):
            widget.setEnabled(available)
        if not available:
            reason = (
                "Nothing is connected to override_requested, so pinning a value "
                "here would write nowhere. Use the fix above."
                if bool(var)
                else "This check does not name an environment variable to pin."
            )
            for widget in (edit, browse, pin):
                widget.setToolTip(reason)
        else:
            edit.setToolTip(f"Pin {var} in the profile's env_overrides.")
            browse.clicked.connect(lambda _c=False, e=edit: self._browse_into(e))
            pin.clicked.connect(
                lambda _c=False, v=var, e=edit: self._emit_override(v, e)
            )

        layout.addWidget(edit, stretch=1)
        layout.addWidget(browse)
        layout.addWidget(pin)
        return row

    def _title_label(
        self, result: PdkCheckResult, parent: QWidget, *, bold: bool
    ) -> QLabel:
        """The fixed-width name column, elided rather than clipped."""

        label = QLabel(parent)
        label.setFixedWidth(TITLE_COLUMN_WIDTH)
        weight = theme.FONT_WEIGHT_BOLD if bold else theme.FONT_WEIGHT_NORMAL
        label.setStyleSheet(
            f"font-family: {theme.FONT_MONO}; font-size: {theme.FONT_SIZE_MONO}px; "
            f"font-weight: {weight};"
        )
        text = row_label(result)
        label.setText(
            label.fontMetrics().elidedText(text, Qt.ElideRight, TITLE_COLUMN_WIDTH)
        )
        label.setToolTip(
            f"{result.title or result.check_id}  ({result.check_id})"
        )
        return label

    @staticmethod
    def _env_var_name(result: PdkCheckResult) -> str:
        """The variable an ``env.*`` check is about.

        ``check_id`` lowercases the name (slugs must), so the display title --
        ``"Environment variable PDK_LAYER_MAP_FILE"`` -- is the only place the
        real, case-sensitive name survives into the report. Its last
        whitespace-separated token is that name.
        """

        return row_label(result) if group_for(result.check_id) == "env" else ""

    def _browse_into(self, edit: QLineEdit) -> None:
        start = edit.text().strip() or str(Path.home())
        chosen, _ = QFileDialog.getOpenFileName(self, "Choose a file to pin", start)
        if chosen:
            edit.setText(chosen)

    def _emit_override(self, var: str, edit: QLineEdit) -> None:
        value = edit.text().strip()
        if not var or not value:
            return
        self.override_requested.emit(var, value)

    def _glyph_label(self, glyph: str, color: str, parent: QWidget) -> QLabel:
        label = QLabel(glyph, parent)
        label.setFixedWidth(12)
        label.setStyleSheet(
            f"color: {color}; font-weight: {theme.FONT_WEIGHT_BOLD};"
        )
        return label

    # ---- layout --------------------------------------------------------

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        """Small and constant: the drawer scrolls, it does not widen the window."""

        hint = super().minimumSizeHint()
        return QSize(min(hint.width(), MIN_WIDTH), min(hint.height(), MIN_HEIGHT))

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        return QSize(theme.SETUP_DRAWER_WIDTH, 480)
