---
name: opik-trace-meta
description: Tag SEOCHO benchmark traces with the 4 mandatory comparison axes (dataset, model, GraphRAG flow, ontology) so Opik runs are filterable and comparable, and verify they landed. Invoke when emitting Opik traces from a benchmark, adding a new run dimension, or when traces show up in Opik with missing/blank tags.
---

# opik-trace-meta

Purpose: make every benchmark trace answer the four questions a comparison needs — **(1) what data, (2) what model, (3) what GraphRAG flow, (4) what ontology** — as filterable Opik tag chips, not just buried metadata. Without this, a multi-LLM × multi-mode × multi-ontology sweep is unanalyzable in the UI. (CLAUDE.md §9 observability; §19 minimal-tag contract.)

## When to invoke

- Emitting Opik traces from any benchmark runner (`finder_compare_vector_graph.py`, `finder_4arm_sample.py`, `graphrag_bench.py`).
- Adding a new comparison dimension (a new model, retrieval mode, ontology arm, graph-quality variant).
- Traces appear in Opik with blank `name`/`tags`/`metadata`, or the UI can't filter by data/model/flow/ontology.

## The 4 mandatory axes (build via `bench_common.build_core_meta`)

Use `examples/finder/lib/bench_common.build_core_meta(...)` — it returns `(tags, metadata)` with all four axes promoted to **tags** (filter chips), the rest in metadata:

| Axis | Tags emitted |
|---|---|
| 1. dataset | `dataset:<csv>`, `case:<id>`, `slice:<S#>`, `category:<...>` |
| 2. model | `model:<provider/model>`, `provider:<...>`, `judge:<provider/model>` (judge traces) |
| 3. GraphRAG flow | `flow:graphrag`, `mode:<vector|graph|hybrid>`, `retrieval_k`, `reasoning_mode`, `repair_budget` |
| 4. ontology | `ontology_hash:<10hex>`, `modules:<be+ind+...>`, `prompt_hash:<10hex>` |
| A/B dims | `graph_quality:<raw|qualified|...>`, `cypher_agent:<v1|v2>` |

Wrap the actual work in `bench_common.run_under_opik_track(name, tags, metadata, work_fn)` so the LLM sub-call traces nest under a tagged parent. `name` convention: `<phase|stage>/<provider>/<mode>/<case>`.

## Self-hosted vs cloud

- **Self-hosted (default here):** `.env` sets `OPIK_URL_OVERRIDE=http://localhost:5173/api` + `SEOCHO_TRACE_OPIK_MODE=self_host` + `OPIK_WORKSPACE` + `OPIK_PROJECT_NAME`. Setting `OPIK_URL_OVERRIDE` auto-flips `seocho.tracing` to self-host. Bring the server up (`./opik.sh` from the cloned comet-ml/opik repo) and confirm `http://localhost:5173/api/...` returns 200 before relying on traces.
- Keep a cloud key (if any) in a separate var like `OPIK_CLOUD_API_KEY` so it doesn't override the self-host config.
- **SDK must match server major version** (self-host `:latest` ≈ opik 2.x). A stale SDK silently writes traces with NULL name/tags/metadata.

## Verify the tags actually landed (don't trust "it ran")

Query the REST API directly (no MCP needed):
```bash
python3 - <<'PY'
import os, json, urllib.request, urllib.parse
base=os.environ["OPIK_URL_OVERRIDE"].rstrip("/"); ws=os.environ["OPIK_WORKSPACE"]; proj=os.environ["OPIK_PROJECT_NAME"]
def get(p,q): 
    u=base+p+"?"+urllib.parse.urlencode(q)
    return json.loads(urllib.request.urlopen(urllib.request.Request(u,headers={"Comet-Workspace":ws}),timeout=10).read())
pid=next(p["id"] for p in get("/v1/private/projects",{"workspace_name":ws,"size":100})["content"] if p["name"]==proj)
t=get("/v1/private/traces",{"workspace_name":ws,"project_id":pid,"size":1})["content"][0]
tags=" ".join(t.get("tags") or [])
print({k:(n in tags) for k,n in [("dataset","dataset:"),("model","model:"),("flow","flow:"),("ontology","ontology_hash:")]})
PY
```
All four must be `True`.

## Gotchas

- **Empty tags despite a "successful" run** = trace was created outside the `@track`/`run_under_opik_track` context (so `update_current_trace` was a no-op), or an SDK/server version skew. Wrap work in the track helper and match SDK to server.
- **A real `0` score must not become `-1`.** When recording judge scores, `int(d.get("score", -1) or -1)` turns a legitimate `0` (refusal/wrong) into `-1`; use an `isinstance` check and reserve `-1` for genuine parse failures only.
