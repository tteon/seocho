"""ProviderAgent — owns one MARA model's sovereign DozerDB store (hq-42k).

Indexes its assigned FinDER cases into its own instance; answers sub-queries
over its OWN subgraph only (never reaches into another store). Reuses the
extraction machinery of 02_extract_departments.py and the graph-as-context
serializer of 09_federated_benchmark.py.

Workspace namespace for this scenario: ``fedcat-<provider>-<case_id>`` — keeps
it isolated from the dept demo's ``mdm-<dept>-<case_id>`` workspaces even when
a store is shared.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Optional

MDM_ROOT = Path(__file__).resolve().parents[1]
ROOT = MDM_ROOT.parents[1]
for p in (str(MDM_ROOT), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import os  # noqa: E402

from agents.contracts import (  # noqa: E402
    ABSTAIN_MARK, Provenance, ProviderFact, ProviderResponse,
)
from lib import federation  # noqa: E402

_INFRA = set(federation.INFRA_LABELS)


def workspace_for(provider_id: str, case_id: str) -> str:
    return f"fedcat-{provider_id}-{case_id}"


def _auth():
    return (os.environ.get("NEO4J_USER", "neo4j"), os.environ.get("NEO4J_PASSWORD", ""))


class ProviderAgent:
    """One model-provider over one physical instance."""

    def __init__(self, instance: "federation.Instance", *, client=None, spec=None):
        self.instance = instance
        self.provider_id = instance.dept
        self.model = instance.model
        self._client = client     # MARA chat client (lazy; only for answer())
        self._spec = spec

    # -- indexing (PAID) ----------------------------------------------------

    def index(self, case: dict, *, ontology, extraction_tmpl) -> dict:
        """Extract one case's gold references into THIS provider's store.

        Opens its own Neo4jGraphStore (Seocho.close closes the store it's
        given — a shared store would die after the first case). Returns the
        02-style record; caller handles resume-safe partials.
        """
        from seocho import Seocho
        from seocho.store.graph import Neo4jGraphStore
        from seocho.store.llm import create_llm_backend

        ws = workspace_for(self.provider_id, case["case_id"])
        started = time.perf_counter()
        error = ""
        nodes = rels = 0
        client = None
        try:
            gs = Neo4jGraphStore(self.instance.uri, *_auth())
            llm = create_llm_backend(provider="mara", model=self.model)
            client = Seocho(ontology=ontology, graph_store=gs, llm=llm,
                            workspace_id=ws, extraction_prompt=extraction_tmpl)
            client.default_database = self.instance.database
            try:
                gs.ensure_constraints(ontology, database=self.instance.database)
            except Exception:
                pass
            for ref in case["references"]:
                client.add(ref, user_id=ws)
            n = gs.query("MATCH (n {_workspace_id:$w}) RETURN count(n) AS c",
                         params={"w": ws}, database=self.instance.database)
            r = gs.query("MATCH ({_workspace_id:$w})-[x]->() RETURN count(x) AS c",
                         params={"w": ws}, database=self.instance.database)
            nodes, rels = int(n[0]["c"]), int(r[0]["c"])
        except Exception as exc:  # noqa: BLE001 — recorded, never imputed (§20.2)
            error = f"{type(exc).__name__}: {exc}"
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
        return {
            "provider_id": self.provider_id, "model": self.model,
            "uri": self.instance.uri, "case_id": case["case_id"],
            "slice": case["slice"], "category": case["category"],
            "workspace_id": ws, "nodes_created": nodes, "rels_created": rels,
            "latency_s": round(time.perf_counter() - started, 2), "error": error,
        }

    # -- reading / answering ------------------------------------------------

    def _read_subgraph(self, gs, ws: str) -> tuple[str, list[ProviderFact], int]:
        """Serialize this provider's subgraph for a case + extract value facts."""
        db = self.instance.database
        nodes = gs.query(
            "MATCH (n {_workspace_id:$w}) RETURN labels(n) AS l, properties(n) AS p",
            params={"w": ws}, database=db) or []
        lines: list[str] = []
        facts: list[ProviderFact] = []
        node_count = 0
        for r in nodes:
            labs = [x for x in (r["l"] or []) if x not in _INFRA]
            if not labs:
                continue
            node_count += 1
            p = r["p"] or {}
            nm = p.get("name") or ""
            bits = [f"{k}={p[k]}" for k in
                    ("value", "period", "basis", "segment", "amount") if p.get(k)]
            lines.append(f"- ({'/'.join(labs)}) {nm}" + (f" [{', '.join(bits)}]" if bits else ""))
        # value-bearing nodes -> structured facts (reference path)
        valrows = gs.query(
            "MATCH (m {_workspace_id:$w}) WHERE m.value IS NOT NULL AND m.name IS NOT NULL "
            "RETURN m.name AS metric, m.value AS value, m.period AS period, "
            "m.basis AS basis, elementId(m) AS eid", params={"w": ws}, database=db) or []
        for r in valrows:
            facts.append(ProviderFact(
                metric_raw=str(r["metric"]), period=str(r["period"] or ""),
                basis=str(r["basis"] or ""), value_raw=str(r["value"]),
                eid=str(r["eid"])))
        rels = gs.query(
            "MATCH (a {_workspace_id:$w})-[x]->(b {_workspace_id:$w}) "
            "RETURN coalesce(a.name,'?') AS s, type(x) AS t, coalesce(b.name,'?') AS o "
            "LIMIT 80", params={"w": ws}, database=db) or []
        if rels:
            lines.append("--- relationships ---")
            lines.extend(f"- {r['s']} -{r['t']}-> {r['o']}" for r in rels)
        header = f"=== {self.provider_id.upper()} provider graph (model {self.model}) ==="
        return (header + "\n" + "\n".join(lines)) if lines else "", facts, node_count

    def answer(self, query: str, case_id: str, *, answer_system: str,
               retrieve_only: bool = False) -> ProviderResponse:
        """Read this provider's subgraph; optionally synthesize a narrative answer.

        Empty subgraph ⇒ abstain (no LLM call). The provider that lacks the
        data says so — it never fabricates (§20.2).
        """
        from seocho.store.graph import Neo4jGraphStore
        from examples.finder.lib import llm_io

        ws = workspace_for(self.provider_id, case_id)
        t0 = time.perf_counter()
        gs = Neo4jGraphStore(self.instance.uri, *_auth())
        try:
            context, facts, n_nodes = self._read_subgraph(gs, ws)
        finally:
            gs.close()
        retrieval_ms = (time.perf_counter() - t0) * 1000

        prov = Provenance(provider_id=self.provider_id, src_instance=self.instance.uri,
                          model=self.model, workspace_id=ws, retrieved_node_count=n_nodes)
        if not context.strip():
            return ProviderResponse(
                provider_id=self.provider_id, query=query, case_id=case_id,
                abstain=True, context="", facts=tuple(facts), answer=ABSTAIN_MARK,
                confidence=0.0, provenance=prov, retrieval_ms=retrieval_ms, answer_ms=0.0)

        answer_text: Optional[str] = None
        answer_ms = 0.0
        err = ""
        if not retrieve_only:
            if self._client is None or self._spec is None:
                self._spec = llm_io.parse_llm_spec(f"mara/{self.model}")
                self._client = llm_io.make_chat_client(self._spec)
            t1 = time.perf_counter()
            try:
                answer_text = llm_io.chat_complete(
                    client=self._client, model=self._spec.model, system=answer_system,
                    user=f"Question: {query}\n\n{context}", temperature=0.0,
                    label=self.provider_id, max_attempts=3, spec=self._spec)
            except Exception as exc:  # noqa: BLE001
                err = f"{type(exc).__name__}: {exc}"
            answer_ms = (time.perf_counter() - t1) * 1000

        abstain = bool(answer_text and ABSTAIN_MARK in answer_text.lower())
        # confidence = retrieved-coverage heuristic (node count, capped); 0 on abstain
        conf = 0.0 if (abstain or err) else round(min(n_nodes / 15.0, 1.0), 3)
        return ProviderResponse(
            provider_id=self.provider_id, query=query, case_id=case_id,
            abstain=abstain, context=context, facts=tuple(facts),
            answer=answer_text, confidence=conf, provenance=prov,
            retrieval_ms=retrieval_ms, answer_ms=answer_ms, error=err)
