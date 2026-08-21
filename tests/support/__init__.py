"""Importable helpers shared by the test suite.

Fixtures live in ``conftest.py``; anything a test needs to call at module
scope (a builder used inside a ``parametrize`` list, a constant compared
against) lives here instead, because a fixture cannot be reached from module
scope. ``pyproject.toml`` puts the repo root on ``pythonpath``, so
``from tests.support.v2 import make_profile`` works without an install.
"""
