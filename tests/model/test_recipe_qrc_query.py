"""``lvs.run_qrc_query`` off + a staged ``quantus`` is not a representable recipe.

The Calibre runset's post-trigger that runs ``calibre -query_input ... -query
svdb`` is what fills ``<output_dir>/query_output``. Both Quantus decks read
that directory unconditionally -- ``input_db -directory_name``, ``-layer_map_file
.../Design.gds.map``, ``-device_properties_file .../Design.props``. Turn the
trigger off and leave quantus in the stage list and Quantus starts, looks for
three files nobody wrote, and dies there, after si and Calibre have already
been paid for.

The knob exists for a reason -- an LVS-only recipe should not pay for the
query_output dump -- so the combination is refused rather than the knob, and
the refusal names the knob so the message says which of the two to change.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from auto_ext.model.recipe import Recipe


def _recipe(**overrides: object) -> Recipe:
    fields: dict[str, object] = {"recipe_id": "lvs-only", "name": "LVS only"}
    fields.update(overrides)
    return Recipe(**fields)  # type: ignore[arg-type]


def test_staging_quantus_with_the_qrc_query_off_is_refused() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _recipe(
            stages=["si", "strmout", "calibre", "quantus"],
            lvs={"run_qrc_query": False},
        )

    message = str(excinfo.value)
    assert "run_qrc_query" in message, "the message must name the knob to change"
    assert "quantus" in message
    assert "query_output" in message


def test_the_query_may_be_turned_off_when_nothing_extracts() -> None:
    """The intended use of the knob keeps working: LVS and nothing after it."""

    recipe = _recipe(
        stages=["si", "strmout", "calibre"], lvs={"run_qrc_query": False}
    )
    assert recipe.lvs.run_qrc_query is False


def test_quantus_is_fine_with_the_query_on() -> None:
    recipe = _recipe(stages=["si", "strmout", "calibre", "quantus"])
    assert recipe.lvs.run_qrc_query is True
