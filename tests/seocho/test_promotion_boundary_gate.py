"""Bounded-context promotion gate (seocho-6gt) — the dormant ContextMap reused
as an ontology-promotion fitness function (Fowler shift-left).

Catches concept-ownership blur at promotion time instead of as a query-time
OntologyDriftError. Default reports into promotion_note; strict_boundaries=True
hard-blocks.
"""

from __future__ import annotations

import pytest

from seocho.ontology_control_plane import (
    OntologyProfile,
    OntologyProfileRegistry,
    PromotionBoundaryError,
    check_promotion_boundaries,
)


def _profile(pid, classes, *, workspace="w", status="draft"):
    return OntologyProfile(
        profile_id=pid, workspace_id=workspace, status=status,
        ontology_candidate={"classes": [{"name": c} for c in classes]},
    )


def test_profile_concepts_check_disjoint_is_clean():
    cand = _profile("legal", ["Lawsuit", "Court"])
    existing = [_profile("finance", ["Company", "Filing"], status="approved")]
    assert check_promotion_boundaries(cand, existing) == []


def test_shared_concept_is_flagged():
    cand = _profile("legal", ["Company", "Lawsuit"])  # Company also in finance
    existing = [_profile("finance", ["Company", "Filing"], status="approved")]
    violations = check_promotion_boundaries(cand, existing)
    assert len(violations) == 1
    assert violations[0].kind == "shared_ownership"
    assert "Company" in violations[0].detail and "finance" in violations[0].detail


def test_promote_strict_blocks_boundary_blur():
    reg = OntologyProfileRegistry()
    reg.register(_profile("finance", ["Company", "Filing"], status="approved"))
    reg.register(_profile("legal", ["Company", "Lawsuit"]))
    with pytest.raises(PromotionBoundaryError):
        reg.promote("legal", workspace_id="w", strict_boundaries=True)
    # candidate was NOT approved
    assert reg.get("legal", workspace_id="w").status == "draft"


def test_promote_default_reports_but_does_not_block():
    reg = OntologyProfileRegistry()
    reg.register(_profile("finance", ["Company", "Filing"], status="approved"))
    reg.register(_profile("legal", ["Company", "Lawsuit"]))
    p = reg.promote("legal", workspace_id="w")  # non-strict
    assert p.status == "approved"
    assert "Company" in p.promotion_note and "boundary" in p.promotion_note


def test_promote_clean_profile_passes_strict():
    reg = OntologyProfileRegistry()
    reg.register(_profile("finance", ["Company", "Filing"], status="approved"))
    reg.register(_profile("hr", ["Employee", "Department"]))
    p = reg.promote("hr", workspace_id="w", strict_boundaries=True)
    assert p.status == "approved" and p.promotion_note == ""
