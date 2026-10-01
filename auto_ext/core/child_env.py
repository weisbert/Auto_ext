"""The environment an external process started by Auto_ext should see.

``run.sh`` changes three variables for *Auto_ext's own interpreter*:

``PYTHONPATH``
    gains the install root and ``<install>/_vendor`` -- the offline
    dependencies (jinja2, pydantic built for cp311, setuptools, packaging,
    typing_extensions, click, ...).
``PYTHONSAFEPATH``
    set to 1, so a same-named ``auto_ext/`` in the workarea cannot shadow the
    package.
``LD_LIBRARY_PATH``
    gains PyQt5's bundled Qt5 for ``gui`` / ``test``, to get past the CentOS 7
    libstdc++.

Every one of those is wrong for a child. si / strmout / calibre / qrc /
jivaro, Calibre Interactive (itself a Qt application) and xdg-open inherit the
environment, and any Python they start -- the system 2.7 or 3.6, an EDA tool's
embedded interpreter, someone's venv -- would put Auto_ext's ``_vendor`` ahead
of its own site-packages, or load Auto_ext's Qt instead of its own.

So ``run.sh`` records what the caller had before it changed anything, as
``AUTO_EXT_CALLER_<VAR>`` (the value) and ``AUTO_EXT_CALLER_<VAR>_SET``
(``1`` if the variable was set at all, ``0`` if it was unset), and
:func:`child_env` puts that back. Every place Auto_ext starts an external
process builds its environment through it.

Without those markers -- the Windows dev venv, the test suite, a direct
``python -m auto_ext`` -- :func:`child_env` returns the environment unchanged,
exactly as before. With them, it also drops the markers, so it is idempotent:
applying it to an environment it already produced changes nothing, and in
particular does not undo a deliberate value (a profile's ``env_overrides``
setting ``PYTHONPATH``, say) laid on top afterwards.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

__all__ = ["CALLER_PREFIX", "RESTORED_VARS", "child_env", "inherited_child_env"]

#: Variables ``run.sh`` changes for Auto_ext's own interpreter only. Keep in
#: step with the ``AUTO_EXT_CALLER_*`` block in ``run.sh``;
#: ``tests/test_run_sh.py`` checks both name the same three.
RESTORED_VARS: tuple[str, ...] = ("PYTHONPATH", "PYTHONSAFEPATH", "LD_LIBRARY_PATH")

CALLER_PREFIX = "AUTO_EXT_CALLER_"


def child_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """A copy of ``base`` (default :data:`os.environ`) fit for a child process.

    For each of :data:`RESTORED_VARS` that ``run.sh`` recorded, the caller's
    original value is restored, or the variable removed if the caller had it
    unset. The ``AUTO_EXT_CALLER_*`` markers themselves are dropped. A
    variable with no marker is left exactly as it is.
    """

    env = dict(os.environ if base is None else base)
    for var in RESTORED_VARS:
        was_set = env.pop(f"{CALLER_PREFIX}{var}_SET", None)
        value = env.pop(f"{CALLER_PREFIX}{var}", None)
        if was_set is None:
            continue
        if was_set == "1":
            env[var] = value or ""
        else:
            env.pop(var, None)
    return env


def inherited_child_env() -> dict[str, str] | None:
    """``env=`` for a child that would otherwise just inherit ``os.environ``.

    ``None`` -- "inherit", exactly what such a call site passed before -- when
    ``run.sh`` left no markers; :func:`child_env` of ``os.environ`` when it did.
    """

    if not any(key.startswith(CALLER_PREFIX) for key in os.environ):
        return None
    return child_env()
