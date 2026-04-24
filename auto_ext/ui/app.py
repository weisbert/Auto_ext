"""GUI entry point.

Invoked from :func:`auto_ext.cli.gui`. Creates the :class:`QApplication`,
builds the :class:`MainWindow` with whatever preload paths the CLI
passed, and enters the Qt event loop.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt5.QtWidgets import QApplication

from auto_ext.ui.main_window import MainWindow


def run_gui(
    *,
    config_dir: Path | None = None,
    auto_ext_root: Path | None = None,
    workarea: Path | None = None,
    argv: list[str] | None = None,
) -> int:
    """Launch the GUI and block until the window closes. Returns the Qt exit code.

    ``argv`` defaults to ``sys.argv`` so Qt can pull its own
    platform-plugin flags; callers that want a headless app (e.g.
    pytest-qt) should pass ``argv=[]`` and manage QApplication manually
    instead of calling this.
    """
    app = QApplication.instance()
    if app is None:
        app = QApplication(argv if argv is not None else sys.argv)

    window = MainWindow(
        config_dir=config_dir,
        auto_ext_root=auto_ext_root,
        workarea=workarea,
    )
    window.show()
    return app.exec_()
