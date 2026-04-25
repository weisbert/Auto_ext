"""Reusable Qt widgets for the GUI tabs.

Lives under ``auto_ext.ui`` so the lazy-import rule (no PyQt5 at package
import time for CLI-only hosts) still holds — tabs pull widgets in only
when they themselves are constructed.
"""
