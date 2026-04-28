"""Tool plugin ABC and shared subprocess helper.

Every EDA tool (``calibre``, ``quantus``, ``jivaro``, ``si``, ``strmout``)
plugs into the runner by implementing :class:`Tool`. The runner never
imports concrete tools directly; it iterates over the registered
subclasses.

Lifecycle per task stage (orchestrated by :mod:`auto_ext.core.runner`):

1. ``render_template(...)`` materialises the tool-specific config file
   (e.g. ``.qci``, ``.cmd``, ``.xml``, ``si.env``) from the Jinja template
   under ``templates/``. Skipped by the runner when ``has_template``
   is ``False`` (``strmout``).
2. ``build_argv(input_path, context)`` returns the argv list. This is the
   one place each tool declares its command-line shape.
3. ``run(argv, cwd, env, log_path, *, cancel_token=None)`` spawns the
   subprocess. Default impl tees combined stdout/stderr to ``log_path``
   via :func:`run_subprocess`; tools only override for special invocation
   patterns (license wait, retries, etc.).
4. ``parse_result(result)`` post-processes outputs. Default returns the
   result unchanged. ``CalibreTool`` overrides to run the strict LVS
   check from :mod:`auto_ext.core.checks`.
"""

from __future__ import annotations

import queue
import shutil
import subprocess
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
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


@dataclass
class ToolResult:
    """Structured outcome of a single tool invocation."""

    success: bool
    stdout_path: Path | None = None
    artifact_paths: list[Path] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)


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

    #: Whether :meth:`render_template` should be called by the runner.
    #: ``strmout`` has no ``.j2`` and sets this to ``False``; the runner
    #: skips the render step entirely.
    has_template: bool = True

    def render_template(
        self,
        template_path: Path,
        context: dict[str, Any],
        env: dict[str, str],
        out_path: Path,
        *,
        knobs: dict[str, Any] | None = None,
    ) -> Path:
        """Render ``template_path`` with ``context`` + ``env`` to ``out_path``.

        When ``knobs`` is ``None``, load the sidecar manifest and fall
        back to its declared defaults — convenient for callers that
        don't plumb per-task knob overrides (e.g. template unit tests).
        Pass an explicit ``{}`` to opt out of default-filling; callers
        that have already resolved knobs (the runner) pass the resolved
        dict directly.
        """
        from auto_ext.core.template import render_template as _render

        if knobs is None:
            from auto_ext.core.manifest import load_manifest, resolve_knob_values

            manifest = load_manifest(template_path)
            knobs = resolve_knob_values(manifest, {}, {}, {})

        rendered = _render(template_path, context=context, env=env, knobs=knobs)
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
            stdout_path=log_path,
            diagnostics={"exit_code": exit_code, "argv": list(argv)},
        )

    def parse_result(self, result: ToolResult) -> ToolResult:
        """Post-process ``result``. Default: identity. ``CalibreTool`` overrides."""
        return result
