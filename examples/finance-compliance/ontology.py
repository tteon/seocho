"""Starter ontology for the finance-compliance usecase.

Small enough to read in one sitting (6 entities, 6 relationships) and shaped
around what a regulated-finance team actually tracks: which regulations the
company is subject to, who enforces them, what went wrong (incidents), what
is being done about it (controls), and what policies govern behavior.

Swap in domain names that match your environment — the shape is the point.
"""

from seocho import NodeDef, Ontology, P, RelDef


def build_ontology() -> Ontology:
    return Ontology(
        name="finance_compliance",
        description="Starter ontology for finance compliance knowledge graphs.",
        nodes={
            "Company": NodeDef(
                description="A regulated financial entity.",
                properties={
                    "name": P(str, unique=True),
                    "ticker": P(str),
                    "jurisdiction": P(str),
                },
            ),
            "Regulator": NodeDef(
                description="A government or industry body that enforces regulations.",
                properties={
                    "name": P(str, unique=True),
                    "jurisdiction": P(str),
                },
            ),
            "Regulation": NodeDef(
                description="A specific rule or statute that governs company behavior.",
                properties={
                    "name": P(str, unique=True),
                    "code": P(str),
                    "jurisdiction": P(str),
                },
            ),
            "ComplianceIncident": NodeDef(
                description="A reported breach, near-miss, or anomaly.",
                properties={
                    "summary": P(str, unique=True),
                    "date": P(str),
                    "severity": P(str),
                },
            ),
            "ControlEvidence": NodeDef(
                description="Evidence that a control is operating (attestation, test, log).",
                properties={
                    "summary": P(str, unique=True),
                    "date": P(str),
                    "status": P(str),
                },
            ),
            "Policy": NodeDef(
                description="An internal policy that governs company behavior.",
                properties={
                    "name": P(str, unique=True),
                    "version": P(str),
                    "effective_date": P(str),
                },
            ),
        },
        relationships={
            "SUBJECT_TO": RelDef(source="Company", target="Regulation"),
            "ENFORCED_BY": RelDef(source="Regulation", target="Regulator"),
            "REPORTED": RelDef(source="Company", target="ComplianceIncident"),
            "RELATES_TO": RelDef(source="ComplianceIncident", target="Regulation"),
            "MITIGATED_BY": RelDef(source="ComplianceIncident", target="ControlEvidence"),
            "GOVERNED_BY": RelDef(source="Company", target="Policy"),
        },
    )
