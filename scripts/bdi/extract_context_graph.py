#!/usr/bin/env python3
"""Extract BDI context graph from Obsidian wiki topic pages.

Reads ``wiki/topics/*.md`` frontmatter (beliefs, desires, intentions)
and produces a JSON-LD graph that can be loaded into seocho's graph store
for context engineering queries.

Usage::

    python scripts/bdi/extract_context_graph.py \\
        --vault /home/hadry/my_local_work/obsidian/seocho \\
        --output outputs/bdi-context-graph.json

The output is a dict with ``nodes`` and ``relationships`` arrays in the
same format that ``seocho.store.graph.GraphStore.write()`` accepts.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def _parse_frontmatter(text: str) -> Dict[str, Any]:
    """Extract YAML frontmatter from markdown text."""
    if not text.startswith("---"):
        return {}
    end = text.find("---", 3)
    if end == -1:
        return {}
    raw = text[3:end].strip()

    # Minimal YAML parser for our structured frontmatter
    # (avoids pyyaml dependency for a standalone script)
    result: Dict[str, Any] = {}
    current_key = ""
    current_list: Optional[List] = None
    current_item: Optional[Dict] = None

    for line in raw.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Top-level key: value
        m = re.match(r"^(\w[\w_-]*)\s*:\s*(.*)", line)
        if m and not line.startswith("  "):
            if current_key and current_list is not None:
                result[current_key] = current_list
            current_key = m.group(1)
            value = m.group(2).strip()
            current_list = None
            current_item = None

            if value.startswith("[") and value.endswith("]"):
                result[current_key] = [v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()]
            elif value:
                result[current_key] = value
            else:
                current_list = []
            continue

        # List item with dict (- id: xxx)
        m_item = re.match(r"^\s+-\s+(\w+)\s*:\s*(.*)", line)
        if m_item:
            if current_item is not None and current_list is not None:
                current_list.append(current_item)
            current_item = {m_item.group(1): m_item.group(2).strip().strip("'\"") }
            continue

        # Dict continuation (    key: value)
        m_kv = re.match(r"^\s+(\w[\w_]*)\s*:\s*(.*)", line)
        if m_kv and current_item is not None:
            val = m_kv.group(2).strip().strip("'\"")
            if val.startswith("[") and val.endswith("]"):
                val = [v.strip().strip("'\"") for v in val[1:-1].split(",") if v.strip()]
            current_item[m_kv.group(1)] = val
            continue

    # Flush last item/list
    if current_item is not None and current_list is not None:
        current_list.append(current_item)
    if current_key and current_list is not None:
        result[current_key] = current_list

    return result


def extract_graph(vault_path: str) -> Dict[str, Any]:
    """Extract BDI graph from wiki/topics/*.md files."""
    topics_dir = Path(vault_path) / "wiki" / "topics"
    if not topics_dir.is_dir():
        print(f"Warning: {topics_dir} not found", file=sys.stderr)
        return {"nodes": [], "relationships": []}

    nodes: List[Dict[str, Any]] = []
    relationships: List[Dict[str, Any]] = []
    seen_ids: set = set()

    def _add_node(node_id: str, label: str, props: Dict[str, Any]) -> None:
        if node_id in seen_ids:
            return
        seen_ids.add(node_id)
        nodes.append({"id": node_id, "label": label, "properties": {**props, "name": node_id}})

    for md_file in sorted(topics_dir.glob("*.md")):
        text = md_file.read_text(encoding="utf-8")
        fm = _parse_frontmatter(text)
        topic_id = fm.get("topic", md_file.stem)

        # Topic node
        _add_node(f"topic:{topic_id}", "Topic", {"label": topic_id, "file": str(md_file.name)})

        # Related topics
        for rel in fm.get("related", []):
            if rel:
                _add_node(f"topic:{rel}", "Topic", {"label": rel})
                relationships.append({
                    "source": f"topic:{topic_id}", "target": f"topic:{rel}",
                    "type": "RELATED_TO", "properties": {},
                })

        # ADRs
        for adr in fm.get("adrs", []):
            if adr:
                _add_node(f"plan:{adr}", "Plan", {"label": adr})
                relationships.append({
                    "source": f"topic:{topic_id}", "target": f"plan:{adr}",
                    "type": "REFERENCES", "properties": {},
                })

        # Beads issues
        for bead in fm.get("beads", []):
            if bead:
                _add_node(f"task:{bead}", "Task", {"beads_id": bead})
                relationships.append({
                    "source": f"topic:{topic_id}", "target": f"task:{bead}",
                    "type": "TRACKED_BY", "properties": {},
                })

        # BDI: Beliefs
        for belief in fm.get("beliefs", []):
            if not isinstance(belief, dict):
                continue
            bid = belief.get("id", "")
            if not bid:
                continue
            _add_node(f"belief:{bid}", "Belief", {
                "label": belief.get("label", ""),
                "status": belief.get("status", "active"),
            })
            relationships.append({
                "source": f"topic:{topic_id}", "target": f"belief:{bid}",
                "type": "HAS_BELIEF", "properties": {},
            })
            # Evidence grounding
            for ev in belief.get("evidence", []):
                if ev:
                    _add_node(f"evidence:{ev}", "Evidence", {"label": ev})
                    relationships.append({
                        "source": f"belief:{bid}", "target": f"evidence:{ev}",
                        "type": "GROUNDED_IN", "properties": {},
                    })
            # Invalidation
            inv = belief.get("invalidates")
            if isinstance(inv, list):
                for target in inv:
                    relationships.append({
                        "source": f"belief:{bid}", "target": f"belief:{target}",
                        "type": "INVALIDATES", "properties": {},
                    })
            elif isinstance(inv, str) and inv:
                relationships.append({
                    "source": f"belief:{bid}", "target": f"belief:{inv}",
                    "type": "INVALIDATES", "properties": {},
                })

        # BDI: Desires
        for desire in fm.get("desires", []):
            if not isinstance(desire, dict):
                continue
            did = desire.get("id", "")
            if not did:
                continue
            _add_node(f"desire:{did}", "Desire", {"label": desire.get("label", "")})
            relationships.append({
                "source": f"topic:{topic_id}", "target": f"desire:{did}",
                "type": "HAS_DESIRE", "properties": {},
            })
            for mb in desire.get("motivated_by", []):
                if mb:
                    relationships.append({
                        "source": f"belief:{mb}", "target": f"desire:{did}",
                        "type": "MOTIVATES", "properties": {},
                    })

        # BDI: Intentions
        for intention in fm.get("intentions", []):
            if not isinstance(intention, dict):
                continue
            iid = intention.get("id", "")
            if not iid:
                continue
            _add_node(f"intention:{iid}", "Intention", {
                "label": intention.get("label", ""),
                "status": intention.get("status", "active"),
            })
            relationships.append({
                "source": f"topic:{topic_id}", "target": f"intention:{iid}",
                "type": "HAS_INTENTION", "properties": {},
            })
            for ful in intention.get("fulfils", []):
                if ful:
                    relationships.append({
                        "source": f"intention:{iid}", "target": f"desire:{ful}",
                        "type": "FULFILS", "properties": {},
                    })
            for sup in intention.get("supported_by", []):
                if sup:
                    relationships.append({
                        "source": f"intention:{iid}", "target": f"belief:{sup}",
                        "type": "SUPPORTED_BY", "properties": {},
                    })
            plan = intention.get("plan", "")
            if plan:
                plan_id = f"plan:{plan}" if not plan.startswith("seocho-") else f"task:{plan}"
                relationships.append({
                    "source": f"intention:{iid}", "target": plan_id,
                    "type": "SPECIFIES", "properties": {},
                })

    return {"nodes": nodes, "relationships": relationships}


def main():
    parser = argparse.ArgumentParser(description="Extract BDI context graph from Obsidian wiki")
    parser.add_argument("--vault", default=os.path.expanduser("~/my_local_work/obsidian/seocho"),
                        help="Obsidian vault root path")
    parser.add_argument("--output", default="-", help="Output JSON file (- for stdout)")
    args = parser.parse_args()

    graph = extract_graph(args.vault)
    output = json.dumps(graph, indent=2, ensure_ascii=False)

    if args.output == "-":
        print(output)
    else:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"Wrote {len(graph['nodes'])} nodes, {len(graph['relationships'])} relationships to {args.output}",
              file=sys.stderr)


if __name__ == "__main__":
    main()
