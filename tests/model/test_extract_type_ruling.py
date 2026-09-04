"""Nine ``extract -type`` members the model refuses, and why refusing is right.

The user's report was one sentence: *"选了 rlck_coupled，跑完网表里一个电感都没有"*
-- picked RLCK, the run passed, the netlist has no inductor in it. Nothing was
broken. Quantus accepted the deck, wrote its outputs and exited zero; the
inductors were never asked for, because ``-ind_component`` and
``-mutual_ind_component`` appear in no template this repository ships. The same
shape sits under ``substrate_only`` and the three ``*_to_substrate`` members,
where the manual is explicit: without ``substrate_nets_file`` *no nets will be
extracted as connected to the substrate*.

A silent success is the failure mode this project exists to remove, so the
model refuses the nine rather than the form merely hiding them. Hiding alone
would not have held: a type reaches ``ExtractRule`` from YAML on disk, from an
imported deck and from Duplicate, none of which pass through a combo box.

The owner ruled on 2026-09-04, and the ruling is broader than these nine:
*"the knobs you listed for me to decide on -- honestly I do not understand any
of them, and if I do not understand them I will most likely never use them."*
Not understood is not offered. The vendor manual stopped being the ruler for
what this form draws; what the owner drives in the Quantus GUI is.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from auto_ext.catalog.spec import builtin_catalog
from auto_ext.model.recipe import (
    NOT_OFFERED_EXTRACT_TYPES,
    ExtractRule,
    ExtractType,
    offered_extract_types,
)


# ---- the refusal -------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "contract"),
    [
        ("rlck_coupled", "-ind_component"),
        ("rlck_decoupled", "-ind_component"),
        ("rlck_decoupled_to_substrate", "-ind_component"),
        ("rlc_coupled", "-ind_component"),
        ("rlc_decoupled", "-ind_component"),
        ("rlc_decoupled_to_substrate", "-ind_component"),
        ("substrate_only", "substrate_nets_file"),
        ("c_only_decoupled_to_substrate", "substrate_nets_file"),
        ("rc_decoupled_to_substrate", "substrate_nets_file"),
    ],
)
def test_a_not_offered_type_is_refused_and_names_the_missing_contract(
    kind: str, contract: str
) -> None:
    """"It ran and there are no inductors" has to become "it will not run".

    The message is the deliverable, not the exception. A refusal that said
    only "not offered" would leave a user with a recipe they cannot fix and
    no idea what would have to change; naming the contract says exactly what
    is missing and therefore what a future round would have to build.
    """

    with pytest.raises(ValidationError) as caught:
        ExtractRule(type=kind)

    message = str(caught.value)
    assert kind in message, "the refusal must quote the value it refused"
    assert contract in message, "the refusal must name the missing contract"
    assert "2026-09-04" in message, "the refusal must cite the ruling behind it"
    assert "rc_coupled" in message, "a refusal with no way forward is a dead end"


def test_the_six_offered_types_are_accepted() -> None:
    """The other half of the claim: what is offered actually works.

    A validator that refused too much would be discovered the same way the
    original defect was -- by a user, later, on a real cell.
    """

    assert [t.value for t in offered_extract_types()] == [
        "none",
        "r_only",
        "c_only_decoupled",
        "c_only_coupled",
        "rc_decoupled",
        "rc_coupled",
    ]
    for kind in offered_extract_types():
        assert ExtractRule(type=kind).type is kind


def test_the_enum_still_carries_all_fifteen_members() -> None:
    """Refusing a value and being unable to name it are different things.

    Readback has to recognise the deck a colleague wrote, the importer has to
    report what it read, and the refusal message above has to quote the member
    it is refusing. Deleting the nine from the enum would have made all three
    impossible, and would additionally have told the catalog a lie about what
    Quantus accepts.
    """

    assert len(ExtractType) == 15
    assert ExtractType("rlck_coupled") is ExtractType.RLCK_COUPLED


# ---- model and catalog say the same thing ------------------------------------


def test_the_model_and_the_catalog_hide_the_same_members() -> None:
    """Two tables, one fact -- the rule ``SELECTION_ARG_KIND`` already follows.

    The model keeps its own copy so it can validate without reading the
    catalog, which is what keeps ``auto_ext.model`` free of a runtime
    dependency on a YAML file. The price of a second copy is that something
    has to keep them equal, and this is that something.
    """

    row = builtin_catalog().option("extract_type")
    assert sorted(str(m) for m in row.choices_not_offered) == sorted(
        t.value for t in NOT_OFFERED_EXTRACT_TYPES
    )
    assert sorted(str(c) for c in row.offered_choices) == sorted(
        t.value for t in offered_extract_types()
    )
    assert row.default == "rc_coupled"
