"""Publish-time compatibility gate (seocho-ia4.2).

The classifier (``classify_ontology_change``, ADR-0177) says HOW a new ontology
version differs from the current one. This gate ACTS on it at publish time: a version
whose change violates the package's declared compatibility mode is refused, unless the
author explicitly acknowledges a breaking bump. Today ``register``/``save`` has no such
check — a breaking change that invalidates all existing data can be published silently
(the silent-breaking / "strict but stale" failure). This is the schema-registry
"compatibility check before publish" for ontologies.

The classifier's verdict also *derives* the read-time drift policy (``derive_drift_policy``),
closing the loop with the ia4.1 barrier: a BACKWARD bump only warrants ``warn`` on
drift; a BREAKING one warrants ``block`` until the graph is re-projected.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .compatibility import classify_ontology_change

# schema-registry-style modes
MODES = ("BACKWARD", "FORWARD", "FULL", "NONE")


class PublishCompatibilityError(RuntimeError):
    """Raised when a version publish violates the package's compatibility mode."""

    def __init__(self, report: Dict[str, Any]) -> None:
        self.report = dict(report)
        super().__init__(
            f"Incompatible ontology publish under mode={report.get('mode')!r}: "
            f"change is {report.get('overall')!r} "
            f"(breaking labels: {report.get('breaking_labels')}). "
            f"Bump MAJOR and attach a migration, or pass allow_breaking=True."
        )


def check_publish_compatibility(
    prior: Optional[Any],
    new_ontology: Any,
    *,
    mode: str = "BACKWARD",
) -> Dict[str, Any]:
    """Classify ``prior -> new_ontology`` and decide if the publish is allowed.

    ``prior`` is the current ontology (or its ``to_dict``) or None (first version,
    always allowed). Returns a report with ``allowed`` and the classifier verdict.
    """
    mode = str(mode).upper()
    if mode not in MODES:
        mode = "BACKWARD"
    if prior is None:
        return {"allowed": True, "mode": mode, "overall": "NONE",
                "breaking_labels": [], "reason": "first version"}

    report = classify_ontology_change(prior, new_ontology)
    overall = report.overall
    # what each mode forbids:
    #  BACKWARD: old data must stay valid -> forbid BREAKING
    #  FORWARD : old readers must tolerate new data -> forbid BREAKING or FORWARD
    #  FULL    : forbid anything not BACKWARD
    #  NONE    : allow all
    forbidden = {
        "BACKWARD": overall == "BREAKING",
        "FORWARD": overall in {"BREAKING", "FORWARD"},
        "FULL": overall in {"BREAKING", "FORWARD"},
        "NONE": False,
    }[mode]
    return {
        "allowed": not forbidden,
        "mode": mode,
        "overall": overall,
        "breaking_labels": sorted(report.breaking_labels),
        "breaking_properties": sorted(f"{a}.{b}" for a, b in report.breaking_properties),
        "atoms": [f"{a.kind}:{a.label}.{a.prop}={a.compatibility}" for a in report.atoms],
        "reason": "compatible" if not forbidden else f"{overall} change violates {mode}",
    }


def derive_drift_policy(report: Dict[str, Any]) -> str:
    """The read-time drift policy implied by a compatibility verdict (ties the
    publish gate to the ia4.1 barrier): BREAKING/FORWARD -> 'block', else 'warn'."""
    return "block" if report.get("overall") in {"BREAKING", "FORWARD"} else "warn"
