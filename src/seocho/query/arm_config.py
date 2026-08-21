"""Arm configuration for the governed-memory ablation (agent-OS organs as flags).

The structured runtime's five organs are each an independent runtime flag, so an
experiment arm is just a point in that flag space. BARE = all off, GOVERNED = all
on, and the five leave-one-outs isolate each organ's marginal contribution. This is
the arm×organ matrix (wiki/e2e-redesign-arm-organ-matrix.md) made executable — the
orchestrator reads these flags, nothing is hard-wired per arm.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import List

# The organ names, in the order they are reported.
ORGANS = ("intern", "schema", "pin", "workspace", "guardrail")


@dataclass(frozen=True)
class ArmConfig:
    """Which governed-memory organs are ON for this arm."""

    intern: bool = True            # canonical address space (shared intern pool vs vector resolve)
    schema_source: str = "pinned"  # "pinned" (resolver) | "introspected" (real DB get_schema)
    pin: bool = True               # RCU version isolation (frozen ontology version)
    workspace_enforce: bool = True # access isolation (enforce_workspace_filter + forced ws)
    guardrail: bool = True         # query safety (reject schema-violating / unscoped Cypher)
    name: str = "governed"

    # -- presets --------------------------------------------------------------
    @classmethod
    def governed(cls) -> "ArmConfig":
        return cls(name="governed")

    @classmethod
    def bare(cls) -> "ArmConfig":
        """A real, competent bare multi-agent RAG — NOT a strawman: introspected
        schema, vector resolve, DB-native MVCC only, software-only workspace, no
        guardrail."""
        return cls(intern=False, schema_source="introspected", pin=False,
                   workspace_enforce=False, guardrail=False, name="bare")

    def without(self, organ: str) -> "ArmConfig":
        """GOVERNED with exactly one organ turned OFF (leave-one-out)."""
        if organ not in ORGANS:
            raise ValueError(f"unknown organ {organ!r}; expected one of {ORGANS}")
        base = ArmConfig.governed()
        flip = {
            "intern": {"intern": False},
            "schema": {"schema_source": "introspected"},
            "pin": {"pin": False},
            "workspace": {"workspace_enforce": False},
            "guardrail": {"guardrail": False},
        }[organ]
        return replace(base, name=f"governed-no-{organ}", **flip)

    def organs_on(self) -> List[str]:
        return [
            o for o, on in (
                ("intern", self.intern),
                ("schema", self.schema_source == "pinned"),
                ("pin", self.pin),
                ("workspace", self.workspace_enforce),
                ("guardrail", self.guardrail),
            ) if on
        ]

    def to_dict(self) -> dict:
        return {"name": self.name, "intern": self.intern,
                "schema_source": self.schema_source, "pin": self.pin,
                "workspace_enforce": self.workspace_enforce, "guardrail": self.guardrail}


def ablation_arms() -> List[ArmConfig]:
    """The principled, power-aware arm set: BARE, GOVERNED, and the five
    leave-one-outs (NOT the 2^5 grid)."""
    arms = [ArmConfig.bare(), ArmConfig.governed()]
    arms += [ArmConfig.governed().without(o) for o in ORGANS]
    return arms
