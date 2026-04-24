"""PyQt5 GUI layer.

Imports Qt lazily inside submodules so ``import auto_ext`` stays cheap
on hosts without PyQt5 installed (servers during headless CI runs,
etc.). Callers should enter via :func:`auto_ext.ui.app.run_gui`.
"""
