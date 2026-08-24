"""Fixtures shared by the GUI tests.

The v2 documents themselves come from ``tests/conftest.py`` and
``tests/support/v2.py``; what lives here is the GUI's view of them -- a loaded
:class:`~auto_ext.ui.config_controller.ConfigController` and an isolated
recipe search path.

Why the search path has to be isolated
--------------------------------------

:func:`auto_ext.ui.config_controller.recipe_search_path` walks
``$AUTO_EXT_RECIPES`` and ``~/.auto_ext/recipes`` before it reaches the
project. On a developer's own machine either can hold recipes, so a test that
asserted "this project has one recipe" would pass or fail depending on whose
home directory it ran in. :func:`isolated_recipe_path` points both at empty
directories inside ``tmp_path``, which is also what keeps a test that *writes*
a recipe from writing into the developer's home.
"""

from __future__ import annotations

import gc
from pathlib import Path

import pytest

pytest.importorskip("PyQt5")


@pytest.fixture(autouse=True)
def _drain_qt_events():
    """Finish Qt's pending work before the test's widgets are collected.

    Every GUI test here builds a parentless top-level widget. PyQt gives
    Python ownership of a parentless QObject, so the C++ widget is destroyed
    the moment the last Python reference goes -- which is *after* the test
    body, while a repaint the test triggered may still be sitting in the
    queue. The next test's ``qtbot`` then pumps that queue, Qt paints into
    freed memory, and the process aborts. It is not a flake: it reproduces on
    the same test every run once enough work has accumulated ahead of it, and
    it takes the whole session down rather than failing one test.

    Draining here -- deliver posted events, run the deferred deletes, then
    collect -- means nothing is queued against a widget that is about to
    disappear. Autouse and directory-wide because the pattern (parentless
    widget + ``qtbot.addWidget``) is in every GUI test file, not one of them.
    """

    yield

    from PyQt5.QtCore import QEvent
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return

    app.processEvents()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    gc.collect()
    app.processEvents()
    app.sendPostedEvents(None, QEvent.DeferredDelete)


@pytest.fixture
def isolated_recipe_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Empty ``$AUTO_EXT_RECIPES`` and ``~``; returns the fake home."""

    from auto_ext.ui.config_controller import RECIPES_ENV_VAR

    home = tmp_path / "home"
    (home / ".auto_ext" / "recipes").mkdir(parents=True)
    monkeypatch.delenv(RECIPES_ENV_VAR, raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


@pytest.fixture
def loaded_controller(
    v2_config_dir: Path, isolated_recipe_path: Path, qtbot
):
    """A :class:`ConfigController` with ``v2_config_dir`` already loaded."""

    from auto_ext.ui.config_controller import ConfigController

    controller = ConfigController(auto_ext_root=v2_config_dir)
    errors: list[str] = []
    controller.config_error.connect(errors.append)
    controller.load(v2_config_dir / "config")
    assert errors == [], errors
    return controller
