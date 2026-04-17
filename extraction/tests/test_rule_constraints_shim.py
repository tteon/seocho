from __future__ import annotations

import extraction.rule_constraints as legacy_rule_constraints
import seocho.rules as canonical_rules


def test_rule_constraints_shim_exports_canonical_symbols() -> None:
    assert legacy_rule_constraints.Rule is canonical_rules.Rule
    assert legacy_rule_constraints.RuleSet is canonical_rules.RuleSet
    assert legacy_rule_constraints.infer_rules_from_graph is canonical_rules.infer_rules_from_graph
    assert legacy_rule_constraints.apply_rules_to_graph is canonical_rules.apply_rules_to_graph
