"""
Re-export shim — canonical implementation lives in ``seocho.rules``.

All public names are re-exported so that existing ``from rule_constraints
import ...`` statements in the extraction layer continue to work.
"""

from seocho.rules import (  # noqa: F401
    Rule,
    RuleSet,
    apply_rules_to_graph,
    infer_rules_from_graph,
)

__all__ = [
    "Rule",
    "RuleSet",
    "apply_rules_to_graph",
    "infer_rules_from_graph",
]
