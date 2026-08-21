"""Spike: SEOCHO as a REASONING-alignment tool (Graph-CoT direction, hadry 2026-08-17).

The governance layer bounds ACTIONS (hard, downstream guardrail). This spike explores
the complementary channel: steering REASONING (soft, upstream) with the same pinned
ontology as the reference — watching a reasoning model's CoT stream and measuring
whether it stays on the enterprise domain, then injecting canonical grounding.

Case (real-case walking): an analyst agent reasons about "Atlas". World-knowledge
Atlas = MongoDB Atlas / CERN ATLAS / the Titan / atlas maps. The ENTERPRISE Atlas
(our shmem workspace) = Atlas Gateway, owned by Platform Team, incident INC-7.

A) UNGROUNDED: stream DeepSeek reasoning closed-book -> measure how much of the CoT
   drifts to world-knowledge Atlases (off-domain markers) vs our domain.
B) GROUNDED: resolve the mention through SEOCHO's canonical store (workspace-scoped),
   inject the canonical entity + 1-hop neighborhood as grounding context -> re-stream
   -> measure the same markers in the REASONING tokens (not just the answer).

Metric is deterministic (marker counts in reasoning text); reasoning tokens captured
from the stream (delta.reasoning / reasoning_content / <think> fallback).
"""
import json, os, re, sys
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "src"))
import importlib.util
_mx_spec = importlib.util.spec_from_file_location(
    "matrix", os.path.join(_ROOT, "scripts", "agentos", "e2e_arm_organ_matrix.py"))
_mx = importlib.util.module_from_spec(_mx_spec); _mx_spec.loader.exec_module(_mx)
_mx._load_mara()

from openai import OpenAI
client = OpenAI(api_key=os.environ["MARA_API_KEY"],
                base_url=os.environ.get("MARA_BASE_URL", "https://api.cloud.mara.com/v1"))

OFF_DOMAIN = ["mongodb", "cern", "titan", "greek", "mythology", "map collection",
              "atlas mountains", "cluster tier", "particle"]
ON_DOMAIN = ["atlas gateway", "platform team", "inc-7", "latency", "incident"]
Q = "What is Atlas, who owns it, and what happened with it recently?"

def stream_with_reasoning(messages, model="DeepSeek-V3.1"):
    """Stream and separate reasoning tokens from content tokens."""
    reasoning, content = [], []
    resp = client.chat.completions.create(model=model, messages=messages,
                                          stream=True, temperature=0.0)
    for chunk in resp:
        d = chunk.choices[0].delta if chunk.choices else None
        if d is None: continue
        for f in ("reasoning", "reasoning_content"):
            v = getattr(d, f, None)
            if v: reasoning.append(v)
        if d.content: content.append(d.content)
    r, c = "".join(reasoning), "".join(content)
    if not r and "<think>" in c:      # fallback: think-tag models
        m = re.search(r"<think>(.*?)</think>", c, re.S)
        if m: r, c = m.group(1), c[m.end():]
    return r, c

def markers(text):
    t = text.lower()
    return ([m for m in OFF_DOMAIN if m in t], [m for m in ON_DOMAIN if m in t])

def main():
    out = {}
    # ---- A) UNGROUNDED reasoning ----
    print("=== A) closed-book reasoning (no ontology) ===", flush=True)
    r, c = stream_with_reasoning([
        {"role": "system", "content": "You are an enterprise analyst. Think step by step."},
        {"role": "user", "content": Q}])
    off, on = markers(r + " " + c)
    print(f"  reasoning tokens: {len(r)} chars | OFF-domain markers in CoT+answer: {off} | on-domain: {on}", flush=True)
    print(f"  reasoning head: {r[:220]}", flush=True)
    print(f"  answer head  : {c[:180]}", flush=True)
    out["ungrounded"] = {"off_domain": off, "on_domain": on,
                         "reasoning_chars": len(r), "reasoning_head": r[:400], "answer": c[:300]}

    # ---- B) SEOCHO grounding injection ----
    print("\n=== B) SEOCHO canonical grounding -> re-reason ===", flush=True)
    from neo4j import GraphDatabase
    drv = GraphDatabase.driver("bolt://localhost:17687", auth=("neo4j", "h0gatepass"))
    with drv.session(database="deptlpg") as s:
        # read-time resolve: mention -> canonical entity + 1-hop neighborhood (ws-scoped)
        rows = list(s.run(
            "MATCH (n {_workspace_id:'shmem'}) WHERE toLower(n.name) CONTAINS 'atlas' "
            "AND NOT labels(n)[0] IN ['Document','DocumentVersion','Chunk','Section'] "
            "OPTIONAL MATCH (n)-[rel]-(m {_workspace_id:'shmem'}) "
            "WHERE NOT labels(m)[0] IN ['Document','DocumentVersion','Chunk','Section'] "
            "RETURN n.name AS name, labels(n)[0] AS label, "
            "collect(DISTINCT type(rel) + ' -> ' + coalesce(m.name,'')) AS hood"))
    drv.close()
    grounding = "\n".join(f"- {r['name']} ({r['label']}): " + "; ".join(r["hood"])
                          for r in rows)
    print(f"  canonical grounding injected:\n{grounding}", flush=True)
    r2, c2 = stream_with_reasoning([
        {"role": "system", "content":
         "You are an enterprise analyst. Think step by step. GROUND your reasoning in "
         "the company's canonical knowledge below; 'Atlas' refers ONLY to the entity "
         "defined here, never to any world-knowledge Atlas.\n\nCANONICAL KNOWLEDGE:\n" + grounding},
        {"role": "user", "content": Q}])
    off2, on2 = markers(r2 + " " + c2)
    print(f"  reasoning tokens: {len(r2)} chars | OFF-domain: {off2} | on-domain: {on2}", flush=True)
    print(f"  reasoning head: {r2[:220]}", flush=True)
    print(f"  answer head  : {c2[:180]}", flush=True)
    out["grounded"] = {"off_domain": off2, "on_domain": on2,
                       "reasoning_chars": len(r2), "reasoning_head": r2[:400], "answer": c2[:300]}

    json.dump(out, open(os.path.join(_ROOT, "outputs", "agentos",
                                     "spike_reasoning_grounding.json"), "w"), indent=2)
    print(f"\nVERDICT: ungrounded CoT off-domain={len(off)} on-domain={len(on)} | "
          f"grounded CoT off-domain={len(off2)} on-domain={len(on2)}", flush=True)

if __name__ == "__main__":
    main()
