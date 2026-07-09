from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Optional


@dataclass(slots=True)
class EntityCurationPolicy:
    identity_keys: list[str] = field(default_factory=list)
    fallback_identity_keys: list[str] = field(default_factory=list)
    property_merge: Dict[str, str] = field(default_factory=dict)
    auto_merge_threshold: float = 0.9


@dataclass(slots=True)
class RelationshipCurationPolicy:
    endpoint_types: list[str] = field(default_factory=list)
    relation_identity_keys: list[str] = field(default_factory=list)
    qualifier_merge: str = "review_if_conflict"
    duplicate_policy: str = "same_endpoints_and_same_qualifiers"


@dataclass(slots=True)
class PromotionRules:
    min_total_score: float = 0.86
    require_ontology_compatibility: bool = True
    require_provenance_count: int = 1
    block_on_cardinality_violation: bool = True


@dataclass(slots=True)
class CurationDesignSpec:
    name: str = "default-curation"
    entity_policies: Dict[str, EntityCurationPolicy] = field(default_factory=dict)
    relationship_policies: Dict[str, RelationshipCurationPolicy] = field(default_factory=dict)
    promotion_rules: PromotionRules = field(default_factory=PromotionRules)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "entity_policies": {
                key: asdict(value)
                for key, value in self.entity_policies.items()
            },
            "relationship_policies": {
                key: asdict(value)
                for key, value in self.relationship_policies.items()
            },
            "promotion_rules": asdict(self.promotion_rules),
        }

    @property
    def design_hash(self) -> str:
        payload = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def get_entity_policy(self, label: str, *, ontology: Optional[Any] = None) -> EntityCurationPolicy:
        policy = self.entity_policies.get(str(label))
        if policy is not None:
            return policy
        identity_keys: list[str] = []
        fallback_identity_keys: list[str] = []
        property_merge: Dict[str, str] = {"aliases": "set_union"}
        if ontology is not None:
            node_def = getattr(ontology, "nodes", {}).get(str(label))
            if node_def is not None:
                identity_keys = list(getattr(node_def, "unique_properties", []) or [])
                fallback_identity_keys = list(getattr(node_def, "required_properties", []) or [])
                for key in identity_keys:
                    property_merge.setdefault(key, "authoritative")
                for key in fallback_identity_keys:
                    property_merge.setdefault(key, "prefer_non_empty")
        if "name" not in fallback_identity_keys:
            fallback_identity_keys.append("name")
        return EntityCurationPolicy(
            identity_keys=identity_keys,
            fallback_identity_keys=fallback_identity_keys,
            property_merge=property_merge,
            auto_merge_threshold=self.promotion_rules.min_total_score,
        )

    def get_relationship_policy(
        self,
        rel_type: str,
        *,
        ontology: Optional[Any] = None,
    ) -> RelationshipCurationPolicy:
        policy = self.relationship_policies.get(str(rel_type))
        if policy is not None:
            return policy
        endpoint_types: list[str] = []
        if ontology is not None:
            rel_def = getattr(ontology, "relationships", {}).get(str(rel_type))
            if rel_def is not None:
                endpoint_types = [
                    str(getattr(rel_def, "source", "") or ""),
                    str(getattr(rel_def, "target", "") or ""),
                ]
        return RelationshipCurationPolicy(endpoint_types=endpoint_types)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CurationDesignSpec":
        entity_policies = {
            str(key): EntityCurationPolicy(**dict(value))
            for key, value in dict(payload.get("entity_policies", {})).items()
            if isinstance(value, Mapping)
        }
        relationship_policies = {
            str(key): RelationshipCurationPolicy(**dict(value))
            for key, value in dict(payload.get("relationship_policies", {})).items()
            if isinstance(value, Mapping)
        }
        promotion_payload = payload.get("promotion_rules", {})
        promotion_rules = PromotionRules(**dict(promotion_payload)) if isinstance(promotion_payload, Mapping) else PromotionRules()
        return cls(
            name=str(payload.get("name", "default-curation")),
            entity_policies=entity_policies,
            relationship_policies=relationship_policies,
            promotion_rules=promotion_rules,
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> "CurationDesignSpec":
        import yaml

        with open(path, "r", encoding="utf-8") as f:
            payload = yaml.safe_load(f) or {}
        if not isinstance(payload, Mapping):
            raise ValueError("Curation design YAML must decode to a mapping.")
        return cls.from_dict(payload)


def load_curation_design_spec(
    value: Optional["CurationDesignSpec | Mapping[str, Any] | str | Path"],
) -> CurationDesignSpec:
    if value is None:
        return CurationDesignSpec()
    if isinstance(value, CurationDesignSpec):
        return value
    if isinstance(value, Mapping):
        return CurationDesignSpec.from_dict(value)
    path = Path(str(value))
    if path.exists():
        return CurationDesignSpec.from_yaml(path)
    raise ValueError(f"Unsupported curation design source: {value!r}")
