"""Regression tests for seocho-mcj0 — ontology drift policy enforcement.

Background: ``assess_ontology_context_mismatch`` returns
``{'mismatch': True, ...}`` when the active context_hash doesn't match
the hashes already in the graph, but every caller used to just log a
warning and proceed. ``enforce_drift_policy`` centralises the decision
so callers can opt into 'raise' or 'block' policies (per
seocho-cimb's contract).
"""

from __future__ import annotations

import logging

import pytest


def _mismatch_assessment() -> dict:
    return {
        "active_context_hash": "abc123",
        "indexed_context_hashes": ["xyz789"],
        "mismatch": True,
        "missing_context_nodes": 0,
        "scoped_nodes": 100,
        "warning": "drift detected",
    }


def _clean_assessment() -> dict:
    return {
        "active_context_hash": "abc123",
        "indexed_context_hashes": ["abc123"],
        "mismatch": False,
        "missing_context_nodes": 0,
        "scoped_nodes": 100,
        "warning": "",
    }


def test_warn_policy_returns_assessment_unchanged_back_compat() -> None:
    """Default policy='warn' just logs + returns; no exception."""
    from seocho.ontology_context import enforce_drift_policy
    out = enforce_drift_policy(_mismatch_assessment())
    assert out["drift_policy"] == "warn"
    assert out["enforced"] is False
    assert out["blocked"] is False


def test_warn_policy_emits_log_when_logger_passed(caplog) -> None:
    from seocho.ontology_context import enforce_drift_policy
    log = logging.getLogger("seocho.test_drift_warn")
    with caplog.at_level(logging.WARNING, logger=log.name):
        enforce_drift_policy(_mismatch_assessment(), policy="warn", logger_obj=log)
    assert any("drift detected" in r.message for r in caplog.records)


def test_raise_policy_throws_on_mismatch() -> None:
    from seocho.ontology_context import OntologyDriftError, enforce_drift_policy
    with pytest.raises(OntologyDriftError) as ei:
        enforce_drift_policy(_mismatch_assessment(), policy="raise")
    assert "abc123" in str(ei.value)
    assert "xyz789" in str(ei.value)
    # The full assessment is preserved on the exception
    assert ei.value.assessment["mismatch"] is True


def test_block_policy_returns_blocked_marker_no_raise() -> None:
    from seocho.ontology_context import enforce_drift_policy
    out = enforce_drift_policy(_mismatch_assessment(), policy="block")
    assert out["drift_policy"] == "block"
    assert out["enforced"] is True
    assert out["blocked"] is True
    # Caller can use these to return an HTTP 409 etc.


def test_no_mismatch_short_circuits_for_any_policy() -> None:
    """When mismatch=False, every policy returns the assessment cleanly."""
    from seocho.ontology_context import enforce_drift_policy
    for pol in ("warn", "raise", "block"):
        out = enforce_drift_policy(_clean_assessment(), policy=pol)
        assert out["mismatch"] is False
        assert out["enforced"] is False
        assert out["blocked"] is False


def test_seocho_top_level_reexports() -> None:
    """OntologyDriftError + enforce_drift_policy are exposed on seocho.*."""
    import seocho
    assert hasattr(seocho, "OntologyDriftError")
    assert hasattr(seocho, "enforce_drift_policy")
