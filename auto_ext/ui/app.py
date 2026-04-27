"""GUI entry point.

Invoked from :func:`auto_ext.cli.gui`. Creates the :class:`QApplication`,
builds the :class:`MainWindow` with whatever preload paths the CLI
passed, and enters the Qt event loop.

Persists the last successfully-loaded ``config_dir`` via
:class:`QSettings` so the next launch auto-loads it (skip with
``--config-dir`` to override or ``--no-remember-config`` to disable).
The settings file lives at the platform's default user-scope location
(``~/.config/Auto_ext/Auto_ext.conf`` on Linux,
``HKCU\\Software\\Auto_ext\\Auto_ext`` on Windows). The path stored is
the absolute config_dir; staleness (dir moved / deleted) is detected
at read time and the entry silently ignored.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PyQt5.QtCore import QSettings
from PyQt5.QtWidgets import QApplication

from auto_ext.ui.main_window import MainWindow

logger = logging.getLogger(__name__)


_QSETTINGS_ORG = "Auto_ext"
_QSETTINGS_APP = "Auto_ext"
_LAST_CONFIG_KEY = "last_config_dir"


def run_gui(
    *,
    config_dir: Path | None = None,
    auto_ext_root: Path | None = None,
    workarea: Path | None = None,
    argv: list[str] | None = None,
    remember_config: bool = True,
) -> int:
    """Launch the GUI and block until the window closes. Returns the Qt exit code.

    ``argv`` defaults to ``sys.argv`` so Qt can pull its own
    platform-plugin flags; callers that want a headless app (e.g.
    pytest-qt) should pass ``argv=[]`` and manage QApplication manually
    instead of calling this.

    ``remember_config`` (default True) reads + writes the last loaded
    config_dir via :class:`QSettings`. When ``config_dir`` is None and
    QSettings has a valid prior entry, the GUI auto-loads it. Pass
    False to skip both directions (one-shot launch / scripting / tests).
    """
    app = QApplication.instance()
    if app is None:
        app = QApplication(argv if argv is not None else sys.argv)
    # Set org/app name BEFORE constructing QSettings so it picks up the
    # right scope. Setting these is idempotent — re-runs don't fight.
    app.setOrganizationName(_QSETTINGS_ORG)
    app.setApplicationName(_QSETTINGS_APP)

    if config_dir is None and remember_config:
        config_dir = _read_last_config_dir()

    window = MainWindow(
        config_dir=config_dir,
        auto_ext_root=auto_ext_root,
        workarea=workarea,
    )

    if remember_config:
        # Persist every successful load — covers init-wizard accept,
        # "Open existing" file dialog, and the auto-load above.
        window._controller.config_loaded.connect(_write_last_config_dir)

    window.show()
    return app.exec_()


def _read_last_config_dir() -> Path | None:
    """Read the persisted last-loaded config_dir, validating it exists.

    Returns ``None`` when no entry exists, the entry is malformed, the
    directory has been moved/deleted, or the directory no longer
    contains a ``project.yaml``. The check is intentionally permissive:
    a stale entry should never fail the launch, just fall through to
    the empty-state banner.
    """
    settings = QSettings()
    raw = settings.value(_LAST_CONFIG_KEY, "")
    if not raw or not isinstance(raw, str):
        return None
    candidate = Path(raw)
    if not candidate.is_dir() or not (candidate / "project.yaml").is_file():
        logger.info(
            "QSettings.last_config_dir=%r is no longer valid; ignoring", raw
        )
        return None
    return candidate


def _write_last_config_dir(config_dir: object) -> None:
    """Persist ``config_dir`` (resolved absolute) into QSettings.

    Connected to :attr:`ConfigController.config_loaded`, whose payload
    is ``object`` (since it can fan out from multiple call sites). The
    cast to ``Path`` is defensive — the controller emits a Path today,
    but the signal type is loose.
    """
    if config_dir is None:
        return
    try:
        path = Path(config_dir).resolve()
    except (TypeError, OSError) as exc:
        logger.warning("could not resolve config_dir for QSettings: %r (%s)", config_dir, exc)
        return
    settings = QSettings()
    settings.setValue(_LAST_CONFIG_KEY, str(path))
