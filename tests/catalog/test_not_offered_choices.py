"""``choices_not_offered``: the column that separates two different claims.

Dropping a member from ``choices`` says *the tool has no such value*. Listing
it in ``choices_not_offered`` says *the tool has it and we chose not to offer
it* -- and until this column existed both were the same edit, so the only way
to stop the form drawing a value was to tell the catalog a lie about the
vendor.

The lie would have cost something concrete. ``choices`` is what the importer
matches a colleague's hand-written deck against, what readback recognises, and
what an error message quotes when it refuses a value. A member deleted from
that list stops being nameable, so the deck that carries it imports as "not
one of the catalog's choices" -- which is the tool calling the vendor wrong.

The owner's ruling of 2026-09-04 is what needed the column: nine of
``extract_type``'s fifteen members are legal, documented, and produce a deck
this repository cannot complete. See
``tests/ui/test_extract_types_not_offered.py`` for the four layers that ruling
touches; this file only pins the catalog machinery underneath it.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from auto_ext.catalog.spec import Confidence, OptionSpec, OptionType, Owner, builtin_catalog


def _spec(**overrides: object) -> OptionSpec:
    """A minimal enum row, so each test states only the thing it is about."""

    fields: dict[str, object] = {
        "key": "sample_enum",
        "template_var": "sample_enum",
        "context_path": None,
        "owner": Owner.FIXED,
        "type": OptionType.ENUM,
        "choices": ["a", "b", "c"],
        "choices_confidence": Confidence.CERTAIN,
        "default": "a",
        "currently": "absent",
        "observed": False,
        "why": "a fixture row",
    }
    fields.update(overrides)
    return OptionSpec(**fields)  # type: ignore[arg-type]


# ---- the self-check ---------------------------------------------------------


def test_a_not_offered_member_without_a_reason_is_refused() -> None:
    """An exemption nobody wrote a reason for is indistinguishable from a bug.

    This is the same load-bearing rule the reachability exemption sets keep,
    one level further in: the set is only worth anything if every entry is a
    claim that a human looked at it.
    """

    with pytest.raises(ValidationError, match="needs a not_offered_reason"):
        _spec(choices_not_offered=["b"])


def test_a_reason_without_a_not_offered_member_is_refused() -> None:
    """A reason left behind after the member came back is a stale review."""

    with pytest.raises(ValidationError, match="no member is listed"):
        _spec(not_offered_reason="ruled out on some date")


def test_a_not_offered_member_that_is_not_a_choice_is_refused() -> None:
    """Not offering a value the tool does not have is a ``choices`` edit.

    Allowing it would let the column absorb typos: ``rlck_couple`` in this
    list reads as a decision and does nothing at all.
    """

    with pytest.raises(ValidationError, match="not in choices"):
        _spec(choices_not_offered=["z"], not_offered_reason="ruled out 2026-09-04")


def test_hiding_every_member_is_refused() -> None:
    """A combo with nothing in it is a broken control, not a strict one."""

    with pytest.raises(ValidationError, match="hides every member"):
        _spec(
            choices_not_offered=["a", "b", "c"],
            not_offered_reason="ruled out 2026-09-04",
        )


def test_hiding_the_rows_own_default_is_refused() -> None:
    """Otherwise a new recipe starts on a value its own form will not draw.

    The user would open the form, see a combo whose current entry is not in
    its list, and have no way back to it once they touched it.
    """

    with pytest.raises(ValidationError, match="is not offered"):
        _spec(
            default="b",
            choices_not_offered=["b"],
            not_offered_reason="ruled out 2026-09-04",
        )


def test_offered_choices_keeps_the_order_of_the_vendors_list() -> None:
    """The combo is read top to bottom; re-sorting it would move the answer.

    ``none`` first and ``rc_coupled`` last is the vendor's own ordering, from
    "extract nothing" to "everything, coupled", and it is the order an
    engineer scanning the list expects.
    """

    spec = _spec(choices_not_offered=["b"], not_offered_reason="ruled out 2026-09-04")
    assert spec.offered_choices == ["a", "c"]
    assert spec.choices == ["a", "b", "c"]


# ---- the shipped catalog ----------------------------------------------------


def test_every_shipped_not_offered_member_carries_a_reason_and_is_not_offered() -> None:
    """The catalog-wide sweep the ruling asked for.

    Two properties, both checked against the shipped table rather than a
    fixture: every hidden member has a written reason, and no member is
    claimed to be both offered and not offered. The second cannot happen
    through ``offered_choices`` alone -- it is asserted because the day
    somebody computes the offered list a second way somewhere else, this is
    the test that notices.
    """

    hiding = [opt for opt in builtin_catalog().options if opt.choices_not_offered]
    assert hiding, (
        "no shipped row hides a member any more. If the ruling was reversed "
        "that is fine, but this file and UX_VALIDATION.md section 5.5 have to "
        "say so."
    )
    for opt in hiding:
        assert len((opt.not_offered_reason or "").strip()) >= 20, (
            f"{opt.key}: choices_not_offered without a real reason"
        )
        assert "2026-09-04" in (opt.not_offered_reason or ""), (
            f"{opt.key}: the reason does not name the ruling it rests on"
        )
        overlap = {str(m) for m in opt.choices_not_offered} & {
            str(c) for c in opt.offered_choices
        }
        assert overlap == set(), f"{opt.key}: {overlap} is both offered and not offered"
