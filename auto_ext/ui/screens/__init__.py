"""Full-window screens mounted into :class:`~auto_ext.ui.shell.Shell`.

A *screen* is what a nav-rail item shows: it owns a whole content area, a
toolbar of its own, and its own idea of what "empty" looks like. That is
the difference from :mod:`auto_ext.ui.widgets`, which holds pieces a screen
composes.

Qt is imported inside the submodules, never here, so ``import auto_ext``
stays cheap on a host with no PyQt5 (the rule the rest of
:mod:`auto_ext.ui` follows).
"""
