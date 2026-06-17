#!/usr/bin/env python3
"""Context-Graph E1 — content(vector) vs context(graph) vs hybrid on BC3
decision-making email threads, with a general-vs-ontology extraction ablation.

Reuses SEOCHO core + the generic graph/vector helpers from finder_4arm_sample;
the DECISION domain (dataset, ontology, prompt, answer, metric) lives here, not
in core/FinDER (CLAUDE.md: FinDER informs design, must not be a dependency).

Design (corrected 2026-06-03 after E1 v1 collapsed):
  - The decision graph is per-(THREAD, arm), built ONCE — NOT per query. A BC3
    thread yields several queries (E1/E2/E3/E4) over the SAME messages, so the
    graph is shared; rebuilding per query in one DB made same-thread workspaces
    merge (UNIQUE name) and collapse. Build once per thread, reuse for its queries.
  - ONE DB for the run (proven FinDER pattern), workspace per (thread, arm).
    Distinct threads have distinct entities → no cross-thread merge.
  - Node count is logged per build so an empty graph can never masquerade as a
    measured "graph loses" result (CLAUDE.md §20).

Per query we score 5 lanes: vector | graph@general | graph@decision |
hybrid@general | hybrid@decision. Metric: token_f1 inline (number_overlap is
meaningless for narrative); partials are finder_judge-format → score with
  finder_judge.py --judge-domain decision --judge-llms openai/gpt-5.5
"""
from __future__ import annotations
import argparse, csv, os, re, sys, json, math, statistics, time, subprocess, threading, signal
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "examples" / "contextgraph"))
from dotenv import dotenv_values
for k, v in dotenv_values(ROOT / ".env").items():
    if v is not None:
        os.environ[k] = v

from scripts.benchmarks.finder_4arm_sample import _graph_context, _vector_context
from seocho.query.strategy import PromptTemplate
from seocho.store.graph import Neo4jGraphStore, sanitize_database_name
from seocho.store.llm import create_llm_backend
from seocho import Ontology, Seocho
from decision_modules.compose import compose_modules, ARMS

SEP = "===EVIDENCE_BOUNDARY==="
DATA = ROOT / "examples/contextgraph/datasets/bc3_slices.csv"
PROMPT_FILE = ROOT / "examples/contextgraph/prompts/decision_meta_system_prompt.md"

_ANSWER_SYSTEM = (
    "You are a decision analyst answering a question about an email thread using "
    "ONLY the provided context. Answer directly, grounded in the context.\n"
    "- Name the participants, proposals, positions (support/oppose), and the "
    "decision/outcome explicitly.\n"
    "- Do not invent people, proposals, or decisions not in the context.\n"
    "- If the answer is not in the context, say 'not in the provided context'."
)


class _AddTimeoutError(TimeoutError):
    pass


def _run_with_alarm_timeout(timeout_seconds: float, fn, *args, **kwargs):
    if timeout_seconds <= 0:
        return fn(*args, **kwargs)
    if threading.current_thread() is not threading.main_thread():
        return fn(*args, **kwargs)
    previous_handler = signal.getsignal(signal.SIGALRM)

    def _raise_timeout(signum, frame):  # noqa: ANN001
        raise _AddTimeoutError(f"operation exceeded {timeout_seconds:.1f}s")

    signal.signal(signal.SIGALRM, _raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
    try:
        return fn(*args, **kwargs)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous_handler)


class BuildObserver:
    """Collect runner-local extraction hygiene metrics without changing core SDK.

    The arbiter is too noisy for prompt/schema micro-tuning, so this observer
    records deterministic leading indicators per (thread, arm): validation
    errors, illegal relationship types, and simple repair/drop counts.
    """

    def __init__(
        self,
        *,
        declared_relationships: set[str],
        repair_invalid_rels: bool = False,
    ) -> None:
        self.declared_relationships = declared_relationships
        self.repair_invalid_rels = repair_invalid_rels
        self.reset()

    def reset(self) -> None:
        self.validation_errors = 0
        self.unknown_relationship_errors = 0
        self.illegal_relationships = 0
        self.repaired_relationships = 0
        self.dropped_relationships = 0
        self.add_timeouts = 0
        self.add_errors = 0
        self.illegal_relationship_types: set[str] = set()

    def after_validate(
        self,
        nodes: list[dict[str, Any]],
        rels: list[dict[str, Any]],
        errors: list[str],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
        self.validation_errors += len(errors or [])
        unknown_errors = [
            err for err in (errors or []) if str(err).startswith("Unknown relationship type")
        ]
        self.unknown_relationship_errors += len(unknown_errors)

        declared = self.declared_relationships
        if not declared:
            return nodes, rels, errors

        node_by_id = {str(n.get("id")): n for n in nodes or []}
        kept: list[dict[str, Any]] = []
        residual_errors = list(errors or [])
        repaired_this_call = 0
        dropped_this_call = 0

        def _label(node_id: Any) -> str:
            node = node_by_id.get(str(node_id))
            return str((node or {}).get("label") or "")

        def _polarity(props: dict[str, Any]) -> str:
            for key in ("polarity", "direction", "stance"):
                value = str(props.get(key) or "").strip().upper()
                if value in {"FOR", "AGAINST", "NEUTRAL"}:
                    return value
                if value == "SUPPORT":
                    return "FOR"
                if value == "OPPOSE":
                    return "AGAINST"
            return ""

        for rel in rels or []:
            rtype = str(rel.get("type") or "").strip()
            if rtype in declared:
                kept.append(rel)
                continue

            self.illegal_relationships += 1
            self.illegal_relationship_types.add(rtype or "<empty>")

            if self.repair_invalid_rels and rtype == "OTHER":
                source_node = node_by_id.get(str(rel.get("source")))
                if source_node and source_node.get("label") == "DecisionEvent":
                    props = source_node.setdefault("properties", {})
                    props.setdefault("event_type", "OTHER")
                    repaired_this_call += 1
                    continue

            if (
                self.repair_invalid_rels
                and "HOLDS_POSITION" in declared
                and _label(rel.get("source")) == "Person"
                and _label(rel.get("target")) == "Topic"
            ):
                props = rel.setdefault("properties", {})
                polarity = _polarity(props)
                if polarity:
                    props["polarity"] = polarity
                    rel["type"] = "HOLDS_POSITION"
                    kept.append(rel)
                    repaired_this_call += 1
                    continue

            dropped_this_call += 1

        if self.repair_invalid_rels and (repaired_this_call or dropped_this_call):
            # The invalid rels have been removed/repaired locally, so the matching
            # unknown-type validation errors are no longer actionable. Other SHACL
            # errors remain visible to strict mode.
            residual_errors = [
                err for err in residual_errors
                if not str(err).startswith("Unknown relationship type")
            ]

        self.repaired_relationships += repaired_this_call
        self.dropped_relationships += dropped_this_call
        return nodes, kept, residual_errors

    def snapshot(self) -> dict[str, Any]:
        return {
            "validation_errors": self.validation_errors,
            "unknown_relationship_errors": self.unknown_relationship_errors,
            "illegal_relationships": self.illegal_relationships,
            "illegal_relationship_types": sorted(self.illegal_relationship_types),
            "repaired_relationships": self.repaired_relationships,
            "dropped_relationships": self.dropped_relationships,
            "add_timeouts": self.add_timeouts,
            "add_errors": self.add_errors,
            "repair_invalid_rels": self.repair_invalid_rels,
        }


def _tok(s):
    import re
    return re.sub(r"[^a-z0-9 ]", " ", str(s or "").lower()).split()


def token_f1(pred, gold):
    from collections import Counter
    p, g = _tok(pred), _tok(gold)
    if not p or not g:
        return 0.0
    common = sum((Counter(p) & Counter(g)).values())
    if not common:
        return 0.0
    prec, rec = common / len(p), common / len(g)
    return round(2 * prec * rec / (prec + rec), 4)


def build_decision_prompt(ontology, arm: str, prompt_file=None) -> PromptTemplate:
    """general arm => no schema block; ontology arm => inject decision schema.

    prompt_file selects the meta prompt (baseline vs approach1 SHACL+SKOS, etc.)."""
    meta = Path(prompt_file or PROMPT_FILE).read_text(encoding="utf-8")
    sys_tmpl = meta[meta.find("## ROLE"):]
    if arm == "non-ontology":
        onto_block = ("(no fixed schema — extract the salient decision elements "
                      "freely: participants, proposals, positions for/against, "
                      "arguments, decisions, with who and when)")
    else:
        ctx = ontology.to_extraction_context()
        onto_block = (f'Use ONLY these node labels and relationship types.\n\n'
                      f'ENTITY TYPES:\n{ctx.get("entity_types","")}\n\n'
                      f'RELATIONSHIP TYPES:\n{ctx.get("relationship_types","")}')
    sys_tmpl = sys_tmpl.replace("{{ontology}}", onto_block)
    return PromptTemplate(system=sys_tmpl, user="Email thread to extract:\n\n{{text}}")


def _load_ontology_for_arm(arm: str, artifact_json: str = "") -> Ontology:
    if artifact_json:
        payload = json.loads(Path(artifact_json).read_text(encoding="utf-8"))
        return Ontology.from_artifact(payload)
    return compose_modules(ARMS[arm])


def _bge_vector_context(refs, query, bge, *, top_k: int = 5, chunk_size: int = 800) -> str:
    """Top-k dense retrieval using the LOCAL BGE backend ($0, no OpenAI).

    Mirrors finder_4arm_sample._vector_context (same chunking/top_k) so the
    vector lane is comparable; only the embedder differs (BGE vs OpenAI). BGE
    vectors are already L2-normalized, so cosine == dot.
    """
    chunks = []
    for ref in refs:
        t = (ref or "").strip()
        if not t:
            continue
        if len(t) <= chunk_size:
            chunks.append(t)
        else:
            s = 0
            while s < len(t):
                chunks.append(t[s:s + chunk_size]); s += chunk_size - 100
    if not chunks:
        return ""
    cv = bge.embed(chunks)
    qv = bge.embed_queries([query])[0]
    scored = sorted(((sum(a * b for a, b in zip(qv, r)), i) for i, r in enumerate(cv)), reverse=True)
    idxs = [i for _, i in scored[:top_k]]
    return "\n\n---\n\n".join(f"[chunk #{j+1}]\n{chunks[i]}" for j, i in enumerate(idxs))


def answer(llm, query, context):
    if not context.strip():
        return "not in the provided context", {}
    # Resilience (§20.2): a single LLM timeout/connection error must NOT crash the
    # whole answering loop — record the failure for that case and continue.
    try:
        r = llm.complete(system=_ANSWER_SYSTEM, user=f"Question: {query}\n\n{context}")
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {str(e)[:100]}", {"error": type(e).__name__}
    return (getattr(r, "text", "") or ""), dict(getattr(r, "usage", {}) or {})


def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value))


def _append_jsonl(path: Path, record: dict[str, Any], lock: threading.Lock | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if lock is None:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
        return
    with lock:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")


def _run_build_workers(
    *,
    args: argparse.Namespace,
    thread_ids: list[str],
    db: str,
    out_run: str,
    out_dir: Path,
    metrics_path: Path,
) -> int:
    """Build threads in isolated child processes with per-thread timeout.

    This is intentionally subprocess-based rather than in-process multiprocessing:
    each worker gets a fresh LLM/graph/tracing client and can be killed without
    poisoning the parent run. Workspaces stay thread-scoped, and status JSONL is
    the source of truth for completed vs timeout/partial builds.
    """

    status_path = Path(args.status_jsonl) if args.status_jsonl else out_dir.parent / "build_status.jsonl"
    logs_dir = out_dir.parent / "worker_logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    status_lock = threading.Lock()
    timeout = float(args.thread_timeout_seconds or 0)

    def run_one(tid: str) -> dict[str, Any]:
        safe_tid = _safe_id(tid)
        log_path = logs_dir / f"{safe_tid}.log"
        trace_path = args.trace_jsonl
        if trace_path and args.trace_backend and "jsonl" in args.trace_backend:
            base = Path(trace_path)
            trace_path = str(base.with_name(f"{base.stem}.{safe_tid}{base.suffix or '.jsonl'}"))

        cmd = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--model", args.model,
            "--provider", args.provider,
            "--arms", args.arms,
            "--threads", "1",
            "--thread-ids", tid,
            "--run", args.run,
            "--out-run", out_run,
            "--data", str(args.data),
            "--prompt-file", str(args.prompt_file),
            "--database", db,
            "--embed", args.embed,
            "--build-only",
            "--strict", args.strict,
            "--metrics-jsonl", str(metrics_path),
            "--build-workers", "1",
        ]
        if args.repair_invalid_rels:
            cmd.append("--repair-invalid-rels")
        if args.trace_backend:
            cmd.extend(["--trace-backend", args.trace_backend])
        if trace_path:
            cmd.extend(["--trace-jsonl", trace_path])
        if args.per_add_timeout_seconds:
            cmd.extend(["--per-add-timeout-seconds", str(args.per_add_timeout_seconds)])
        if args.ontology_artifact_json:
            cmd.extend(["--ontology-artifact-json", str(args.ontology_artifact_json)])

        start = time.time()
        record: dict[str, Any] = {
            "run": args.run,
            "out_run": out_run,
            "thread_id": tid,
            "database": db,
            "status": "started",
            "timeout_seconds": timeout,
            "log_path": str(log_path),
            "started_at": start,
        }
        _append_jsonl(status_path, record, status_lock)
        with log_path.open("w", encoding="utf-8") as log_fh:
            try:
                completed = subprocess.run(
                    cmd,
                    cwd=str(ROOT),
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
                    timeout=timeout if timeout > 0 else None,
                    check=False,
                )
                status = "completed" if completed.returncode == 0 else "failed"
                returncode = completed.returncode
            except subprocess.TimeoutExpired:
                status = "timeout"
                returncode = None
            except Exception as exc:
                status = "failed"
                returncode = None
                log_fh.write(f"\nworker exception: {type(exc).__name__}: {exc}\n")

        elapsed = time.time() - start
        done = {
            **record,
            "status": status,
            "returncode": returncode,
            "elapsed_seconds": round(elapsed, 3),
            "completed_at": time.time(),
        }
        _append_jsonl(status_path, done, status_lock)
        return done

    print(
        f"== worker build: threads={len(thread_ids)} workers={args.build_workers} "
        f"timeout={timeout or 'none'}s status={status_path} =="
    )
    failures = 0
    with ThreadPoolExecutor(max_workers=max(1, int(args.build_workers))) as pool:
        futures = [pool.submit(run_one, tid) for tid in thread_ids]
        for fut in as_completed(futures):
            rec = fut.result()
            if rec["status"] != "completed":
                failures += 1
            print(
                f"  [worker {rec['thread_id']}] {rec['status']} "
                f"elapsed={rec.get('elapsed_seconds')}s log={rec['log_path']}"
            )
    print(f"worker status -> {status_path}")
    print(f"build metrics -> {metrics_path}")
    return 1 if failures else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="MiniMax-M2.5")
    ap.add_argument("--provider", default="mara")
    ap.add_argument("--arms", default="non-ontology,decision")  # general vs ontology
    ap.add_argument("--threads", type=int, default=4, help="number of distinct threads to sample")
    ap.add_argument("--thread-ids", default="",
                    help="comma-separated thread ids to run; overrides --threads when provided")
    ap.add_argument("--run", default="e1-bc3")
    ap.add_argument("--data", default=str(DATA), help="slices CSV (bc3_slices.csv / ami_slices.csv)")
    ap.add_argument("--prompt-file", default=str(PROMPT_FILE),
                    help="extraction meta prompt (baseline vs decision_meta_system_prompt_a1.md)")
    ap.add_argument("--db-prefix", default=None, help="DB name prefix (default derived from --data stem)")
    ap.add_argument("--database", default=None, help="pin exact graph DB (decouples from --model; for --reuse-graph)")
    ap.add_argument("--embed", default="bge", choices=["bge", "openai"],
                    help="vector-lane embedder: bge=local (default, $0, cost policy) | openai")
    ap.add_argument("--bge-device", default=None,
                    help="local BGE device: auto, cuda, cpu, or mps; defaults to SEOCHO_BGE_DEVICE/auto")
    ap.add_argument("--build-only", action="store_true",
                    help="build graphs only, skip answering (round-1: $0 CQ/SHACL metrics, judge deferred)")
    ap.add_argument("--reuse-graph", action="store_true",
                    help="reuse already-built graphs (skip re-extraction), answer only (round-2 judge)")
    ap.add_argument("--out-run", default=None,
                    help="output dir tag (default=--run); decouples partials dir from the workspace run-tag")
    ap.add_argument("--strict", default="off", choices=["off", "strip", "true"],
                    help="relation firewall: off (default) | strip (drop only undeclared relation "
                         "types, keep valid nodes) | true (reject whole chunk on any validation error)")
    ap.add_argument("--repair-invalid-rels", action="store_true",
                    help="runner-local repair pass for known invalid relation drift, e.g. OTHER rels "
                         "from process-context prompts; emits repair/drop metrics")
    ap.add_argument("--metrics-jsonl", default=None,
                    help="write per-build local metric records here (default: outputs/.../<out-run>/build_metrics.jsonl)")
    ap.add_argument("--trace-backend", default=None,
                    help="enable tracing backend for this run: none|console|jsonl|opik|jsonl,opik "
                         "(default: SEOCHO_TRACE_BACKEND env)")
    ap.add_argument("--trace-jsonl", default=None,
                    help="JSONL trace path when jsonl tracing is enabled")
    ap.add_argument("--build-workers", type=int, default=1,
                    help="build-only subprocess workers across thread ids; use >1 for timeout-safe parallel build")
    ap.add_argument("--thread-timeout-seconds", type=float, default=0.0,
                    help="per-thread worker timeout when --build-workers > 1; 0 disables timeout")
    ap.add_argument("--per-add-timeout-seconds", type=float, default=0.0,
                    help="per-message client.add timeout inside build workers; timed-out messages are skipped")
    ap.add_argument("--status-jsonl", default=None,
                    help="write parent worker terminal status records here")
    ap.add_argument("--ontology-artifact-json", default="",
                    help="load the extraction ontology from an approved/draft semantic artifact JSON "
                         "instead of composing the named arm modules; useful for approved_only-style "
                         "local experiment reruns")
    args = ap.parse_args()
    _STRICT = {"off": False, "strip": "strip", "true": True}[args.strict]

    data_path = Path(args.data)
    all_cases = list(csv.DictReader(open(data_path)))
    by_thread = defaultdict(list)
    for c in all_cases:
        by_thread[str(c["_id"]).split("#")[0]].append(c)
    if args.thread_ids.strip():
        requested = [x.strip() for x in args.thread_ids.split(",") if x.strip()]
        thread_ids = [tid for tid in requested if tid in by_thread]
    else:
        thread_ids = list(by_thread)[: args.threads]
    arms = [a.strip() for a in args.arms.split(",") if a.strip() in ARMS]

    # DB prefix from dataset stem (bc3_slices -> cgbc3, ami_slices -> cgami) so
    # different datasets never share a graph DB (per-dataset isolation).
    # --database pins the exact graph DB (decoupled from --model). Needed for
    # --reuse-graph when the ANSWER model differs from the model that BUILT the
    # graph (e.g. graphs built with MiniMax-M2.5, answered with M2.7 because M2.5
    # is down) — otherwise the model-derived DB would point at an empty database.
    if args.database:
        db = sanitize_database_name(args.database)
    else:
        prefix = args.db_prefix or ("cg" + re.sub(r"[^a-z0-9]", "", data_path.stem.lower().replace("slices", "")))
        db = sanitize_database_name(f"{prefix}{args.model}")
    out_run = args.out_run or args.run  # decouple output dir from workspace run-tag (reuse-graph)
    out_dir = ROOT / "outputs" / "evaluation" / "contextgraph" / out_run / "partial"
    out_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = Path(args.metrics_jsonl) if args.metrics_jsonl else out_dir.parent / "build_metrics.jsonl"
    metrics_path.parent.mkdir(parents=True, exist_ok=True)

    if (
        args.build_only
        and not args.reuse_graph
        and int(args.build_workers or 1) > 1
        and len(thread_ids) > 1
    ):
        parent_gs = Neo4jGraphStore(
            os.environ["NEO4J_URI"],
            os.environ.get("NEO4J_USER", "neo4j"),
            os.environ.get("NEO4J_PASSWORD", ""),
        )
        parent_gs.ensure_database(db, wait_online=True, timeout=30.0)
        parent_gs.close()
        code = _run_build_workers(
            args=args,
            thread_ids=thread_ids,
            db=db,
            out_run=out_run,
            out_dir=out_dir,
            metrics_path=metrics_path,
        )
        raise SystemExit(code)

    if args.trace_backend:
        from seocho.tracing import enable_tracing
        enable_tracing(
            backend=[b.strip() for b in args.trace_backend.split(",") if b.strip()],
            output=args.trace_jsonl or str(out_dir.parent / "trace.jsonl"),
            project_name=os.getenv("OPIK_PROJECT_NAME", "seocho-contextgraph"),
        )
    else:
        from seocho.tracing import configure_tracing_from_env
        configure_tracing_from_env()
    # Embedder for the vector lane. Build-only runs never answer vector lanes, so
    # avoid requiring optional sentence-transformers in pure graph-build jobs.
    bge = None
    oai = None
    if args.build_only:
        print("  [embed] skipped for build-only run")
    elif args.embed == "bge":
        from seocho.store.local_embedding import LocalBGEEmbeddingBackend
        bge = LocalBGEEmbeddingBackend(device=args.bge_device)
        print(f"  [embed] local BGE {bge._model_name} dim={bge.dim} device={bge.device} ($0, no OpenAI)")
    else:
        from openai import OpenAI
        oai = OpenAI(timeout=60)
    gs = Neo4jGraphStore(os.environ["NEO4J_URI"], os.environ.get("NEO4J_USER", "neo4j"),
                         os.environ.get("NEO4J_PASSWORD", ""))
    gs.ensure_database(db, wait_online=True, timeout=30.0)
    llm = create_llm_backend(provider=args.provider, model=args.model)
    print(f"== E1 [{data_path.stem}] run={args.run}: model={args.model} db={db} "
          f"arms={arms} threads={len(thread_ids)} embed={args.embed} ==\n")

    summary = []
    for tid in thread_ids:
        tcases = by_thread[tid]
        refs = [x.strip() for x in str(tcases[0]["references_joined"]).split(SEP) if x.strip()]
        # build the decision graph ONCE per (thread, arm)
        gctx_by_arm = {}
        for arm in arms:
            onto = _load_ontology_for_arm(arm, args.ontology_artifact_json)
            ws = f"{args.run}-{arm}-{tid}"
            observer = BuildObserver(
                declared_relationships=set(onto.relationships),
                repair_invalid_rels=args.repair_invalid_rels,
            )
            client = Seocho(ontology=onto, graph_store=gs, llm=llm,
                            workspace_id=ws, extraction_prompt=build_decision_prompt(onto, arm, args.prompt_file))
            client.default_database = db
            if getattr(client, "_engine", None) is not None:
                client._engine._indexing.on_after_validate = observer.after_validate
            build_elapsed = 0.0
            if args.reuse_graph:
                pass  # round-2: graph already built (build-only round-1); answer only, no re-extract
            else:
                try:
                    build_start = time.time()
                    for r in refs:
                        try:
                            _run_with_alarm_timeout(
                                float(args.per_add_timeout_seconds or 0.0),
                                client.add,
                                r,
                                user_id=ws,
                                strict_validation=_STRICT,
                            )
                        except _AddTimeoutError as e:
                            observer.add_timeouts += 1
                            print(
                                f"  [build {tid} @{arm}] add timeout after "
                                f"{args.per_add_timeout_seconds:.1f}s: {str(e)[:80]}"
                            )
                            continue
                        except Exception:
                            observer.add_errors += 1
                            raise
                except Exception as e:
                    print(f"  [build {tid} @{arm}] add err: {type(e).__name__}: {str(e)[:80]}")
                finally:
                    build_elapsed = time.time() - build_start
            # NOTE: do NOT client.close() here — it closes the SHARED gs driver,
            # which silently broke every subsequent build (build 1 wrote, close
            # killed gs, builds 2..N wrote 0 nodes). gs is closed once at the end.
            try:
                gctx = _graph_context(gs, ws, db)
            except Exception:
                gctx = ""
            try:
                nrec = gs.query("MATCH (n {_workspace_id:$w}) RETURN count(n) AS c",
                                params={"w": ws}, database=db)
                nodes = nrec[0]["c"] if nrec else 0
            except Exception:
                nodes = -1
            gctx_by_arm[arm] = gctx
            metric_record = {
                "run": args.run,
                "out_run": out_run,
                "thread_id": tid,
                "arm": arm,
                "workspace_id": ws,
                "database": db,
                "strict": args.strict,
                "reuse_graph": bool(args.reuse_graph),
                "nodes": nodes,
                "graph_context_chars": len(gctx),
                "elapsed_seconds": round(build_elapsed if not args.reuse_graph else 0.0, 3),
                **observer.snapshot(),
            }
            with metrics_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(metric_record, default=str) + "\n")
            try:
                from seocho.tracing import log_span, is_tracing_enabled
                if is_tracing_enabled():
                    log_span(
                        "contextgraph.build",
                        input_data={
                            "run": args.run,
                            "thread_id": tid,
                            "arm": arm,
                            "strict": args.strict,
                        },
                        output_data={
                            "nodes": nodes,
                            "graph_context_chars": len(gctx),
                            "validation_errors": observer.validation_errors,
                            "illegal_relationships": observer.illegal_relationships,
                            "repaired_relationships": observer.repaired_relationships,
                            "dropped_relationships": observer.dropped_relationships,
                        },
                        metadata={
                            "workspace_id": ws,
                            "database": db,
                            "model": f"{args.provider}/{args.model}",
                            "elapsed_seconds": metric_record["elapsed_seconds"],
                            "illegal_relationship_types": metric_record["illegal_relationship_types"],
                        },
                        tags=["contextgraph", "build", f"arm:{arm}", f"strict:{args.strict}"],
                    )
            except Exception:
                pass
            print(f"  [build {tid} @{arm:<12}] nodes={nodes} gctx_chars={len(gctx)}")

        if args.build_only:
            continue  # round-1: graphs only; $0 CQ/SHACL metrics, no answer/judge cost

        for c in tcases:
            q, gold, slice_ = c["query"], c["answer"], c["slice"]
            try:
                vec_ctx = (_bge_vector_context(refs, q, bge) if bge is not None
                           else _vector_context(refs, q, oai))
            except Exception:
                vec_ctx = ""
            lanes = {}
            a, u = answer(llm, q, "=== MESSAGES (vector top-k) ===\n" + vec_ctx)
            lanes[("vector", "n-a")] = (a, len(vec_ctx), u)
            for arm in arms:
                g = gctx_by_arm.get(arm, "")
                ag, ug = answer(llm, q, "=== DECISION GRAPH ===\n" + g)
                lanes[("graph", arm)] = (ag, len(g), ug)
                ah, uh = answer(llm, q, "=== MESSAGES ===\n" + vec_ctx + "\n\n=== DECISION GRAPH ===\n" + g)
                lanes[("hybrid", arm)] = (ah, len(vec_ctx) + len(g), uh)

            for (lane, arm), (ans, ctx_chars, usage) in lanes.items():
                f1 = token_f1(ans, gold)
                rec = {"_id": f"{c['_id']}|{lane}|{arm}", "slice": slice_, "category": "Decision",
                       "query": q, "expected_answer": gold, "answer": ans,
                       "retrieval": lane, "mode": lane, "arm": arm,
                       "model": f"{args.provider}/{args.model}",
                       "evaluation": {"number_overlap_ratio": 0.0}, "token_f1": f1,
                       "context_chars": ctx_chars, "answer_usage": usage}
                (out_dir / f"{slice_}_{c['_id']}_{lane}_{arm}.json".replace("#", "_")).write_text(
                    json.dumps(rec, default=str))
                summary.append({"slice": slice_, "lane": lane, "arm": arm, "f1": f1, "chars": ctx_chars})
            print(f"    [{c['_id']} {slice_}] " + "  ".join(
                f"{l}@{a}={token_f1(v[0], gold):.2f}" for (l, a), v in lanes.items()))

    gs.close()
    try:
        from seocho.tracing import flush_tracing
        flush_tracing()
    except Exception:
        pass
    print(f"\n=== E1 rollup [{data_path.stem}]: token_f1 by lane×arm (decision) ===")
    agg = defaultdict(list); chars = defaultdict(list)
    for s in summary:
        agg[(s["lane"], s["arm"])].append(s["f1"]); chars[(s["lane"], s["arm"])].append(s["chars"])
    for k in sorted(agg):
        print(f"  {k[0]:<8}@{k[1]:<12} f1={statistics.mean(agg[k]):.3f}  ctx_chars={statistics.mean(chars[k]):.0f}  n={len(agg[k])}")
    print(f"\nwrote {len(summary)} partials -> {out_dir}")
    print(f"build metrics -> {metrics_path}")
    print("judge: python scripts/benchmarks/finder_judge.py --judge-domain decision "
          f"--judge-llms openai/gpt-5.5 --inputs 'outputs/evaluation/contextgraph/{args.run}/partial/*.json' "
          f"--out outputs/evaluation/contextgraph/{args.run}_judged.json")


if __name__ == "__main__":
    main()
