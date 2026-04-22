"""Load and validate ``project.yaml`` + ``tasks.yaml``.

Implementation lands in Phase 2. Uses ``ruamel.yaml`` (comment-preserving
roundtrip) + ``pydantic`` v2 schemas. List-valued task fields are
auto-expanded to subtasks at load time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def load_project(path: Path) -> Any:
    """Load and validate ``project.yaml``. Phase 2."""

    raise NotImplementedError


def load_tasks(path: Path) -> Any:
    """Load and validate ``tasks.yaml``, expanding list-valued fields. Phase 2."""

    raise NotImplementedError
