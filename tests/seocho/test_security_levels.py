"""Palantir-style layered security: dataset -> row -> cell -> sub-cell (organ 3)."""

from __future__ import annotations

from seocho.security_levels import (
    SecurityPolicy,
    dataset_visible,
    filter_array_elements,
    row_visible,
    visible,
)


def test_lattice_default_deny():
    assert visible("public", "internal") and visible("internal", "internal")
    assert not visible("secret", "internal")
    assert not visible(None, "internal")          # unknown -> restricted -> denied
    assert visible("restricted", "restricted") and visible("restricted", "secret")


def test_level0_dataset():
    assert dataset_visible("acme", "acme")
    assert not dataset_visible("acme", "globex")


def test_row_wise_osp_drops_denied_row():
    assert row_visible("internal", "internal")
    assert not row_visible("secret", "internal")   # a denied record is invisible (dropped)


def test_subcell_derived_property_filters_array_elements():
    """The Palantir sub-cell: one sensitive note inside a list is locked away from a
    lower-clearance principal, the rest of the list still returned."""
    notes = ["intake summary", "billing note", "HIV status note"]
    sens = ["public", "internal", "secret"]
    # general staff (internal) sees the first two, NOT the secret element
    assert filter_array_elements(notes, sens, "internal") == ["intake summary", "billing note"]
    # compliance (secret) sees all
    assert filter_array_elements(notes, sens, "secret") == notes
    # unlabeled element defaults-DENY (restricted): hidden from internal
    assert filter_array_elements(["a", "b"], ["public"], "internal") == ["a"]


def test_policy_composes_all_four_levels():
    policy = SecurityPolicy(
        row_sensitivity="internal",
        property_sensitivity={"name": "public", "ssn": "secret", "notes": "public"},
        array_element_sensitivity={"notes": ["public", "secret"]},
    )
    rec = {"name": "Jane", "ssn": "123-45-6789", "notes": ["ok to share", "sensitive"]}

    # internal principal: row visible; ssn cell masked; notes sub-cell filtered
    out, red = policy.apply(rec, clearance="internal")
    assert out == {"name": "Jane", "notes": ["ok to share"]}
    assert "cell:ssn" in red and any(r.startswith("subcell:notes") for r in red)

    # secret principal: everything
    out2, red2 = policy.apply(rec, clearance="secret")
    assert out2 == rec and red2 == []


def test_policy_row_drop_returns_none():
    policy = SecurityPolicy(row_sensitivity="secret")
    out, red = policy.apply({"x": 1}, clearance="internal")
    assert out is None and red == ["row:secret"]
