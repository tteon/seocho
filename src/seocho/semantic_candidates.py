"""Pure candidate merge rules shared by prompt and query composition."""

from __future__ import annotations
import json
from typing import Any, Dict, Sequence


def merge_shacl_candidates(candidates: Sequence[Any]) -> Dict[str, Any]:
    shape_map: Dict[str, Dict[str, Any]] = {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for shape in candidate.get("shapes", []):
            if not isinstance(shape, dict):
                continue
            target_class = str(shape.get("target_class", "")).strip()
            if not target_class:
                continue
            existing = shape_map.setdefault(
                target_class, {"target_class": target_class, "properties": []}
            )
            seen = {
                (
                    prop.get("path"),
                    prop.get("constraint"),
                    json.dumps(prop.get("params", {}), sort_keys=True),
                )
                for prop in existing["properties"]
                if isinstance(prop, dict)
            }
            for prop in shape.get("properties", []):
                if not isinstance(prop, dict):
                    continue
                path = str(prop.get("path", "")).strip()
                constraint = str(prop.get("constraint", "")).strip()
                if not path or not constraint:
                    continue
                key = (
                    path,
                    constraint,
                    json.dumps(prop.get("params", {}), sort_keys=True),
                )
                if key in seen:
                    continue
                seen.add(key)
                existing["properties"].append(
                    {
                        "path": path,
                        "constraint": constraint,
                        "params": prop.get("params", {})
                        if isinstance(prop.get("params", {}), dict)
                        else {},
                    }
                )
    return {"shapes": list(shape_map.values())}
