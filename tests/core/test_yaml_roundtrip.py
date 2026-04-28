"""YAML round-trip + comment-preservation guarantees.

The project rule is "ruamel.yaml because it preserves comments — DO NOT
substitute pyyaml" (see ``project_auto_ext_design.md``). The existing
``tests/core/test_config.py`` exercises ``apply_project_edits`` /
``dump_project_yaml`` but only spot-checks individual lines; it does not
assert that *every* comment, *the original key order*, and the file's
structural details (single trailing ``\n``, no spurious blank lines,
inline comments still inline) survive a load → edit → save cycle.

This file fills that gap. If a future refactor swaps ``ruamel`` for
``pyyaml`` or breaks the round-trip path, these tests will fail loudly.

All test functions are prefixed ``test_yaml_rt_`` and helpers ``_yaml_rt_``
so the namespace is unmistakably mine in greps and pytest output.
"""

from __future__ import annotations

import re
from io import StringIO
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from auto_ext.core.config import (
    apply_project_edits,
    apply_tasks_edits,
    dump_project_yaml,
    dump_tasks_yaml,
    load_project,
    load_tasks_with_raw,
)


# ---- helpers ---------------------------------------------------------------


def _yaml_rt_extract_comment_lines(text: str) -> list[str]:
    """Return every comment substring (everything from ``#`` to EOL) in ``text``.

    Catches both line-leading comments (``# foo``) and inline comments
    (``key: val  # foo``). Used to assert "every comment that was there
    before is still there" without caring about line position.
    """
    comments: list[str] = []
    for line in text.splitlines():
        idx = line.find("#")
        if idx == -1:
            continue
        comments.append(line[idx:].rstrip())
    return comments


def _yaml_rt_top_level_keys_in_order(text: str) -> list[str]:
    """Return top-level mapping keys in the textual order they appear."""
    keys: list[str] = []
    pat = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:")
    for raw_line in text.splitlines():
        if not raw_line or raw_line.startswith((" ", "\t", "#", "-")):
            continue
        m = pat.match(raw_line)
        if m:
            keys.append(m.group(1))
    return keys


def _yaml_rt_write(tmp_path: Path, name: str, content: str) -> Path:
    """Write ``content`` (UTF-8, no BOM) to ``tmp_path/name`` and return path."""
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


# ---- 1. comment preservation through edit ----------------------------------


def test_yaml_rt_comment_preservation_through_edit(tmp_path: Path) -> None:
    """Multi-line + inline + last-line comments all survive a single edit."""
    src = (
        "# header line one\n"
        "# header line two\n"
        "# header line three\n"
        "work_root: /data/work  # inline on work_root\n"
        "verify_root: /data/verify\n"
        "# block comment before employee_id\n"
        "employee_id: alice  # inline on employee_id\n"
        "# block comment before intermediate_dir\n"
        "intermediate_dir: ${WORK_ROOT2}\n"
        "layer_map: /pdk/layers.map  # inline on layer_map\n"
        "# trailing-of-file comment\n"
    )
    p = _yaml_rt_write(tmp_path, "project.yaml", src)
    project = load_project(p)

    apply_project_edits(project.raw, {"intermediate_dir": "/tmp/edited"})
    dumped = dump_project_yaml(project)

    # Every original comment must appear verbatim in the dump.
    original_comments = _yaml_rt_extract_comment_lines(src)
    dumped_comments = _yaml_rt_extract_comment_lines(dumped)
    for c in original_comments:
        assert c in dumped_comments, (
            f"comment lost in round-trip: {c!r}\n"
            f"original comments: {original_comments}\n"
            f"dumped comments:   {dumped_comments}"
        )

    # Only the target field's value differs.
    assert "intermediate_dir: /tmp/edited" in dumped
    assert "intermediate_dir: ${WORK_ROOT2}" not in dumped
    # All other field values intact.
    assert "work_root: /data/work" in dumped
    assert "employee_id: alice" in dumped
    assert "layer_map: /pdk/layers.map" in dumped


# ---- 2. key order preservation ---------------------------------------------


def test_yaml_rt_key_order_preserved_after_edit(tmp_path: Path) -> None:
    """Deliberately non-alphabetical key order survives load → edit → save.

    Order chosen here (verify_root, work_root, layer_map, employee_id) is
    NOT alphabetical and NOT the schema declaration order — pyyaml would
    sort it; ruamel must not.
    """
    src = (
        "# preserve-order test\n"
        "verify_root: /data/verify\n"
        "work_root: /data/work\n"
        "layer_map: /pdk/layers.map\n"
        "employee_id: alice\n"
    )
    p = _yaml_rt_write(tmp_path, "project.yaml", src)
    project = load_project(p)

    apply_project_edits(project.raw, {"employee_id": "bob"})
    dumped = dump_project_yaml(project)

    expected = ["verify_root", "work_root", "layer_map", "employee_id"]
    actual = _yaml_rt_top_level_keys_in_order(dumped)
    assert actual == expected, (
        f"key order changed by round-trip\n"
        f"  expected: {expected}\n"
        f"  actual:   {actual}"
    )
    # And the edit landed.
    assert "employee_id: bob" in dumped


# ---- 3. tasks.yaml round-trip with paths-bearing comments ------------------


def test_yaml_rt_tasks_yaml_paths_and_comments(tmp_path: Path) -> None:
    """tasks.yaml round-trip: comments + Phase 5.6.5 dspf_out_path + nested
    knobs all survive an edit-free dump.

    Phase 5.6.5 introduced ``dspf_out_path`` overrides on tasks; this exercises
    that the per-task override + surrounding comments still round-trip.
    """
    src = (
        "# tasks file header comment\n"
        "# second header line\n"
        "- library: TEST_LIB  # inline lib\n"
        "  cell: TEST_CELL\n"
        "  lvs_layout_view: layout\n"
        "  lvs_source_view: schematic\n"
        "  ground_net: vss  # inline on ground_net\n"
        "  out_file: av_ext\n"
        "  jivaro:\n"
        "    enabled: false\n"
        "    frequency_limit: 14\n"
        "    error_max: 2\n"
        "  # comment before continue_on_lvs_fail\n"
        "  continue_on_lvs_fail: false\n"
        "  # comment before dspf_out_path override\n"
        "  dspf_out_path: ${output_dir}/{cell}.dspf\n"
        "  knobs:\n"
        "    quantus:\n"
        "      exclude_floating_nets_limit: 200  # inline on knob\n"
        "# trailing comment\n"
    )
    p = _yaml_rt_write(tmp_path, "tasks.yaml", src)
    _tasks, raw = load_tasks_with_raw(p)

    dumped = dump_tasks_yaml(raw)

    original_comments = _yaml_rt_extract_comment_lines(src)
    dumped_comments = _yaml_rt_extract_comment_lines(dumped)
    for c in original_comments:
        assert c in dumped_comments, (
            f"tasks.yaml comment lost: {c!r}\n"
            f"original: {original_comments}\n"
            f"dumped:   {dumped_comments}"
        )

    # Phase 5.6.5: dspf_out_path override survives untouched.
    assert "dspf_out_path: ${output_dir}/{cell}.dspf" in dumped
    # Nested knob value survives.
    assert "exclude_floating_nets_limit: 200" in dumped
    # Spec-level scalars preserved.
    assert "library: TEST_LIB" in dumped
    assert "cell: TEST_CELL" in dumped


# ---- 4. anchors / merge keys -----------------------------------------------


def test_yaml_rt_anchors_and_merge_keys(tmp_path: Path) -> None:
    """No production yaml in this repo currently uses anchors (``&``) or merge
    keys (``<<``) — verified by grep at test-write time. This test still
    exercises the round-trip path on a synthetic example so a future
    introduction of anchors anywhere does not silently regress.

    The synthetic yaml is loaded + dumped through the *raw* ruamel API
    (not ``apply_project_edits``, which only knows ProjectConfig fields),
    matching how a hypothetical anchor-using config.yaml would survive.
    """
    src = (
        "# anchors + merge keys round-trip probe\n"
        "_defaults: &defaults\n"
        "  a: 1\n"
        "  b: 2\n"
        "first:\n"
        "  <<: *defaults\n"
        "  c: 3\n"
        "second:\n"
        "  <<: *defaults\n"
        "  c: 4\n"
        "# trailing\n"
    )
    yaml = YAML(typ="rt")
    data = yaml.load(src)
    buf = StringIO()
    yaml.dump(data, buf)
    out = buf.getvalue()

    # Anchor declaration + both back-references must survive textually.
    assert "&defaults" in out, "anchor declaration dropped on round-trip"
    assert out.count("*defaults") == 2, "merge-key references not preserved"
    # The merge-key syntax itself must be preserved (not flattened).
    assert "<<: *defaults" in out
    # And the comments around them.
    assert "# anchors + merge keys round-trip probe" in out
    assert "# trailing" in out


# ---- 5. trailing newline + structural details ------------------------------


def test_yaml_rt_trailing_newline_and_no_spurious_blanks(tmp_path: Path) -> None:
    """Output ends with exactly one ``\\n``, no trailing whitespace on lines,
    and no blank line is added that was not in the source.
    """
    src = (
        "# header\n"
        "work_root: /data/work\n"
        "verify_root: /data/verify\n"
        "employee_id: alice\n"
        "layer_map: /pdk/layers.map\n"
    )
    p = _yaml_rt_write(tmp_path, "project.yaml", src)
    project = load_project(p)

    # Edit a key that already exists — should NOT introduce blank lines.
    apply_project_edits(project.raw, {"employee_id": "bob"})
    dumped = dump_project_yaml(project)

    # Exactly one trailing newline.
    assert dumped.endswith("\n"), "missing trailing newline"
    assert not dumped.endswith("\n\n"), (
        "extra trailing newline added by ruamel round-trip"
    )

    # No trailing whitespace on any line.
    for i, line in enumerate(dumped.splitlines(), 1):
        assert line == line.rstrip(), (
            f"line {i} has trailing whitespace: {line!r}"
        )

    # No spurious blank lines (source had none, dump should have none).
    src_blanks = sum(1 for ln in src.splitlines() if ln.strip() == "")
    dump_blanks = sum(1 for ln in dumped.splitlines() if ln.strip() == "")
    assert dump_blanks == src_blanks, (
        f"blank-line count changed: src={src_blanks} dump={dump_blanks}\n"
        f"DUMP:\n{dumped}"
    )


def test_yaml_rt_existing_blank_lines_preserved(tmp_path: Path) -> None:
    """A blank line that *was* in the source (visual separator) survives.

    Symmetrical to the previous test: do not strip blank lines either.
    """
    src = (
        "# section A\n"
        "work_root: /data/work\n"
        "verify_root: /data/verify\n"
        "\n"
        "# section B\n"
        "employee_id: alice\n"
        "layer_map: /pdk/layers.map\n"
    )
    p = _yaml_rt_write(tmp_path, "project.yaml", src)
    project = load_project(p)

    dumped = dump_project_yaml(project)

    src_blanks = sum(1 for ln in src.splitlines() if ln.strip() == "")
    dump_blanks = sum(1 for ln in dumped.splitlines() if ln.strip() == "")
    assert dump_blanks == src_blanks, (
        f"blank-line preservation broken: src={src_blanks} dump={dump_blanks}\n"
        f"DUMP:\n{dumped}"
    )
    # Both section banners survive.
    assert "# section A" in dumped
    assert "# section B" in dumped


# ---- 6. negative test: malformed comment after block scalar ----------------


def test_yaml_rt_comment_after_block_scalar_documented(tmp_path: Path) -> None:
    """Document — via assertions — what the round-trip does to:

    (a) a comment that follows a block scalar at the *outer* (mapping)
        indent level. ruamel keeps such comments intact and re-emits them
        verbatim; this is the well-formed case.
    (b) a "comment" line indented *inside* the block scalar's content
        column. YAML spec says this is part of the block scalar's text,
        not a comment — it is therefore round-tripped as content (with
        the leading ``#`` preserved literally inside the scalar value).

    Neither case is sanitized away. If a future ruamel upgrade changes
    behavior here this test fails and surfaces it.
    """
    src = (
        "key1: |\n"
        "  block scalar line 1\n"
        "  block scalar line 2\n"
        "  # this # is INSIDE the scalar (matches indent), so it is content\n"
        "# this comment is OUTSIDE the scalar (zero-indent), so it is a comment\n"
        "key2: value\n"
    )
    yaml = YAML(typ="rt")
    data = yaml.load(src)

    # The "inside" comment is part of the scalar content.
    scalar_value = str(data["key1"])
    assert "# this # is INSIDE the scalar" in scalar_value, (
        "block-scalar content was sanitized — ruamel behavior changed"
    )

    buf = StringIO()
    yaml.dump(data, buf)
    out = buf.getvalue()

    # Both lines round-trip verbatim. The "outside" comment stays a comment.
    assert "# this comment is OUTSIDE the scalar" in out
    # The "inside" pseudo-comment stays content (still emitted in the block).
    assert "# this # is INSIDE the scalar (matches indent), so it is content" in out
    # And the structure (key1 block, then key2 scalar) is intact.
    assert "key1: |" in out
    assert "key2: value" in out


def test_yaml_rt_unusual_comment_no_space_after_hash(tmp_path: Path) -> None:
    """A comment line with no space after ``#`` (``#bad`` instead of ``# bad``)
    is still a valid YAML comment and must round-trip verbatim, NOT be
    rewritten into the canonical ``# bad`` form.
    """
    src = (
        "key1: 1\n"
        "#no-space-after-hash-comment\n"
        "key2: 2\n"
        "##double-hash\n"
        "key3: 3\n"
    )
    yaml = YAML(typ="rt")
    data = yaml.load(src)
    buf = StringIO()
    yaml.dump(data, buf)
    out = buf.getvalue()

    assert "#no-space-after-hash-comment" in out
    assert "##double-hash" in out


# ---- 7. extra: apply_tasks_edits round-trip preserves wrapper comments -----


@pytest.mark.xfail(
    strict=False,
    reason=(
        "ROUND-TRIP BUG surfaced by this test: apply_tasks_edits drops the "
        "file's *trailing* comment when seq[i] is overwritten with a plain "
        "dict. ruamel attaches the post-sequence comment to the last item's "
        "trailing-end token; replacing the item with a CommentedMap-less "
        "dict severs that link. The preamble + inter-spec comments DO "
        "survive (asserted below as positive evidence the test wiring works), "
        "but the trailing comment is lost. config.py docstring says "
        "'top-level container comments (file preamble, inter-spec blank "
        "lines) survive' — note it carefully omits 'trailing comments', so "
        "the loss may be intentional/known. Marked xfail strict=False so a "
        "future fix (e.g. preserve the seq's end-comment via "
        "raw.ca.comment) flips this to XPASS and surfaces the change."
    ),
)
def test_yaml_rt_apply_tasks_edits_preserves_wrapper_comments(
    tmp_path: Path,
) -> None:
    """``apply_tasks_edits`` overwrites spec entries but the file's
    top-level comment block (preamble) AND trailing comment should survive.

    Documented behavior (see config.py): "top-level container comments
    (file preamble, inter-spec blank lines) survive". This tests that
    contract end-to-end via the public dump path — and surfaces that the
    trailing comment is in fact NOT preserved (see xfail reason above).
    """
    src = (
        "# tasks.yaml preamble\n"
        "# more preamble\n"
        "- library: OLD_LIB\n"
        "  cell: OLD_CELL\n"
        "  lvs_layout_view: layout\n"
        "  lvs_source_view: schematic\n"
        "# trailing comment\n"
    )
    p = _yaml_rt_write(tmp_path, "tasks.yaml", src)
    _tasks, raw = load_tasks_with_raw(p)

    new_specs = [
        {
            "library": "NEW_LIB",
            "cell": "NEW_CELL",
            "lvs_layout_view": "layout",
            "lvs_source_view": "schematic",
        }
    ]
    apply_tasks_edits(raw, new_specs)
    dumped = dump_tasks_yaml(raw)

    # Preamble survives + new spec values landed (sanity).
    assert "# tasks.yaml preamble" in dumped
    assert "# more preamble" in dumped
    assert "library: NEW_LIB" in dumped
    assert "cell: NEW_CELL" in dumped
    assert "OLD_LIB" not in dumped
    assert "OLD_CELL" not in dumped
    # The bug: trailing comment is dropped. This is the assertion that XFAILs.
    assert "# trailing comment" in dumped, (
        "trailing comment lost when apply_tasks_edits overwrites the "
        "last sequence element with a plain dict"
    )


# ---- 8. extra: idempotent round-trip (zero-edit) ---------------------------


def test_yaml_rt_zero_edit_idempotent(tmp_path: Path) -> None:
    """Loading and immediately dumping (no edits) must be byte-identical for
    a representative project.yaml. Any divergence means ruamel is silently
    reformatting the user's file on every save.
    """
    src = (
        "# Minimal valid project.yaml used by tests/core/test_yaml_roundtrip.py\n"
        "work_root: /data/work\n"
        "verify_root: /data/verify\n"
        "setup_root: /data/setup\n"
        "employee_id: alice\n"
        "layer_map: /pdk/layers.map\n"
    )
    p = _yaml_rt_write(tmp_path, "project.yaml", src)
    project = load_project(p)
    dumped = dump_project_yaml(project)
    assert dumped == src, (
        "zero-edit round-trip changed the file\n"
        f"---SRC---\n{src!r}\n"
        f"---DUMP---\n{dumped!r}"
    )


# ---- 9. extra: real production fixture round-trips losslessly --------------


def test_yaml_rt_repo_project_fixture_roundtrips(fixtures_dir: Path) -> None:
    """The committed ``tests/fixtures/project_minimal.yaml`` round-trips with
    every comment intact. Catches regressions where edits to the fixture
    accidentally introduce a form that ruamel cannot preserve.
    """
    fixture = fixtures_dir / "project_minimal.yaml"
    src = fixture.read_text(encoding="utf-8")
    project = load_project(fixture)
    dumped = dump_project_yaml(project)

    src_comments = _yaml_rt_extract_comment_lines(src)
    dump_comments = _yaml_rt_extract_comment_lines(dumped)
    for c in src_comments:
        assert c in dump_comments, f"fixture comment lost: {c!r}"
