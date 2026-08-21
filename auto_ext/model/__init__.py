"""Persisted object model for Auto_ext.

Home of the pydantic v2 objects described in ``docs/refactor/01-schema.md``.
S1 lands the Run layer only (:mod:`auto_ext.model.run`); ``recipe`` / ``pdk``
/ ``cells`` / ``common`` arrive in S2 and take over the stand-in types that
:mod:`auto_ext.model.run` defines for now.

Import direction is one-way: ``auto_ext.model`` may import leaf modules of
``auto_ext.core`` (``errors``, ``progress``, ``checks``), never
``auto_ext.core.runner`` -- the runner imports the model, not the reverse.
"""

from __future__ import annotations

from auto_ext.model.run import (
    MAX_SAME_SECOND_RUNS,
    RUN_SCHEMA_VERSION,
    RUN_TIMESTAMP_FORMAT,
    Base,
    DutSnapshot,
    EnvBinding,
    Frozen,
    JivaroSnapshot,
    JsonScalar,
    LvsResult,
    RecipeSnapshot,
    RunAnnotations,
    RunBatch,
    RunIdError,
    RunPaths,
    RunRecord,
    RunResults,
    StageRecord,
    StageStatus,
    TaskStatus,
    allocate_run_dir,
    make_run_slug,
    parse_run_id,
    run_paths,
    slugify,
    utcnow,
    validate_run_slug,
)

__all__ = [
    "MAX_SAME_SECOND_RUNS",
    "RUN_SCHEMA_VERSION",
    "RUN_TIMESTAMP_FORMAT",
    "Base",
    "DutSnapshot",
    "EnvBinding",
    "Frozen",
    "JivaroSnapshot",
    "JsonScalar",
    "LvsResult",
    "RecipeSnapshot",
    "RunAnnotations",
    "RunBatch",
    "RunIdError",
    "RunPaths",
    "RunRecord",
    "RunResults",
    "StageRecord",
    "StageStatus",
    "TaskStatus",
    "allocate_run_dir",
    "make_run_slug",
    "parse_run_id",
    "run_paths",
    "slugify",
    "utcnow",
    "validate_run_slug",
]
