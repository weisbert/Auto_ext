"""Tool plugin ABC and shared subprocess helper.

Every EDA tool (``calibre``, ``quantus``, ``jivaro``, ``si``, ``strmout``)
plugs into the runner by implementing :class:`Tool`. The runner never
imports concrete tools directly; it iterates over the registered
subclasses.

Lifecycle per task stage (orchestrated by :mod:`auto_ext.core.runner`):

1. The catalog pipeline (:mod:`auto_ext.core.render`) materialises the
   tool-specific config file (e.g. ``.qci``, ``.cmd``, ``.xml``, ``si.env``)
   from the Jinja template under ``templates/``, applying the recipe's stored
   patches on the way out. Skipped by the runner when ``has_template`` is
   ``False`` (``strmout``).
2. ``build_argv(input_path, context)`` returns the argv list. This is the
   one place each tool declares its command-line shape.
3. ``run(argv, cwd, env, log_path, *, cancel_token=None)`` spawns the
   subprocess. Default impl tees combined stdout/stderr to ``log_path``
   via :func:`run_subprocess`; tools only override for special invocation
   patterns (license wait, retries, etc.).
4. ``parse_result(result)`` post-processes outputs: it is where each tool
   records what it produced. Every tool overrides it to fill
   :attr:`ToolResult.artifact_paths` / :attr:`ToolResult.cellviews`, and
   ``CalibreTool`` additionally runs the strict LVS check from
   :mod:`auto_ext.core.checks`.

Two kinds of artifact
---------------------
Not every EDA output is a file. Quantus' ``output_db -type extracted_view``
and Jivaro's ``<outputView>`` write a *cellview* into the DFII library --
addressed as a ``library / cell / view`` triple, with no path on disk that
means anything to ``open()``. Conflating the two would put a fabricated
path into the run record, so :class:`ToolResult` keeps them apart:
:attr:`~ToolResult.artifact_paths` for real files and
:attr:`~ToolResult.cellviews` for :class:`CellViewRef` triples. See
``docs/refactor/05-catalog-critique.md`` item A5.
"""

from __future__ import annotations

import queue
import shutil
import subprocess
import threading
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from auto_ext.core.progress import CancelToken

#: SIGTERM → SIGKILL grace window when :class:`CancelToken` fires mid-run.
#: 10s matches the Kubernetes default; chosen over 3s because Calibre
#: LVS writes multi-GB SVDB databases and Quantus writes parasitic
#: netlists — mid-write corruption on retry is worse than a 7-second
#: extra Cancel latency.
CANCEL_GRACE_SECONDS: float = 10.0

#: Bounded poll interval for the main drain loop. Sets the worst-case
#: cancel latency when the subprocess is silent (no stdout arriving).
_DRAIN_POLL_SECONDS: float = 0.5


#: ``diagnostics`` key under which :meth:`ToolResult.with_artifacts` files
#: the declared-but-absent outputs. :func:`auto_ext.core.failure_class.
#: classify_tool_result` reads it as the "tool produced nothing" signal.
MISSING_ARTIFACTS_KEY = "missing_artifacts"


@dataclass(frozen=True)
class CellViewRef:
    """A DFII cellview -- ``library / cell / view`` -- which is not a file.

    Quantus' extracted view and Jivaro's reduced view land inside the
    Cadence library, not on a path. Recording one of these as a filesystem
    path would invent a location that does not exist; recording it as a
    triple keeps the run record honest and still lets the GUI say exactly
    what was written.
    """

    library: str
    cell: str
    view: str

    @property
    def key(self) -> str:
        """``"lib/cell/view"`` -- for display and comparison, never for ``open()``."""

        return f"{self.library}/{self.cell}/{self.view}"

    def as_dict(self) -> dict[str, str]:
        """JSON-safe form, for ``StageRecord.details``."""

        return {"library": self.library, "cell": self.cell, "view": self.view}


@dataclass
class ToolResult:
    """Structured outcome of a single tool invocation."""

    success: bool
    #: Subprocess exit code, or ``None`` when no process ran (dry run,
    #: render-only, synthetic result). ``127`` is
    #: :func:`run_subprocess`'s "executable not found" convention.
    exit_code: int | None = None
    stdout_path: Path | None = None
    #: Absolute paths of real files this invocation produced. Only paths
    #: that exist on disk get in here (see :meth:`with_artifacts`).
    artifact_paths: list[Path] = field(default_factory=list)
    #: DFII cellviews this invocation wrote. Deliberately separate from
    #: :attr:`artifact_paths` -- these have no filesystem location.
    cellviews: list[CellViewRef] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def with_artifacts(
        self,
        paths: Iterable[Path | str] = (),
        *,
        cellviews: Iterable[CellViewRef] = (),
        require_exists: bool = True,
        missing_key: str = MISSING_ARTIFACTS_KEY,
    ) -> ToolResult:
        """Return a copy with ``paths`` / ``cellviews`` recorded.

        Order-preserving and de-duplicating, so calling this twice with
        overlapping declarations does not double-list anything.

        With ``require_exists`` (the default), a declared path that is not
        on disk is *not* added to :attr:`artifact_paths` -- it is appended
        to ``diagnostics[missing_key]`` instead. A run record whose
        artifact list points at files that were never written is worse than
        one that admits the output is missing, and the missing list is
        exactly the evidence
        :func:`auto_ext.core.failure_class.classify_failure` needs for its
        "declared output never appeared" rule.

        Uses :func:`dataclasses.replace`, so fields added later are carried
        over without touching this method.
        """

        kept = list(self.artifact_paths)
        seen = {str(p) for p in kept}
        missing: list[str] = []

        for raw in paths:
            path = Path(raw)
            key = str(path)
            if key in seen:
                continue
            if require_exists and not path.exists():
                if key not in missing:
                    missing.append(key)
                continue
            seen.add(key)
            kept.append(path)

        views = list(self.cellviews)
        for ref in cellviews:
            if ref not in views:
                views.append(ref)

        diagnostics = dict(self.diagnostics)
        if missing:
            previous = diagnostics.get(missing_key)
            merged = list(previous) if isinstance(previous, list) else []
            merged.extend(m for m in missing if m not in merged)
            diagnostics[missing_key] = merged

        return replace(
            self,
            artifact_paths=kept,
            cellviews=views,
            diagnostics=diagnostics,
        )


def argv_value(argv: Sequence[str], flag: str) -> str | None:
    """Return the token following ``flag`` in ``argv``, or ``None``.

    Every tool's rendered input file is reachable from its own argv
    (``-runset`` / ``-cmd`` / ``-xml``), which is how ``parse_result`` --
    whose only input is the :class:`ToolResult` -- finds the file it needs
    to read the tool's declared outputs out of.
    """

    values = list(argv)
    try:
        index = values.index(flag)
    except ValueError:
        return None
    if index + 1 >= len(values):
        return None
    return values[index + 1]


def argv_path(result: ToolResult, flag: str) -> Path | None:
    """The ``flag`` argument of ``result``'s recorded argv, as a Path.

    Returns ``None`` when the result carries no argv, the flag is absent,
    or nothing follows it.
    """

    argv = result.diagnostics.get("argv")
    if not isinstance(argv, (list, tuple)):
        return None
    value = argv_value([str(a) for a in argv], flag)
    return None if value is None else Path(value)


def run_subprocess(
    argv: list[str],
    cwd: Path,
    env: dict[str, str],
    log_path: Path,
    *,
    cancel_token: CancelToken | None = None,
) -> int:
    """Blocking: execute ``argv`` with ``cwd`` + ``env``, tee stdout/stderr to ``log_path``.

    Creates ``log_path``'s parent dir. Writes an audit header (argv + cwd)
    before the command output so the log is self-describing. Returns the
    subprocess exit code, or 127 if the executable is not found (bash
    "command not found" convention).

    Cancellation: when ``cancel_token`` is supplied and fires mid-run, the
    subprocess is terminated via :meth:`subprocess.Popen.terminate` and
    given :data:`CANCEL_GRACE_SECONDS` to exit; if it doesn't,
    :meth:`~subprocess.Popen.kill` escalates. Returned exit code then
    reflects the signal (POSIX: negative signal number; Windows: the
    ``TerminateProcess`` return code — usually 1). Callers should
    distinguish "cancelled" from "failed" by checking the token itself,
    not by inspecting the exit code.

    Implementation note: stdout is drained on a dedicated reader thread
    into a queue so the main loop can poll the cancel token between
    queue waits. A direct ``for line in proc.stdout`` would block
    indefinitely on a silent subprocess (Quantus routinely goes minutes
    without output during parasitic solve), making cancel unresponsive.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Resolve argv[0] via PATH ourselves so .bat shims work on Windows
    # (CreateProcess alone only resolves .exe) and so "not found" fails
    # with a readable log entry instead of WinError 2.
    resolved = shutil.which(argv[0], path=env.get("PATH"))
    if resolved is None:
        log_path.write_text(
            f"# argv: {argv}\n# cwd: {cwd}\n"
            f"# ERROR: executable not found on PATH: {argv[0]!r}\n",
            encoding="utf-8",
        )
        return 127
    resolved_argv = [resolved, *argv[1:]]

    with log_path.open("w", encoding="utf-8") as log:
        log.write(f"# argv: {resolved_argv}\n# cwd: {cwd}\n\n")
        log.flush()
        try:
            proc = subprocess.Popen(
                resolved_argv,
                cwd=str(cwd),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError as exc:
            log.write(f"# ERROR: executable not found: {resolved_argv[0]!r}\n# {exc}\n")
            return 127
        assert proc.stdout is not None

        line_queue: queue.Queue[str | None] = queue.Queue()

        def _reader() -> None:
            try:
                for line in proc.stdout:  # type: ignore[union-attr]
                    line_queue.put(line)
            finally:
                line_queue.put(None)

        reader_thread = threading.Thread(
            target=_reader, name=f"run_subprocess-reader-{proc.pid}", daemon=True
        )
        reader_thread.start()

        cancelled = False
        while True:
            if cancel_token is not None and cancel_token.is_cancelled():
                cancelled = True
                break
            try:
                item = line_queue.get(timeout=_DRAIN_POLL_SECONDS)
            except queue.Empty:
                continue
            if item is None:
                break
            log.write(item)
            # Flush per-line so the GUI's LogTab (QFileSystemWatcher +
            # 1s poll) sees fresh content immediately. Without this the
            # default ~4 KB stdio buffer can withhold output for the
            # entire stage, defeating live tailing.
            log.flush()

        if cancelled:
            log.write("\n# CANCELLED: terminating subprocess...\n")
            log.flush()
            proc.terminate()
            try:
                exit_code = proc.wait(timeout=CANCEL_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                log.write(
                    f"# subprocess ignored terminate() after "
                    f"{CANCEL_GRACE_SECONDS}s; escalating to kill\n"
                )
                proc.kill()
                exit_code = proc.wait()
            # Drain anything the reader captured before the pipe closed.
            reader_thread.join(timeout=2.0)
            while True:
                try:
                    item = line_queue.get_nowait()
                except queue.Empty:
                    break
                if item is None:
                    break
                log.write(item)
            log.write(f"\n# exit: {exit_code} (cancelled)\n")
        else:
            exit_code = proc.wait()
            reader_thread.join(timeout=2.0)
            log.write(f"\n# exit: {exit_code}\n")
    return exit_code


class Tool(ABC):
    """Abstract base class for every EDA tool plugin."""

    #: Short identifier used in templates, configs and logs (e.g. ``"calibre"``).
    name: str = ""

    #: Default command-line executable, resolvable via ``PATH`` on the server.
    executable: str = ""

    #: Whether this stage consumes a rendered input file at all. ``strmout``
    #: has no template and sets this to ``False``; the runner skips the render
    #: step entirely and passes it the ``rendered/`` directory instead.
    has_template: bool = True

    def render_template(
        self,
        template_path: Path,
        context: dict[str, Any],
        env: dict[str, str],
        out_path: Path,
    ) -> Path:
        """Render ``template_path`` with ``context`` + ``env`` to ``out_path``.

        ``context`` is the whole Jinja namespace. The runner does not call
        this any more -- :mod:`auto_ext.core.render` renders the catalog's
        templates from source strings, because a stored patch has to be
        applied between the render and the write. This stays as the
        render-a-file-from-disk entry point for tool-level tests and one-off
        scripts, and because it is where ``has_template`` stops mattering.
        """
        from auto_ext.core.template import render_template as _render

        rendered = _render(template_path, context=context, env=env)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered, encoding="utf-8")
        return out_path

    @abstractmethod
    def build_argv(
        self,
        input_path: Path,
        context: dict[str, Any],
    ) -> list[str]:
        """Return the subprocess argv for this tool invocation.

        ``input_path`` is the rendered input file from :meth:`render_template`
        (or, when ``has_template`` is ``False``, a sentinel the tool may
        ignore). ``context`` is the task render context; tools like
        ``strmout`` that build argv from task fields consume it directly.
        """

    def run(
        self,
        argv: list[str],
        cwd: Path,
        env: dict[str, str],
        log_path: Path,
        *,
        cancel_token: CancelToken | None = None,
    ) -> ToolResult:
        """Spawn the subprocess and return a :class:`ToolResult`.

        Default: :func:`run_subprocess` + exit-code to success. Override
        only when the tool needs pre/post-execution logic beyond the norm.

        ``cancel_token`` is threaded through so the runner can hard-kill
        this stage's subprocess mid-run. Overriders that do their own
        pre-run work (e.g. :class:`SiTool.run` unlinking ``.running``)
        must forward the token to :func:`run_subprocess`.
        """
        exit_code = run_subprocess(
            argv, cwd=cwd, env=env, log_path=log_path, cancel_token=cancel_token
        )
        return ToolResult(
            success=(exit_code == 0),
            exit_code=exit_code,
            stdout_path=log_path,
            # ``exit_code`` is duplicated into diagnostics on purpose: it
            # has been read from there since before the field existed, and
            # ``argv`` next to it is what ``parse_result`` uses to find the
            # rendered input file.
            diagnostics={"exit_code": exit_code, "argv": list(argv)},
        )

    def parse_result(self, result: ToolResult) -> ToolResult:
        """Post-process ``result``. Default: identity.

        Every concrete tool overrides this to record what it produced.
        Note the signature: the runner hands over the :class:`ToolResult`
        and nothing else, so an override that needs to know where its
        outputs went reads them out of the rendered input file named in
        ``result.diagnostics["argv"]`` (see :func:`argv_path`) rather than
        from the render context.
        """
        return result
