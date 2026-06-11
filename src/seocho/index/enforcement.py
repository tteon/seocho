"""Closed-vocabulary ontology enforcement semantics (seocho-snt).

One :class:`EnforcementPolicy` value drives every enforcement decision in the
indexing path, replacing the single ``strict_validation`` boolean while
remaining back-compatible with it.

The three presets follow the ontology-panel design:

- ``strict`` — closed vocabulary. Unknown labels/relationship types,
  dangling endpoints, and domain/range violations reject the whole chunk
  (no silent element drops). The relaxed retry and the heuristic
  ``Entity``/``MENTIONS`` fallback are disabled because both emit
  out-of-vocabulary structure. A constant, ontology-independent prompt
  line instructs the model to stay inside the provided vocabulary — it
  must stay ontology-independent so the extraction firewall
  (``to_extraction_context`` byte-identity, r=-0.76 driver) holds.
- ``guided`` — today's default behavior: validation errors are recorded
  but do not reject, relaxed retry and fallback stay available.
- ``open`` — like guided, but out-of-ontology elements are annotated with
  ``_out_of_ontology: true`` instead of being treated as errors, so
  downstream consumers can filter or study them.

Run-level failure thresholds intentionally live in the e2e runner
(``seocho run``), not here; the rejection unit at this layer is the chunk.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

__all__ = ["EnforcementPolicy", "STRICT", "GUIDED", "OPEN", "resolve_enforcement"]


@dataclass(frozen=True)
class EnforcementPolicy:
    """Resolved enforcement semantics for one indexing run."""

    mode: str
    closed_vocabulary: bool
    allow_relaxed_retry: bool
    allow_fallback_extract: bool
    annotate_out_of_ontology: bool
    closed_vocab_prompt_line: bool
    revalidate_after_link: bool
    reject_on_validation_errors: bool


STRICT = EnforcementPolicy(
    mode="strict",
    closed_vocabulary=True,
    allow_relaxed_retry=False,
    allow_fallback_extract=False,
    annotate_out_of_ontology=False,
    closed_vocab_prompt_line=True,
    revalidate_after_link=True,
    reject_on_validation_errors=True,
)

GUIDED = EnforcementPolicy(
    mode="guided",
    closed_vocabulary=False,
    allow_relaxed_retry=True,
    allow_fallback_extract=True,
    annotate_out_of_ontology=False,
    closed_vocab_prompt_line=False,
    revalidate_after_link=False,
    reject_on_validation_errors=False,
)

OPEN = EnforcementPolicy(
    mode="open",
    closed_vocabulary=False,
    allow_relaxed_retry=True,
    allow_fallback_extract=True,
    annotate_out_of_ontology=True,
    closed_vocab_prompt_line=False,
    revalidate_after_link=False,
    reject_on_validation_errors=False,
)

_PRESETS = {"strict": STRICT, "guided": GUIDED, "open": OPEN}


def resolve_enforcement(
    value: Any = None,
    *,
    strict_validation: Optional[bool] = None,
) -> EnforcementPolicy:
    """Resolve a policy from a preset name, a policy, or the legacy flag.

    Precedence: an explicit ``value`` (policy instance or preset name) wins;
    otherwise the legacy ``strict_validation`` boolean maps to
    ``strict``/``guided``; with neither, the default is ``guided``.
    """
    if isinstance(value, EnforcementPolicy):
        return value
    if isinstance(value, str) and value.strip():
        name = value.strip().lower()
        if name not in _PRESETS:
            raise ValueError(
                f"Unknown enforcement mode {value!r}; "
                f"expected one of: {', '.join(sorted(_PRESETS))}."
            )
        return _PRESETS[name]
    if value is not None:
        raise TypeError(
            f"enforcement must be a str preset or EnforcementPolicy, got {type(value).__name__}"
        )
    if strict_validation:
        return STRICT
    return GUIDED
