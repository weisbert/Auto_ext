"""The acceptance gate for the template parameterisation round.

One claim, tested five ways: **rendering the five targets from catalog
defaults must produce exactly the same bytes before and after a literal in a
``.j2`` is replaced by a ``[[placeholder]]``.** Byte-identical output is the
only cheap proof of the two things that can go wrong when a placeholder is
inserted -- it went into the wrong slot, or the value now flowing through it is
not the value the template used to write -- and it catches both at once,
including the whitespace failures this project's Jinja settings make so easy:
``trim_blocks`` is off, so a ``[% endif %]`` on a line of its own adds a blank
line that no assertion about "the right value is present" would ever notice.

The baseline lives in ``tests/fixtures/golden/`` and was captured *before* any
template was touched. It is never refreshed automatically. To refresh it on
purpose, from the repository root::

    python -m tests.catalog.test_byte_fidelity --refresh

and commit the result together with whatever change made the output move, so
the diff shows what the tool actually emits differently now. A refresh during
the parameterisation round means the round failed: the whole point is that the
bytes do not move.

What the fixture renders
------------------------
A pinned Recipe, PdkProfile, DUT and run, all defined in this module rather
than imported from ``tests.support.v2``. The baseline has to stay still while
four other work streams edit templates and the catalog, and a shared builder
that someone widens for an unrelated test would move it silently.

Two halves of the input are read *from the catalog* rather than typed here, and
for the same reason in both cases -- they must be what a default install has,
or the comparison stops meaning anything:

* the Recipe comes from :func:`~auto_ext.model.recipe.recipe_from_catalog`, so
  every recipe-owned field holds today's effective value;
* the profile's ``power_names`` / ``ground_names`` come from the catalog rows
  of the same name. ``PdkProfile`` defaults those two to empty lists, and an
  empty list reads as "not stated" to
  :func:`~auto_ext.core.render.check_representable`; it would therefore pass
  the pre-parameterisation render and then write an empty
  ``*lvsPowerNames:`` line the moment the row is parameterised. That is a
  fixture bug wearing the costume of a template bug, so it is excluded here.
"""

from __future__ import annotations

import difflib
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from auto_ext.catalog import builtin_catalog
from auto_ext.core import render
from auto_ext.core.env import discover_required_vars
from auto_ext.model.common import RenderTarget
from auto_ext.model.pdk import (
    CornerSpec,
    LvsDeckSet,
    LvsDeckVariant,
    PdkProfile,
    QrcDeck,
)
from auto_ext.model.recipe import OutputKind, Recipe, recipe_from_catalog
from auto_ext.model.run import DutSnapshot

#: Where the baseline lives. Derived from ``__file__``, never from cwd: the
#: suite runs with cwd inside the repository and a cwd-relative lookup can find
#: the right file for the wrong reason.
GOLDEN_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "golden"

#: Root of the fictional shared filesystem the env values describe. Fictional
#: on purpose: these strings are substituted into tool input files and nothing
#: ever opens them, so the baseline must not carry a real machine's paths.
WORK = "/w"

#: A resolved environment satisfying every path expression below.
ENV: dict[str, str] = {
    "WORK_ROOT": WORK,
    "WORK_ROOT2": WORK,
    "VERIFY_ROOT": f"{WORK}/fake/verify",
    "SETUP_ROOT": f"{WORK}/fake/setup",
    "PDK_LAYER_MAP_FILE": f"{WORK}/fake/layers.map",
    "calibre_source_added_place": (
        f"{WORK}/fake/runset/Calibre_QRC/LVS/Ver_Plus_1.0l_0.9/CFXXX/empty.cdl"
    ),
}


def golden_profile() -> PdkProfile:
    """The PDK half of the baseline: one technology, everything else default.

    Every field the templates still hardcode is left at its model default so
    the declared value matches the catalog default -- which *is* the literal in
    the template. The two supply tables are the exception explained in the
    module docstring.
    """

    catalog = builtin_catalog()
    return PdkProfile(
        profile_id="hn001",
        display_name="HN001 22nm",
        tech_name="HN001",
        lvs_decks=LvsDeckSet(
            dir_expr="$calibre_source_added_place|parent",
            variants=[LvsDeckVariant(name="wodio", rules_suffix="wodio")],
            default_variant="wodio",
            runset_version="Ver_Plus_1.0l_0.9",
        ),
        qrc=QrcDeck(
            dir_expr=(
                "$VERIFY_ROOT/runset/Calibre_QRC/QRC/Ver_Plus_1.0a/CFXXX/QCI_deck"
            ),
            runset_version="Ver_Plus_1.0a",
        ),
        corners=[
            CornerSpec(
                name="typical",
                technology_corner="TYPICAL",
                default_temperature_c=55.0,
                aliases=["nominal"],
            )
        ],
        default_corner="typical",
        power_names=list(catalog.option("power_names").default),
        ground_names=list(catalog.option("ground_names").default),
    )


def golden_recipe() -> Recipe:
    """Catalog defaults, emitting both Quantus forms so all five files render."""

    return recipe_from_catalog(
        recipe_id="golden-baseline",
        name="Golden baseline",
        output={"emit": [OutputKind.EXTRACTED_VIEW, OutputKind.DSPF]},
    )


def golden_dut() -> DutSnapshot:
    """The DUT half: one cell, spelled the same way the rest of the suite does."""

    return DutSnapshot(
        library="WB_PLL_DCO",
        cell="inv",
        layout_view="layout",
        source_view="schematic",
        ground_net="vss",
        out_file="av_ext",
    )


def golden_run() -> render.RunFacts:
    """Run facts with a frozen clock and no machine-local path anywhere."""

    return render.RunFacts(
        run_id="20260821T143205Z_inv-rc",
        run_slug="inv-rc",
        run_dir=Path(f"{WORK}/runs/20260821T143205Z_inv-rc"),
        workarea=Path(f"{WORK}/workarea"),
        output_dir=f"{WORK}/cds/verify/QCI_PATH_inv",
        intermediate_dir=WORK,
        dspf_out_path=f"{WORK}/inv.dspf",
        started_at=datetime(2026, 8, 21, 14, 32, 5, tzinfo=timezone.utc),
        stages=("si", "calibre", "quantus", "jivaro"),
    )


def render_all(*, templates_root: Path | None = None) -> dict[RenderTarget, str]:
    """Render every target from the pinned inputs, in memory.

    ``write=False``: the comparison is against the text the renderer produced,
    not against a file it wrote. On Windows ``Path.write_text`` translates every
    ``\\n`` into ``\\r\\n``, so a disk round trip would compare the platform's
    newline habits rather than the template's content.
    """

    recipe = golden_recipe()
    profile = golden_profile()
    context = render.build_context(
        dut=golden_dut(),
        recipe=recipe,
        profile=profile,
        run=golden_run(),
        resolved_env=ENV,
    )
    out: dict[RenderTarget, str] = {}
    for plan in render.plan_targets(recipe):
        rendered = render.render_one(
            plan,
            context=context,
            recipe=recipe,
            profile=profile,
            resolved_env=ENV,
            out_dir=Path("unused"),
            templates_root=templates_root,
            write=False,
        )
        out[plan.target] = rendered.text
    return out


def golden_path(target: RenderTarget) -> Path:
    """Baseline file for one target, named after the artifact the run writes."""

    return GOLDEN_DIR / render.RENDERED_FILENAMES[target]


def read_golden(target: RenderTarget) -> str:
    """The baseline, byte for byte, with no newline translation."""

    return golden_path(target).read_bytes().decode("utf-8")


def write_golden(text: str, target: RenderTarget) -> None:
    """Write one baseline with LF endings on every platform."""

    path = golden_path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def refresh() -> list[RenderTarget]:
    """Regenerate every baseline. Returns the targets whose bytes moved."""

    moved: list[RenderTarget] = []
    for target, text in render_all().items():
        path = golden_path(target)
        previous = path.read_bytes() if path.is_file() else None
        write_golden(text, target)
        if previous != text.encode("utf-8"):
            moved.append(target)
    return moved


def diff(expected: str, actual: str, target: RenderTarget) -> str:
    """A unified diff naming the exact lines that moved."""

    lines = list(
        difflib.unified_diff(
            expected.splitlines(keepends=True),
            actual.splitlines(keepends=True),
            fromfile=f"golden/{render.RENDERED_FILENAMES[target]}",
            tofile=f"rendered/{render.RENDERED_FILENAMES[target]}",
        )
    )
    return "".join(lines) or "(no line differs; the two texts differ in bytes only)"


# ---- the tests ---------------------------------------------------------------


ALL_TARGETS = list(RenderTarget)


@pytest.fixture(scope="module")
def rendered() -> dict[RenderTarget, str]:
    return render_all()


def test_the_baseline_covers_every_render_target() -> None:
    """A missing baseline is a silently unguarded template, so it fails here."""

    missing = [t.value for t in ALL_TARGETS if not golden_path(t).is_file()]
    assert not missing, (
        f"no golden sample for {missing}. Capture the baseline with "
        "`python -m tests.catalog.test_byte_fidelity --refresh` -- but only "
        "before the templates are edited, never after."
    )


@pytest.mark.parametrize("target", ALL_TARGETS, ids=[t.value for t in ALL_TARGETS])
def test_rendering_from_catalog_defaults_is_byte_identical_to_the_baseline(
    rendered: dict[RenderTarget, str], target: RenderTarget
) -> None:
    """The acceptance criterion of the parameterisation round, in one line.

    A failure here means one of exactly three things, in decreasing order of
    likelihood: the placeholder went into the wrong place, the value bound to
    it is not the literal it replaced, or a ``[% %]`` tag was written on a line
    of its own and ``trim_blocks`` (which is off) left the newline behind.
    """

    expected = read_golden(target)
    actual = rendered[target]
    assert actual == expected, (
        f"{target.value} no longer renders byte-identically from catalog "
        f"defaults:\n{diff(expected, actual, target)}"
    )


@pytest.mark.parametrize("target", ALL_TARGETS, ids=[t.value for t in ALL_TARGETS])
def test_the_baseline_uses_lf_endings_only(target: RenderTarget) -> None:
    """CRLF in a baseline would make the comparison test the checkout, not the
    template. ``.gitattributes`` forces LF; this is the assertion behind it."""

    raw = golden_path(target).read_bytes()
    assert b"\r" not in raw, f"{golden_path(target)} carries CR bytes"


@pytest.mark.parametrize("target", ALL_TARGETS, ids=[t.value for t in ALL_TARGETS])
def test_no_env_reference_survives_into_the_baseline(target: RenderTarget) -> None:
    """``si`` and ``jivaro`` do not expand ``$VAR`` inside a string value, so a
    surviving reference is a wrong file rather than an unresolved one."""

    assert discover_required_vars([read_golden(target)]) == set()


def test_the_baseline_holds_no_machine_local_path() -> None:
    """Everything in it must come from :data:`ENV` and the pinned run facts.

    A Windows drive letter or a pytest ``tmp_path`` in the baseline would make
    the comparison pass or fail depending on whose checkout ran it.
    """

    for target in ALL_TARGETS:
        text = read_golden(target)
        for line in text.splitlines():
            assert ":\\" not in line and ":/" not in line.replace("://", ""), (
                f"{golden_path(target).name} carries what looks like an absolute "
                f"host path: {line!r}"
            )


def test_rendering_twice_gives_the_same_bytes() -> None:
    """No clock, no dict ordering, no environment read leaks into the output."""

    assert render_all() == render_all()


def test_the_baseline_is_what_a_run_would_write(tmp_path: Path) -> None:
    """The in-memory text and the on-disk artifact agree once newline
    translation is taken out of the picture.

    Guards the one assumption :func:`render_all` makes by rendering with
    ``write=False``: that the text it compares is the text a real run puts in
    ``runs/<id>/rendered/``.
    """

    recipe = golden_recipe()
    profile = golden_profile()
    context = render.build_context(
        dut=golden_dut(),
        recipe=recipe,
        profile=profile,
        run=golden_run(),
        resolved_env=ENV,
    )
    for plan in render.plan_targets(recipe):
        written = render.render_one(
            plan,
            context=context,
            recipe=recipe,
            profile=profile,
            resolved_env=ENV,
            out_dir=tmp_path / "rendered",
            write=True,
        )
        on_disk = written.out_path.read_bytes().decode("utf-8").replace("\r\n", "\n")
        assert on_disk == read_golden(plan.target)


def test_the_five_baselines_are_named_after_their_artifacts() -> None:
    """``si.env`` and not ``default.env``: the directory describes the run's
    output, not the template that happened to make it."""

    assert sorted(p.name for p in GOLDEN_DIR.iterdir() if p.is_file()) == sorted(
        render.RENDERED_FILENAMES[t] for t in ALL_TARGETS
    )


# ---- refresh entry point -----------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """``python -m tests.catalog.test_byte_fidelity [--refresh|--check]``."""

    import argparse

    parser = argparse.ArgumentParser(
        description="Capture or compare the byte-fidelity baseline."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--refresh", action="store_true", help="overwrite the golden samples"
    )
    group.add_argument(
        "--check",
        action="store_true",
        help="compare and print a diff, writing nothing (the default)",
    )
    args = parser.parse_args(argv)

    if args.refresh:
        moved = refresh()
        print(f"wrote {len(ALL_TARGETS)} golden sample(s) to {GOLDEN_DIR.as_posix()}")
        for target in moved:
            print(f"  changed: {render.RENDERED_FILENAMES[target]}")
        if not moved:
            print("  (no baseline moved)")
        return 0

    failures = 0
    for target, text in render_all().items():
        path = golden_path(target)
        if not path.is_file():
            print(f"MISSING {path}")
            failures += 1
            continue
        expected = read_golden(target)
        if expected == text:
            print(f"ok      {render.RENDERED_FILENAMES[target]}")
            continue
        failures += 1
        print(f"DIFFERS {render.RENDERED_FILENAMES[target]}")
        print(diff(expected, text, target))
    return 1 if failures else 0


if __name__ == "__main__":  # pragma: no cover - manual tool
    sys.exit(main())
