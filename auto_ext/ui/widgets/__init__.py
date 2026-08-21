"""Reusable Qt widgets the screens compose.

A *widget* here is a piece with no nav-rail item of its own -- a run bar, an
option editor, a log tailer, a modal dialog. Whole content areas live in
:mod:`auto_ext.ui.screens`.

Lives under ``auto_ext.ui`` so the lazy-import rule (no PyQt5 at package
import time for CLI-only hosts) still holds: a screen pulls a widget in only
when it is itself constructed.
"""
