"""Classify *why* a stage failed, and say what to do about it.

A failed stage today gives the user one bit ("calibre FAILED") plus a log
they have to read. This module turns the facts the runner already holds --
the exit code, the parsed LVS report, whether rendering even got as far as
producing an input file -- into a :class:`FailureVerdict`: a class, a
one-line reason, and a ``next_action`` string the GUI can show under the
red row.

Four classes, per the S1 brief:

``license_unavailable``
    The tool could not obtain a license. Not our bug, not the user's
    netlist -- purely a matter of waiting or of the license configuration.

``environment``
    The flow never really started: executable not on PATH, a required
    input file missing, template rendering failed.

``lvs_mismatch``
    Calibre ran fine and reported a real layout-vs-schematic difference.
    This is the only class that means "the design needs work".

``tool_crash``
    The tool started, then died or produced nothing usable.

Plus ``unknown``, which is not a fifth diagnosis but the honest absence of
one -- see below.

**Certain facts vs. log text.** The rules that key off an exit code or off
:class:`~auto_ext.core.checks.LvsReport` are decided here in code, because
they are facts we control: :func:`auto_ext.tools.base.run_subprocess`
itself returns 127 for "executable not found", and ``checks.py`` parses the
banner. Everything that can only be recognised from the tool's own wording
-- ``license_unavailable`` above all -- lives in the data table
``failure_signatures.yaml`` next to this module. That table ships **empty**:
this project has no captured EDA log to derive patterns from, and a
made-up pattern is worse than none. When nothing matches, the verdict is
:attr:`FailureClass.UNKNOWN` and its ``next_action`` points the reader at
the table. The classifier never guesses.

**Rule order** (first match wins)::

    1. render_error set                  -> ENVIRONMENT          (certain)
    2. a declared input is missing       -> ENVIRONMENT          (certain)
    3. exit_code == 127                  -> ENVIRONMENT          (certain)
    4. LVS report parsed and not passed  -> LVS_MISMATCH         (certain)
    5. a signature matches the log text  -> the signature's class(signature)
    6. a declared output is missing or
       unparsable                        -> TOOL_CRASH           (certain)
    7. exit_code not in (None, 0)        -> TOOL_CRASH           (certain)
    8. nothing above                     -> UNKNOWN

Rule 4 sits above rule 5 deliberately: once Calibre has written a report we
can parse, the report *is* the answer, and a stray license message earlier
in the same log (from a retry, say) must not overrule it. Rule 5 sits above
rules 6 and 7 because "exited non-zero" is the symptom every failure shares
-- a matched signature is strictly more informative.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from auto_ext.core.checks import LvsReport
from auto_ext.core.errors import ConfigError

logger = logging.getLogger(__name__)

#: Data table of log-text signatures, shipped next to this module.
DEFAULT_SIGNATURES_PATH: Path = Path(__file__).with_name("failure_signatures.yaml")

#: Schema version this loader understands.
SIGNATURES_SCHEMA_VERSION = 1

#: How much of a stage log to scan. Quantus logs run to hundreds of MB on
#: large blocks; license and crash messages sit at one end or the other, so
#: oversized logs are scanned head-and-tail rather than whole.
MAX_LOG_SCAN_BYTES = 512 * 1024

#: Marker inserted between the head and tail halves of a truncated scan.
LOG_ELISION_MARKER = "\n...[auto_ext: middle of log not scanned]...\n"

#: Evidence stored in the run record is one line, capped.
MAX_EVIDENCE_CHARS = 200

_RE_SIGNATURE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

#: Wildcard accepted in a signature's ``stages`` list.
_ANY_STAGE = "*"


class FailureSignatureError(ConfigError):
    """``failure_signatures.yaml`` is malformed.

    Subclasses :class:`~auto_ext.core.errors.ConfigError` so a caller that
    already catches config problems keeps working, while code that cares
    specifically about the signature table can catch this.
    """


class FailureClass(str, Enum):
    """What kind of failure this was."""

    LICENSE_UNAVAILABLE = "license_unavailable"
    ENVIRONMENT = "environment"
    LVS_MISMATCH = "lvs_mismatch"
    TOOL_CRASH = "tool_crash"
    #: Not a diagnosis: nothing we can prove matched. Never guessed into.
    UNKNOWN = "unknown"


#: The classes a signature may assign. ``unknown`` is what you get when
#: nothing matches, so it is not assignable from the table.
ASSIGNABLE_CLASSES: frozenset[FailureClass] = frozenset(
    {
        FailureClass.LICENSE_UNAVAILABLE,
        FailureClass.ENVIRONMENT,
        FailureClass.LVS_MISMATCH,
        FailureClass.TOOL_CRASH,
    }
)


class Confidence(str, Enum):
    """How the verdict was reached -- so the GUI can hedge its wording."""

    #: Derived from a fact this codebase controls: an exit code we set, a
    #: report we parsed, a file we looked for.
    CERTAIN = "certain"
    #: Matched a log-text signature from ``failure_signatures.yaml``.
    SIGNATURE = "signature"
    #: Nothing matched. Only ever paired with :attr:`FailureClass.UNKNOWN`.
    NONE = "none"


#: One next step per class. A signature may override its own with
#: ``next_action:``. Imperative English, and always something to *do*.
DEFAULT_NEXT_ACTIONS: dict[FailureClass, str] = {
    FailureClass.LICENSE_UNAVAILABLE: (
        "No license seat was available. Check the license server (lmstat -a) for a "
        "free seat of this tool's feature, then re-run the stage; if this happens "
        "often, raise the tool's license wait time so the job queues instead of failing."
    ),
    FailureClass.ENVIRONMENT: (
        "The stage never really started. Open the stage log: its first two lines are "
        "the exact argv and cwd. Confirm the executable is on PATH in the shell that "
        "launched Auto_ext, and that every path the rendered input references exists "
        "and is readable."
    ),
    FailureClass.LVS_MISMATCH: (
        "Layout and schematic really differ. Open the LVS report and start at the "
        "first INCORRECT row of the CELL SUMMARY, then compare the discrepancy count "
        "against the previous run of this cell to see whether the mismatch is new."
    ),
    FailureClass.TOOL_CRASH: (
        "The tool started and then died. Read the tail of the stage log for its own "
        "error message, then re-run the same argv by hand in the workarea to "
        "reproduce it outside Auto_ext."
    ),
    FailureClass.UNKNOWN: (
        "Auto_ext could not classify this failure. Read the stage log; if the root "
        "cause turns out to be a recognisable message, add it to "
        "auto_ext/core/failure_signatures.yaml (the file header explains how) so the "
        "next run classifies itself."
    ),
}


def next_action_for(failure_class: FailureClass) -> str:
    """The default next step for ``failure_class``."""

    return DEFAULT_NEXT_ACTIONS[FailureClass(failure_class)]


@dataclass(frozen=True)
class FailureVerdict:
    """What went wrong, why we think so, and what to do next."""

    failure_class: FailureClass
    confidence: Confidence
    #: One line stating the finding, e.g. "exit code 127: executable not found".
    reason: str
    #: One or two imperative English sentences for the GUI.
    next_action: str
    #: The log line (or path) the verdict rests on, capped in length.
    evidence: str | None = None
    #: ``id`` of the matching signature, when rule 5 fired.
    signature_id: str | None = None

    @property
    def is_unknown(self) -> bool:
        """True when nothing matched and no diagnosis was reached."""

        return self.failure_class is FailureClass.UNKNOWN

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe form for ``StageRecord.details["failure"]``."""

        return {
            "failure_class": self.failure_class.value,
            "confidence": self.confidence.value,
            "reason": self.reason,
            "next_action": self.next_action,
            "evidence": self.evidence,
            "signature_id": self.signature_id,
        }


def _verdict(
    failure_class: FailureClass,
    confidence: Confidence,
    reason: str,
    *,
    evidence: str | None = None,
    next_action: str | None = None,
    signature_id: str | None = None,
) -> FailureVerdict:
    return FailureVerdict(
        failure_class=failure_class,
        confidence=confidence,
        reason=reason,
        next_action=next_action or next_action_for(failure_class),
        evidence=_cap(evidence),
        signature_id=signature_id,
    )


def _cap(text: str | None) -> str | None:
    if text is None:
        return None
    flat = " ".join(text.split())
    if len(flat) <= MAX_EVIDENCE_CHARS:
        return flat
    return flat[: MAX_EVIDENCE_CHARS - 1] + "…"


# ---- signature table -------------------------------------------------------


@dataclass(frozen=True)
class FailureSignature:
    """One log-text pattern from ``failure_signatures.yaml``."""

    id: str
    failure_class: FailureClass
    #: At least one must appear. Never empty.
    any: tuple[str, ...]
    #: All must appear, when given.
    all: tuple[str, ...] = ()
    #: Runner stage names this may fire on; empty tuple = any stage.
    stages: tuple[str, ...] = ()
    regex: bool = False
    case_sensitive: bool = False
    next_action: str | None = None
    note: str | None = None
    sample: str | None = None

    def applies_to(self, stage: str | None) -> bool:
        """True when this signature may fire on ``stage``."""

        if not self.stages:
            return True
        if stage is None:
            return False
        return stage in self.stages

    def matches(self, text: str) -> str | None:
        """Return the evidence line if this signature fires on ``text``, else None.

        The evidence is the whole log line containing the first ``any``
        pattern that hit, whitespace-collapsed and capped.
        """

        if not text:
            return None
        hit = self._search(text, self.any)
        if hit is None:
            return None
        for pattern in self.all:
            if self._search(text, (pattern,)) is None:
                return None
        return _cap(_line_at(text, hit))

    def _search(self, text: str, patterns: Sequence[str]) -> int | None:
        """Earliest offset at which any of ``patterns`` matches, or None.

        Earliest rather than first-listed: the evidence line stored in the
        run record should be the one the tool printed first, whichever
        pattern happened to catch it.
        """

        best: int | None = None
        haystack = text if self.case_sensitive else text.lower()
        for pattern in patterns:
            if self.regex:
                flags = 0 if self.case_sensitive else re.IGNORECASE
                m = re.search(pattern, text, flags)
                pos = m.start() if m else -1
            else:
                needle = pattern if self.case_sensitive else pattern.lower()
                pos = haystack.find(needle)
            if pos >= 0 and (best is None or pos < best):
                best = pos
        return best


def _line_at(text: str, offset: int) -> str:
    """The full line of ``text`` containing ``offset``."""

    start = text.rfind("\n", 0, offset) + 1
    end = text.find("\n", offset)
    if end == -1:
        end = len(text)
    return text[start:end]


@dataclass(frozen=True)
class SignatureTable:
    """The parsed contents of ``failure_signatures.yaml``."""

    version: int = SIGNATURES_SCHEMA_VERSION
    signatures: tuple[FailureSignature, ...] = ()
    #: Where it was loaded from; ``None`` for a table built in memory.
    source: Path | None = None

    def __len__(self) -> int:
        return len(self.signatures)

    def match(
        self, text: str, *, stage: str | None = None
    ) -> tuple[FailureSignature, str] | None:
        """First signature (in file order) that fires on ``text``, with evidence."""

        for signature in self.signatures:
            if not signature.applies_to(stage):
                continue
            evidence = signature.matches(text)
            if evidence is not None:
                return signature, evidence
        return None


#: A table with nothing in it -- what classification falls back to when the
#: file is missing or unreadable.
EMPTY_TABLE = SignatureTable()


def _require(cond: bool, msg: str, path: Path | None) -> None:
    if not cond:
        where = f" in {path}" if path is not None else ""
        raise FailureSignatureError(f"failure signatures{where}: {msg}")


def _as_str_tuple(value: Any, *, field_name: str, sig_id: str, path: Path | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    _require(
        isinstance(value, list),
        f"signature {sig_id!r}: {field_name} must be a string or a list of strings",
        path,
    )
    out: list[str] = []
    for item in value:
        _require(
            isinstance(item, str) and item.strip() != "",
            f"signature {sig_id!r}: {field_name} entries must be non-empty strings",
            path,
        )
        out.append(item)
    return tuple(out)


def _as_bool(value: Any, *, field_name: str, sig_id: str, path: Path | None) -> bool:
    if value is None:
        return False
    _require(
        isinstance(value, bool),
        f"signature {sig_id!r}: {field_name} must be true or false",
        path,
    )
    return value


def parse_signature_table(data: Any, *, source: Path | None = None) -> SignatureTable:
    """Build a :class:`SignatureTable` from already-loaded YAML data.

    Raises :class:`FailureSignatureError` on anything malformed. Strict on
    purpose: this is a hand-edited developer table, and a silently skipped
    entry would look exactly like a signature that never fires.
    """

    if data is None:
        # An entirely blank file is the same as an empty table.
        return SignatureTable(source=source)

    _require(isinstance(data, Mapping), "top level must be a mapping", source)
    unknown_keys = set(data) - {"version", "signatures"}
    _require(not unknown_keys, f"unknown top-level key(s): {sorted(unknown_keys)}", source)

    version = data.get("version", SIGNATURES_SCHEMA_VERSION)
    _require(isinstance(version, int), "version must be an integer", source)
    _require(
        version == SIGNATURES_SCHEMA_VERSION,
        f"unsupported version {version}; this build understands "
        f"{SIGNATURES_SCHEMA_VERSION}",
        source,
    )

    raw_signatures = data.get("signatures")
    if raw_signatures is None:
        raw_signatures = []
    _require(isinstance(raw_signatures, list), "signatures must be a list", source)

    seen: set[str] = set()
    signatures: list[FailureSignature] = []
    allowed_keys = {
        "id",
        "failure_class",
        "stages",
        "any",
        "all",
        "regex",
        "case_sensitive",
        "next_action",
        "note",
        "sample",
    }

    for index, raw in enumerate(raw_signatures):
        _require(isinstance(raw, Mapping), f"signature #{index} must be a mapping", source)
        sig_id = raw.get("id")
        _require(
            isinstance(sig_id, str) and bool(_RE_SIGNATURE_ID.match(sig_id)),
            f"signature #{index}: id must match {_RE_SIGNATURE_ID.pattern}",
            source,
        )
        assert isinstance(sig_id, str)  # narrowed by _require above
        _require(sig_id not in seen, f"duplicate signature id {sig_id!r}", source)
        seen.add(sig_id)

        extra = set(raw) - allowed_keys
        _require(not extra, f"signature {sig_id!r}: unknown key(s) {sorted(extra)}", source)

        raw_class = raw.get("failure_class")
        _require(
            isinstance(raw_class, str),
            f"signature {sig_id!r}: failure_class is required",
            source,
        )
        _require(
            raw_class in {c.value for c in ASSIGNABLE_CLASSES},
            f"signature {sig_id!r}: failure_class {raw_class!r} is not assignable; "
            f"expected one of {sorted(c.value for c in ASSIGNABLE_CLASSES)} "
            "('unknown' is the result of matching nothing, not a value you set)",
            source,
        )
        failure_class = FailureClass(raw_class)

        any_patterns = _as_str_tuple(
            raw.get("any"), field_name="any", sig_id=sig_id, path=source
        )
        _require(bool(any_patterns), f"signature {sig_id!r}: any must list >= 1 pattern", source)
        all_patterns = _as_str_tuple(
            raw.get("all"), field_name="all", sig_id=sig_id, path=source
        )
        stages = _as_str_tuple(
            raw.get("stages"), field_name="stages", sig_id=sig_id, path=source
        )
        if _ANY_STAGE in stages:
            stages = ()

        regex = _as_bool(raw.get("regex"), field_name="regex", sig_id=sig_id, path=source)
        if regex:
            for pattern in (*any_patterns, *all_patterns):
                try:
                    re.compile(pattern)
                except re.error as exc:
                    _require(
                        False,
                        f"signature {sig_id!r}: bad regular expression {pattern!r}: {exc}",
                        source,
                    )

        next_action = raw.get("next_action")
        _require(
            next_action is None or isinstance(next_action, str),
            f"signature {sig_id!r}: next_action must be a string",
            source,
        )
        note = raw.get("note")
        _require(
            note is None or isinstance(note, str),
            f"signature {sig_id!r}: note must be a string",
            source,
        )
        sample = raw.get("sample")
        _require(
            sample is None or isinstance(sample, str),
            f"signature {sig_id!r}: sample must be a string",
            source,
        )

        signatures.append(
            FailureSignature(
                id=sig_id,
                failure_class=failure_class,
                any=any_patterns,
                all=all_patterns,
                stages=stages,
                regex=regex,
                case_sensitive=_as_bool(
                    raw.get("case_sensitive"),
                    field_name="case_sensitive",
                    sig_id=sig_id,
                    path=source,
                ),
                next_action=next_action.strip() if isinstance(next_action, str) else None,
                note=note,
                sample=sample,
            )
        )

    return SignatureTable(version=version, signatures=tuple(signatures), source=source)


def load_signatures(path: Path | None = None) -> SignatureTable:
    """Load the signature table from ``path`` (default: the bundled file).

    Raises :class:`FailureSignatureError` when the file exists but is
    malformed, and when ``path`` was given explicitly but does not exist.
    A *missing bundled* file yields an empty table with a warning -- the
    classifier must keep working in a stripped-down deployment.

    Not cached: the table is small, it is consulted at most once per failed
    stage, and a cache would need invalidating every time someone edits the
    file during a debugging session.
    """

    target = DEFAULT_SIGNATURES_PATH if path is None else Path(path)

    if not target.is_file():
        if path is None:
            logger.warning(
                "failure signature table not found at %s; classification will fall "
                "back to certain-fact rules only",
                target,
            )
            return SignatureTable(source=None)
        raise FailureSignatureError(f"failure signature table not found: {target}")

    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise FailureSignatureError(f"cannot read {target}: {exc}") from exc

    yaml = YAML(typ="safe")
    try:
        data = yaml.load(text)
    except YAMLError as exc:
        raise FailureSignatureError(f"cannot parse {target}: {exc}") from exc

    return parse_signature_table(data, source=target)


def _table_or_empty(signatures: SignatureTable | None) -> SignatureTable:
    """Resolve the table to use, never raising into a running stage."""

    if signatures is not None:
        return signatures
    try:
        return load_signatures()
    except FailureSignatureError:
        logger.exception(
            "failure signature table is malformed; classifying without it. "
            "Fix %s.",
            DEFAULT_SIGNATURES_PATH,
        )
        return EMPTY_TABLE


# ---- log reading -----------------------------------------------------------


def read_log_excerpt(path: Path, *, max_bytes: int = MAX_LOG_SCAN_BYTES) -> str:
    """Read ``path`` for scanning, head-and-tail when it is oversized.

    License diagnostics land at the start (checkout at launch) and crash
    tracebacks at the end, so when a log exceeds ``max_bytes`` we keep the
    first and last halves and drop the middle rather than truncating one
    end. Returns ``""`` for a missing or unreadable file -- an unreadable
    log is not itself a diagnosis.
    """

    try:
        size = path.stat().st_size
    except OSError:
        return ""

    try:
        if size <= max_bytes:
            return path.read_text(encoding="utf-8", errors="replace")
        half = max_bytes // 2
        with path.open("rb") as fh:
            head = fh.read(half)
            fh.seek(-half, 2)
            tail = fh.read(half)
    except OSError as exc:
        logger.warning("cannot read stage log %s: %s", path, exc)
        return ""

    return (
        head.decode("utf-8", errors="replace")
        + LOG_ELISION_MARKER
        + tail.decode("utf-8", errors="replace")
    )


# ---- the classifier --------------------------------------------------------


def _path_strings(values: Iterable[Path | str] | None) -> tuple[str, ...]:
    """Normalise an optional path iterable to a tuple of strings."""

    if not values:
        return ()
    return tuple(str(v) for v in values)


def classify_failure(
    *,
    stage: str | None = None,
    exit_code: int | None = None,
    lvs: LvsReport | None = None,
    render_error: str | None = None,
    missing_inputs: Iterable[Path | str] | None = None,
    missing_outputs: Iterable[Path | str] | None = None,
    unparsable_output: str | None = None,
    log_text: str | None = None,
    log_path: Path | None = None,
    signatures: SignatureTable | None = None,
) -> FailureVerdict:
    """Classify a stage failure from the facts the caller already has.

    Every argument is optional; supply what you know. With nothing at all
    the verdict is :attr:`FailureClass.UNKNOWN`, which is the correct
    answer to "a stage failed and I can tell you nothing about it".

    Args:
        stage: runner stage name, used to scope signatures.
        exit_code: subprocess exit code. ``127`` is
            :func:`~auto_ext.tools.base.run_subprocess`'s "executable not
            found" convention; ``None`` means no process ran.
        lvs: parsed LVS report, when the stage produced one.
        render_error: message from a template render that failed before
            any process started.
        missing_inputs: input files the stage needed and did not find.
        missing_outputs: outputs the rendered input declared that the stage
            did not produce.
        unparsable_output: message explaining why a produced output could
            not be read (e.g. a truncated LVS report).
        log_text: stage log content to scan for signatures. When omitted,
            it is read from ``log_path``.
        log_path: stage log to read when ``log_text`` is not given.
        signatures: table to match against; loaded from disk when omitted.

    Returns:
        A :class:`FailureVerdict`. Never raises: a classifier that throws
        while explaining a failure is worse than one that says "unknown".
    """

    missing_in = _path_strings(missing_inputs)
    missing_out = _path_strings(missing_outputs)

    # 1. Rendering never produced an input file.
    if render_error:
        return _verdict(
            FailureClass.ENVIRONMENT,
            Confidence.CERTAIN,
            f"the stage input could not be rendered: {render_error}",
            evidence=render_error,
        )

    # 2. A required input was not there.
    if missing_in:
        listed = ", ".join(missing_in)
        return _verdict(
            FailureClass.ENVIRONMENT,
            Confidence.CERTAIN,
            f"required input file(s) missing: {listed}",
            evidence=listed,
        )

    # 3. Executable not on PATH (run_subprocess's own convention).
    if exit_code == 127:
        return _verdict(
            FailureClass.ENVIRONMENT,
            Confidence.CERTAIN,
            "exit code 127: the executable was not found on PATH",
        )

    # 4. Calibre ran and told us the design does not match.
    if lvs is not None and not lvs.passed:
        detail = f"LVS banner {lvs.banner}" if lvs.banner else "LVS reported a mismatch"
        if lvs.discrepancies is not None:
            detail += f", {lvs.discrepancies} discrepancies"
        if lvs.mismatched_cells:
            shown = ", ".join(lvs.mismatched_cells[:5])
            more = len(lvs.mismatched_cells) - 5
            if more > 0:
                shown += f" (+{more} more)"
            detail += f"; INCORRECT cells: {shown}"
        return _verdict(
            FailureClass.LVS_MISMATCH,
            Confidence.CERTAIN,
            detail,
            evidence=str(lvs.source),
        )

    # 5. The tool said something we have a signature for.
    text = log_text
    if text is None and log_path is not None:
        text = read_log_excerpt(log_path)
    if text:
        hit = _table_or_empty(signatures).match(text, stage=stage)
        if hit is not None:
            signature, evidence = hit
            return _verdict(
                signature.failure_class,
                Confidence.SIGNATURE,
                f"log matched signature {signature.id!r}",
                evidence=evidence,
                next_action=signature.next_action,
                signature_id=signature.id,
            )

    # 6. It claimed to run but produced nothing usable.
    if unparsable_output:
        return _verdict(
            FailureClass.TOOL_CRASH,
            Confidence.CERTAIN,
            f"the stage produced output that could not be read: {unparsable_output}",
            evidence=unparsable_output,
        )
    if missing_out:
        listed = ", ".join(missing_out)
        return _verdict(
            FailureClass.TOOL_CRASH,
            Confidence.CERTAIN,
            f"declared output(s) never appeared: {listed}",
            evidence=listed,
        )

    # 7. Non-zero exit with nothing more specific to say.
    if exit_code not in (None, 0):
        return _verdict(
            FailureClass.TOOL_CRASH,
            Confidence.CERTAIN,
            f"the tool exited {exit_code}",
        )

    # 8. Out of rules. Say so.
    return _verdict(
        FailureClass.UNKNOWN,
        Confidence.NONE,
        "no rule and no log signature matched this failure",
    )


def classify_tool_result(
    result: Any,
    *,
    stage: str | None = None,
    log_text: str | None = None,
    signatures: SignatureTable | None = None,
) -> FailureVerdict:
    """Classify from a :class:`~auto_ext.tools.base.ToolResult`.

    The adapter the runner wants: it pulls the exit code, the parsed LVS
    report and the "declared output missing" diagnostics straight out of
    the result, and reads the stage log from ``result.stdout_path``.

    ``result`` is typed loosely to keep :mod:`auto_ext.core` free of an
    import edge into :mod:`auto_ext.tools`; anything with the
    :class:`~auto_ext.tools.base.ToolResult` shape works, and every field
    is read defensively so a partially-built result still classifies.
    """

    diagnostics: Mapping[str, Any] = getattr(result, "diagnostics", None) or {}

    exit_code = getattr(result, "exit_code", None)
    if exit_code is None:
        raw_exit = diagnostics.get("exit_code")
        exit_code = raw_exit if isinstance(raw_exit, int) else None

    raw_lvs = diagnostics.get("lvs_report")
    lvs = raw_lvs if isinstance(raw_lvs, LvsReport) else None

    missing_outputs: list[str] = []
    report_missing = diagnostics.get("lvs_report_missing")
    if isinstance(report_missing, str):
        missing_outputs.append(report_missing)
    declared_missing = diagnostics.get("missing_artifacts")
    if isinstance(declared_missing, list):
        missing_outputs.extend(str(p) for p in declared_missing)

    unparsable = diagnostics.get("lvs_parse_error")

    log_path = getattr(result, "stdout_path", None)

    return classify_failure(
        stage=stage,
        exit_code=exit_code,
        lvs=lvs,
        missing_outputs=missing_outputs,
        unparsable_output=unparsable if isinstance(unparsable, str) else None,
        log_text=log_text,
        log_path=log_path if isinstance(log_path, Path) else None,
        signatures=signatures,
    )


__all__ = [
    "ASSIGNABLE_CLASSES",
    "DEFAULT_NEXT_ACTIONS",
    "DEFAULT_SIGNATURES_PATH",
    "EMPTY_TABLE",
    "LOG_ELISION_MARKER",
    "MAX_EVIDENCE_CHARS",
    "MAX_LOG_SCAN_BYTES",
    "SIGNATURES_SCHEMA_VERSION",
    "Confidence",
    "FailureClass",
    "FailureSignature",
    "FailureSignatureError",
    "FailureVerdict",
    "SignatureTable",
    "classify_failure",
    "classify_tool_result",
    "load_signatures",
    "next_action_for",
    "parse_signature_table",
    "read_log_excerpt",
]
