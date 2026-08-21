"""Path-traversal and subprocess-injection safety audit.

Auto_ext runs on a multi-user shared filesystem and ingests strings from
``project.yaml`` / ``tasks.yaml`` / GUI fields. These tests lock the
current safety posture so a future refactor can't silently regress it,
and they document the deliberate trade-offs (e.g. ``dspf_out_path``
treats ``..`` as the user's responsibility).

Audit summary (verified by the tests below):

- :func:`auto_ext.tools.base.run_subprocess` always uses argv list form
  via :class:`subprocess.Popen`; no ``shell=True`` anywhere in the
  codebase. Cell / library / ground_net values flow into argv as
  individual list entries, so shell metacharacters are inert.
- :func:`auto_ext.core.env.substitute_env` is pure ``str.replace`` /
  regex sub; values from ``env_overrides`` are interpolated as literals,
  never evaluated.
- **No user-supplied string reaches a template path any more.** The four
  ``project.yaml`` template slots that this audit used to guard are gone;
  which file backs a render target is catalog state, derived from the
  package's own ``__file__``. What a user still writes is a
  :attr:`~auto_ext.core.patch_models.TemplatePatch.template_id`, and that
  is a *lookup key* compared against catalog target ids -- never joined
  onto a directory.
- The new attack surface is the **run slug**: it comes from a cell name or
  a user's note and is spelled into a directory name.
  :func:`~auto_ext.model.run.allocate_run_dir` refuses a slug that could
  leave ``runs/``.
- ``dspf_out_pattern`` / ``output_dir_pattern`` / ``intermediate_dir`` on
  :class:`~auto_ext.model.workspace.WorkspaceConfig` intentionally accept
  ``..`` (legitimate uses like ``${WORK_ROOT}/../shared``); foot-gun left
  to the user. Documented via :func:`pytest.xfail`.
"""

from __future__ import annotations

import inspect
import subprocess
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from auto_ext.core import render as render_module
from auto_ext.core.config import JivaroConfig, ProjectConfig, TaskConfig
from auto_ext.core.env import substitute_env
from auto_ext.tools import base as tools_base
from auto_ext.tools.calibre import CalibreTool
from auto_ext.tools.jivaro import JivaroTool
from auto_ext.tools.quantus import QuantusTool
from auto_ext.tools.si import SiTool
from auto_ext.tools.strmout import StrmoutTool


# ---- helpers ---------------------------------------------------------------


def _fingerprint() -> Any:
    """A minimal valid :class:`~auto_ext.core.patch_models.BaseFingerprint`.

    The digests are not checked against anything here; the patch model only
    requires that they are real 64-hex strings, and this audit is about the
    ``template_id`` beside them.
    """

    from datetime import datetime, timezone

    from auto_ext.core.patch_models import BaseFingerprint

    return BaseFingerprint(
        template_sha256="a" * 64,
        masked_sha256="b" * 64,
        captured_at=datetime(2026, 8, 21, 14, 32, 5, tzinfo=timezone.utc),
    )


def _path_safety_make_task(**overrides: Any) -> TaskConfig:
    """Build a :class:`TaskConfig` with the given fields overridden.

    Inline so this test file stays self-contained per scope rules.
    """
    defaults: dict[str, Any] = {
        "task_id": "lib__cell__layout__schematic",
        "library": "lib",
        "cell": "cell",
        "lvs_source_view": "schematic",
        "lvs_layout_view": "layout",
        "ground_net": "vss",
        "out_file": None,
        "jivaro": JivaroConfig(),
        "continue_on_lvs_fail": False,
        "spec_index": 0,
        "expansion_index": 0,
    }
    defaults.update(overrides)
    return TaskConfig(**defaults)


# ---- subprocess shape audit -----------------------------------------------


def test_path_safety_run_subprocess_uses_list_form_not_shell() -> None:
    """``run_subprocess`` calls Popen with ``argv`` as the first positional
    arg (a list) and never sets ``shell=True``.

    Audit method: read the function source. Cheaper and more tamper-proof
    than mocking Popen — if someone changes the call site, the source
    inspection trips immediately.
    """
    src = inspect.getsource(tools_base.run_subprocess)
    assert "subprocess.Popen(" in src
    assert "shell=True" not in src
    assert "shell=False" not in src or "shell=True" not in src
    # The call site explicitly passes resolved_argv as the first positional
    # (list) argument. Check that ``shell=`` is not present at all so the
    # default (False) applies; defense in depth against a future edit.
    assert "shell=" not in src


def test_path_safety_no_shell_true_anywhere_in_production() -> None:
    """Belt-and-suspenders: walk every ``auto_ext/*.py`` source file and
    confirm no production module passes ``shell=True``."""
    repo_root = Path(__file__).resolve().parent.parent
    auto_ext_dir = repo_root / "auto_ext"
    offenders: list[Path] = []
    for py in auto_ext_dir.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        if "shell=True" in text:
            offenders.append(py)
    assert not offenders, f"shell=True found in: {offenders}"


def test_path_safety_run_subprocess_signature_is_argv_list() -> None:
    """Lock the public signature: first positional must be named ``argv``
    (typed ``list[str]`` per the source). Renaming would suggest a refactor
    that may have changed the call shape; this test forces a review."""
    sig = inspect.signature(tools_base.run_subprocess)
    params = list(sig.parameters.values())
    assert params[0].name == "argv"
    # The annotation can be the typing form ``list[str]`` (3.9+) or a
    # string under PEP 563. Either way, "list" should appear.
    annotation = str(params[0].annotation)
    assert "list" in annotation.lower()


# ---- shell-metachar cell names → list-form argv ----------------------------


@pytest.mark.parametrize(
    "cell_name",
    [
        "foo;bar",          # shell sequence
        "$(whoami)",        # shell command substitution
        "foo bar",          # whitespace would split if run via shell
        "foo'bar",          # quote — would mangle a string-cmd
        "`id`",             # backtick command substitution
        "foo|bar",          # pipe
        "foo&bar",          # background
        "foo>out",          # redirection
    ],
)
def test_path_safety_strmout_argv_keeps_cell_as_single_token(cell_name: str) -> None:
    """``StrmoutTool.build_argv`` puts cell straight into argv[index]
    without quoting / splitting. With list-form Popen, the OS receives
    one argv element regardless of metacharacters."""
    tool = StrmoutTool()
    ctx = {
        "library": "lib",
        "cell": cell_name,
        "lvs_layout_view": "layout",
        "output_dir": "/tmp/out",
        "layer_map": "/pdk/layer.map",
    }
    argv = tool.build_argv(Path("/unused"), ctx)
    # ``-topCell <cell>`` carries the cell name as exactly one argv
    # element, never split on whitespace or metacharacters.
    assert "-topCell" in argv
    top_cell_value = argv[argv.index("-topCell") + 1]
    assert top_cell_value == cell_name
    # The strmFile path embeds the cell name verbatim as a substring;
    # metacharacters are part of the filename string, not exec'd as
    # shell. (We tolerate native path separators around it.)
    strm_value = argv[argv.index("-strmFile") + 1]
    assert cell_name in strm_value
    # Argv is a list of plain strings — no shell parsing happens.
    assert all(isinstance(a, str) for a in argv)


@pytest.mark.parametrize(
    "tool_cls,expected_argv_contains",
    [
        (CalibreTool, "-runset"),
        (QuantusTool, "-cmd"),
        (JivaroTool, "-xml"),
        (SiTool, "-batch"),
    ],
)
def test_path_safety_tool_argvs_are_list_form(
    tool_cls: type, expected_argv_contains: str
) -> None:
    """Every tool's ``build_argv`` returns a list of strings — never a
    single space-joined command string that would invite shell parsing."""
    tool = tool_cls()
    argv = tool.build_argv(
        Path("/some/rendered/file"),
        {
            "library": "lib",
            "cell": "cell",
            "lvs_layout_view": "layout",
            "output_dir": "/tmp/out",
            "layer_map": "/pdk/layer.map",
        },
    )
    assert isinstance(argv, list)
    assert all(isinstance(a, str) for a in argv)
    assert expected_argv_contains in argv


# ---- env_overrides values: substitute_env is literal replacement ----------


def test_path_safety_substitute_env_is_literal_replacement() -> None:
    """A value like ``$(rm -rf /)`` substituted through ``substitute_env``
    must come out verbatim — no shell expansion, no recursion into the
    replacement string."""
    text = "value=$X"
    overrides = {"X": "$(rm -rf /)"}
    result = substitute_env(text, overrides)
    assert result == "value=$(rm -rf /)"


def test_path_safety_substitute_env_no_recursion_into_replacement() -> None:
    """If a replacement value itself contains ``$Y``, the second pass must
    NOT expand it. Otherwise an attacker who controls one env var could
    reach into a separately-set var via crafted layering."""
    text = "$X"
    overrides = {"X": "$Y", "Y": "secret"}
    result = substitute_env(text, overrides)
    # Phase-1 expansion only: ``$X -> $Y``. ``$Y`` is left untouched.
    # (substitute_env's regex matches the original text once; the regex
    # replacement string is opaque to subsequent passes within the
    # same call.)
    assert result == "$Y"


def test_path_safety_substitute_env_handles_metacharacters() -> None:
    """Backticks, semicolons, quotes in env-var values are inert — the
    substitution is text-level, not eval-level."""
    text = "echo ${MSG}"
    overrides = {"MSG": "`whoami`; rm -rf /; \"x\""}
    result = substitute_env(text, overrides)
    assert result == "echo `whoami`; rm -rf /; \"x\""


# ---- the template path is no longer user-supplied at all ------------------


def test_path_safety_project_config_names_no_template_path() -> None:
    """The four ``templates.*`` slots this audit used to guard are gone.

    They were the only place a user string became a path that Auto_ext then
    opened and executed as a Jinja template. Deleting the field deletes the
    whole class of finding -- so the audit asserts the field's *absence*
    rather than keeping validators for a shape nobody can write any more.
    A project.yaml that still carries the key is refused by name.
    """

    assert "templates" not in ProjectConfig.model_fields
    assert not [
        name
        for name in ProjectConfig.model_fields
        if "template" in name
    ]


def test_path_safety_a_v1_template_binding_is_refused_by_load_project(
    tmp_path: Path,
) -> None:
    """End-to-end: ``templates.calibre: ../../etc/passwd`` still cannot load.

    The reason changed -- the key is retired rather than the value being
    validated -- but the traversal string must not reach a file open either
    way, and the error has to name the migration.
    """

    from auto_ext.core.config import load_project
    from auto_ext.core.errors import ConfigError

    p = tmp_path / "project.yaml"
    p.write_text(
        "work_root: /tmp\n"
        "templates:\n"
        "  calibre: ../../etc/passwd\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="templates") as excinfo:
        load_project(p)
    assert "migrate" in str(excinfo.value)


def test_path_safety_catalog_template_paths_come_from_the_package() -> None:
    """Every render target's template resolves under the package's own root.

    ``spec.template_path`` is derived from ``__file__``, so no configuration
    -- v1 or v2 -- can point a render target outside the checkout.
    """

    from auto_ext.catalog import builtin_catalog

    import auto_ext

    package_root = Path(auto_ext.__file__).resolve().parent.parent
    targets = builtin_catalog().targets
    assert targets, "the catalog must declare at least one render target"
    for spec in targets:
        resolved = spec.template_path.resolve()
        assert resolved.is_relative_to(package_root), spec.template_id
        assert ".." not in spec.template.split("/")


@pytest.mark.parametrize(
    "bad_id",
    [
        "../../etc/passwd",
        "quantus/../../etc/passwd",
        "../quantus/ext.cmd",
        "/etc/passwd",
        "quantus/./x",
        "quantus\\..\\x",
    ],
)
def test_path_safety_patch_template_id_refuses_a_path(bad_id: str) -> None:
    """A patch's ``template_id`` is the one template reference a user writes.

    Its pattern allows exactly ``<stage>/<file>``: one separator, no
    backslash, no ``.`` or ``..`` segment.
    """

    from auto_ext.core.patch_models import TemplatePatch

    with pytest.raises(ValidationError):
        TemplatePatch(stage="quantus", template_id=bad_id, base=_fingerprint())


def test_path_safety_patch_template_id_is_a_key_not_a_path() -> None:
    """``quantus/..`` satisfies the pattern -- and is inert, because the id is
    only ever *compared* against catalog target ids.

    Nothing joins it onto a directory (:mod:`auto_ext.core.render` looks the
    patch up with ``recipe.patch_for(stage, spec.template_id)``), so the worst
    a crafted id achieves is a patch that matches no target and is never
    applied. This test is what makes that reasoning falsifiable: if a future
    change starts resolving the id as a path, the second assertion is where
    the audit notices.
    """

    from auto_ext.catalog import builtin_catalog
    from auto_ext.core.patch_models import TemplatePatch

    odd = TemplatePatch(stage="quantus", template_id="quantus/..", base=_fingerprint())
    known = {spec.template_id for spec in builtin_catalog().targets}
    assert odd.template_id not in known

    source = inspect.getsource(render_module)
    assert "templates_root / patch" not in source
    assert "/ patch.template_id" not in source


# ---- the new surface: a run slug becomes a directory name ------------------


@pytest.mark.parametrize(
    "hostile_slug",
    [
        "../../etc",
        "..",
        "a/../../b",
        "/abs",
        "C:\\windows",
        "a\\b",
        "$(whoami)",
        "`id`",
        "foo;bar",
        "with:colon",
    ],
)
def test_path_safety_run_slug_never_leaves_the_runs_root(
    runs_root: Path, frozen_clock: Any, hostile_slug: str
) -> None:
    """A slug comes from a cell name or a user note and is spelled into a path.

    Either the allocator refuses it outright or it sanitises it, but the
    directory it returns is always a direct child of ``runs/`` whose name
    carries no separator, no ``..`` and no colon (NTFS refuses the last one,
    and the development machine is Windows).
    """

    from auto_ext.core.errors import AutoExtError
    from auto_ext.model.run import allocate_run_dir, slugify

    try:
        created = allocate_run_dir(runs_root, hostile_slug)
    except (AutoExtError, ValueError):
        # Refusing is a valid answer; what matters is that nothing was made.
        assert list(runs_root.iterdir()) == []
        return

    assert created.parent == runs_root
    assert created.resolve().is_relative_to(runs_root.resolve())
    for forbidden in ("..", "/", "\\", ":"):
        assert forbidden not in created.name
    # ...and the sanitiser, not luck, is what did it.
    assert slugify(hostile_slug) in created.name


# ---- shell-metachar cell name flows into render context as literal --------


def test_path_safety_cell_with_shell_metachars_in_format_string() -> None:
    """A cell name with shell metachars survives ``str.format`` in
    ``dspf_out_path`` resolution as a literal substring (no eval)."""
    from auto_ext.core.runner import resolve_dspf_path

    raw = "/out/{cell}.dspf"
    text, err = resolve_dspf_path(
        raw,
        extended_env={},
        cell="$(whoami);ls",
        library="lib",
        task_id="t",
    )
    # The metacharacters are embedded into the path string verbatim.
    assert text == "/out/$(whoami);ls.dspf"
    assert err is None


# ---- cell name with shell metachars feeds Popen as one argv element -------


def test_path_safety_cell_metachars_passed_to_subprocess_as_single_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end shape check: a cell name with ``;`` ends up as exactly
    one argv element in the captured Popen call. We monkeypatch ``Popen``
    rather than execute, so the test is OS-agnostic."""
    captured: dict[str, Any] = {}

    class _FakeProc:
        def __init__(self) -> None:
            self.stdout = None
            self.pid = 1234

        def wait(self, timeout: float | None = None) -> int:
            return 0

    def _fake_popen(argv: list[str], **kwargs: Any) -> _FakeProc:
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        # Match the real shape so run_subprocess can drain.
        proc = _FakeProc()
        # Fake an empty pipe (immediately at EOF).
        import io as _io
        proc.stdout = _io.StringIO("")  # type: ignore[assignment]
        return proc  # type: ignore[return-value]

    monkeypatch.setattr(tools_base.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(
        tools_base.shutil,
        "which",
        lambda exe, path=None: "/fake/bin/" + exe,
    )

    log_path = tmp_path / "log.txt"
    argv = ["strmout", "-topCell", "foo;rm -rf /", "-strmFile", "/o/foo.db"]
    rc = tools_base.run_subprocess(
        argv, cwd=tmp_path, env={"PATH": "/usr/bin"}, log_path=log_path
    )
    assert rc == 0
    # The injected metacharacter survives as one element. Importantly,
    # ``shell`` is NOT among the kwargs (so default False applies).
    assert "foo;rm -rf /" in captured["argv"]
    assert captured["argv"].count("foo;rm -rf /") == 1
    assert "shell" not in captured["kwargs"]


# ---- documented foot-guns: dspf_out_path / output_dir traversal ----------


@pytest.mark.xfail(
    reason=(
        "dspf_out_pattern / output_dir_pattern / intermediate_dir "
        "intentionally accept '..' to support legitimate cross-tree "
        "outputs (e.g. ${WORK_ROOT}/../shared/dspf). Path traversal in "
        "these fields is the user's responsibility on a shared FS, "
        "matching the rest of the EDA toolchain. If a future security "
        "review wants to sandbox these, this xfail flips to a real "
        "validator + assertion."
    ),
    strict=True,
)
def test_path_safety_workspace_patterns_reject_traversal(tmp_path: Path) -> None:
    """Documented as out-of-scope, on the field's new home.

    ``WorkspaceConfig`` does validate these three patterns -- it refuses an
    unknown or retired ``{format_key}`` -- so the absence of a ``..`` check is
    a decision, not an oversight, and this is where it is recorded.
    """
    from auto_ext.core.errors import ConfigError
    from auto_ext.model.workspace import load_workspace

    p = tmp_path / "workspace.yaml"
    p.write_text(
        "pdk_profile: hn001\n"
        "dspf_out_pattern: ../../etc/passwd\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"\.\."):
        load_workspace(p)


# ---- env_overrides values flow into rendered files literally -------------


def test_path_safety_env_overrides_value_with_metachars_renders_literally(
    tmp_path: Path,
) -> None:
    """A user who pastes ``$(whoami)`` into ``env_overrides.X`` will see
    that exact string in the rendered template — the renderer does NOT
    evaluate it. (The downstream EDA tool may or may not interpret it
    when it later reads the rendered file; that's the tool's contract,
    not ours.)"""
    from auto_ext.core.template import render_template

    tpl = tmp_path / "t.j2"
    tpl.write_text("path=$X", encoding="utf-8")
    out = render_template(
        tpl,
        context={},
        env={"X": "$(rm -rf /); echo pwn"},
    )
    assert out == "path=$(rm -rf /); echo pwn"
