"""Tests for :mod:`auto_ext.ui.patch_capture`.

The escape hatch behind the Recipes screen's ``Edit rendered file``: render a
target the way a run would, take the user's edit, store it as a masked patch.
Qt-free, so these run without a QApplication.

The property that matters is the round trip -- an edit captured against one
cell must apply to a different cell -- because that is the only reason the
patch is masked rather than stored as plain text.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_ext.core.patch import apply_patch
from auto_ext.ui import patch_capture
from tests.support.v2 import ENV, make_cell, make_workspace


@pytest.fixture
def si_plan(recipe):
    """The ``si.env`` target -- first in stage order, and the smallest file."""

    plans = patch_capture.editable_targets(recipe)
    assert plans, "the default recipe must generate at least one file"
    return plans[0]


@pytest.fixture
def preview(si_plan, recipe, pdk_profile, tmp_path: Path):
    return patch_capture.build_preview(
        si_plan,
        recipe=recipe,
        profile=pdk_profile,
        workspace=make_workspace(),
        cell=make_cell(),
        resolved_env=ENV,
        workarea=tmp_path / "wa",
    )


# ---- workspace path expansion ----------------------------------------------


def test_the_workspace_patterns_expand_env_refs_then_format_keys() -> None:
    paths = patch_capture.resolve_workspace_paths(
        make_workspace(), make_cell(cell="amp2"), ENV, recipe_id="rc"
    )
    assert paths.output_dir.endswith("QCI_PATH_amp2")
    assert paths.dspf_out_path.endswith("amp2.dspf")
    assert "$" not in paths.output_dir


def test_an_unresolved_env_reference_is_refused_with_the_variable_named() -> None:
    """si and jivaro do not expand ``$VAR`` inside string values, so a file
    carrying one would be wrong rather than merely unresolved."""

    with pytest.raises(patch_capture.CaptureError) as exc:
        patch_capture.resolve_workspace_paths(
            make_workspace(), make_cell(), {}, recipe_id="rc"
        )
    assert "env reference" in str(exc.value)


def test_the_recipe_id_is_available_as_a_format_key() -> None:
    workspace = make_workspace(output_dir_pattern="${WORK_ROOT}/{recipe}/{cell}")
    paths = patch_capture.resolve_workspace_paths(
        workspace, make_cell(cell="inv"), ENV, recipe_id="rc-fast"
    )
    assert paths.output_dir.endswith("/rc-fast/inv")


# ---- the preview ------------------------------------------------------------


def test_the_preview_renders_the_file_the_run_would_write(preview) -> None:
    assert preview.filename
    assert preview.base_text
    assert "[[" not in preview.base_text, "Jinja must be fully rendered"
    assert "$" not in preview.base_text


def test_the_masked_twin_is_line_aligned_with_the_real_render(preview) -> None:
    """``capture_patch`` refuses a misaligned pair, and a silent misalignment
    would attach every hunk to the wrong masked line."""

    assert len(preview.base_text.splitlines()) == len(
        preview.masked_text.splitlines()
    )


def test_unmasking_the_twin_reproduces_the_real_render(preview) -> None:
    """The invariant the whole format rests on. ``values`` covers the entire
    render context, so not every slot appears in every file -- what has to
    hold is that the ones that do round-trip exactly."""

    from auto_ext.core.patch import slots_in, unmask

    assert slots_in(preview.masked_text), "this file masks nothing at all"
    assert unmask(preview.masked_text, preview.values) == preview.base_text


def test_a_missing_environment_is_reported_not_raised_as_a_stack_trace(
    si_plan, recipe, pdk_profile, tmp_path: Path
) -> None:
    with pytest.raises(patch_capture.CaptureError):
        patch_capture.build_preview(
            si_plan,
            recipe=recipe,
            profile=pdk_profile,
            workspace=make_workspace(),
            cell=make_cell(),
            resolved_env={},
            workarea=tmp_path,
        )


def test_strmout_never_appears_among_the_editable_targets(recipe) -> None:
    """It renders nothing, so there is no text to edit."""

    keys = {plan.stage.value for plan in patch_capture.editable_targets(recipe)}
    assert "strmout" not in keys


# ---- capture ----------------------------------------------------------------


def _edit(text: str, addition: str) -> str:
    lines = text.splitlines(keepends=True)
    lines.insert(1, addition + "\n")
    return "".join(lines)


def test_an_unchanged_text_produces_no_patch(preview, recipe, pdk_profile) -> None:
    assert (
        patch_capture.capture(
            preview, preview.base_text, recipe=recipe, profile=pdk_profile
        )
        is None
    )


def test_an_edit_becomes_a_patch_mounted_on_the_right_file(
    preview, recipe, pdk_profile
) -> None:
    patch = patch_capture.capture(
        preview,
        _edit(preview.base_text, "; typed by hand"),
        recipe=recipe,
        profile=pdk_profile,
    )
    assert patch is not None
    assert patch.template_id == preview.template_id
    assert patch.stage == preview.plan.stage
    assert patch.base.profile_id == pdk_profile.profile_id
    assert patch.base.template_sha256 == preview.template_sha256
    assert len(patch.hunks) == 1


def test_a_captured_edit_re_applies_to_a_different_cell(
    si_plan, recipe, pdk_profile, tmp_path: Path
) -> None:
    """The whole reason the patch is masked: one stored edit, every DUT."""

    workspace = make_workspace()
    first = patch_capture.build_preview(
        si_plan,
        recipe=recipe,
        profile=pdk_profile,
        workspace=workspace,
        cell=make_cell(cell="inv"),
        resolved_env=ENV,
        workarea=tmp_path,
    )
    patch = patch_capture.capture(
        first,
        _edit(first.base_text, "; typed by hand"),
        recipe=recipe,
        profile=pdk_profile,
    )
    assert patch is not None

    other = patch_capture.build_preview(
        si_plan,
        recipe=recipe,
        profile=pdk_profile,
        workspace=workspace,
        cell=make_cell(cell="amp2", library="OTHER_LIB"),
        resolved_env=ENV,
        workarea=tmp_path,
    )
    report = apply_patch(
        other.base_text,
        patch,
        other.values,
        base_masked_text=other.masked_text,
    )
    assert not report.blocking_under(patch.on_fuzzy), [
        (r.hunk.id, r.status) for r in report.results
    ]
    assert "; typed by hand" in report.patched_text
    assert "amp2" in report.patched_text


def test_re_capturing_the_same_edit_keeps_the_hunk_id(
    preview, recipe, pdk_profile
) -> None:
    """A re-capture must not reset the UI rows or lose the user's notes."""

    edited = _edit(preview.base_text, "; typed by hand")
    first = patch_capture.capture(
        preview, edited, recipe=recipe, profile=pdk_profile
    )
    carried = patch_capture.with_patch(recipe, first)
    second = patch_capture.capture(
        preview, edited, recipe=carried, profile=pdk_profile
    )
    assert [h.id for h in second.hunks] == [h.id for h in first.hunks]


# ---- mounting the patch on the recipe --------------------------------------


def test_with_patch_adds_the_patch_and_bumps_the_timestamp(
    preview, recipe, pdk_profile
) -> None:
    patch = patch_capture.capture(
        preview,
        _edit(preview.base_text, "; typed by hand"),
        recipe=recipe,
        profile=pdk_profile,
    )
    updated = patch_capture.with_patch(recipe, patch)

    assert updated.manual_edit_count == len(patch.hunks)
    assert updated.patch_for(patch.stage, patch.template_id) is patch
    assert updated.updated_at >= recipe.updated_at
    assert recipe.patches == [], "the original is not mutated"


def test_with_patch_replaces_rather_than_appends_for_the_same_file(
    preview, recipe, pdk_profile
) -> None:
    """``Recipe`` allows one patch per (stage, template_id); appending a second
    would fail validation at save time instead of at capture time."""

    first = patch_capture.capture(
        preview,
        _edit(preview.base_text, "; one"),
        recipe=recipe,
        profile=pdk_profile,
    )
    carried = patch_capture.with_patch(recipe, first)
    second = patch_capture.capture(
        preview,
        _edit(preview.base_text, "; two"),
        recipe=carried,
        profile=pdk_profile,
    )
    final = patch_capture.with_patch(carried, second)

    assert len(final.patches) == 1
    assert final.patches[0] is second
