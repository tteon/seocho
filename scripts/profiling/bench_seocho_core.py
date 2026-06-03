#!/usr/bin/env python3
"""$0 Python-vs-Rust benchmark for the (revived) seocho-core native extension.

No LLM / embedding API calls — synthetic 1536-dim f64 vectors + real node JSON
pulled from existing DozerDB graphs. Methodology follows the Rust-expert review:
  - --release wheel only (debug would lie); assert native path is actually live.
  - interleaved A/B in one process, GC disabled in the timed region, warmup,
    report min/median/p90 (min = least-interference estimate of true cost).
  - cosine: a `noop_consume` probe pays the SAME PyO3 marshaling but ~no math,
    so (rust - noop) attributes native compute vs the boundary-crossing floor.
  - matrix: the honest competitor is NumPy BLAS (X@Xt on normalized rows),
    NOT the naive Python triple loop — report all three.
  - rules: time the WHOLE infer_rules_from_graph (incl. json dumps/loads on the
    native path), and assert native==fallback output (parity) before trusting it.

Run: python scripts/profiling/bench_seocho_core.py
"""
from __future__ import annotations
import gc, json, math, statistics, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dotenv import dotenv_values
import os
for k, v in dotenv_values(ROOT / ".env").items():
    if v is not None:
        os.environ[k] = v

# Native module is OFF by default (CLAUDE.md §21). This bench is the only
# sanctioned reason to build it; guard the import so merely importing this module
# (e.g. for load_real_nodes) never crashes when the wheel is absent.
try:
    import seocho_core as sc
    HAVE_NATIVE = True
except ImportError:
    sc = None
    HAVE_NATIVE = False

_BUILD_HINT = (
    "seocho_core native module not installed (expected — §21 keeps it OFF by default).\n"
    "  This bench measures the native A/B, so build it temporarily:\n"
    "    (cd seocho-core && maturin build --release --interpreter python3)\n"
    "    pip install --user seocho-core/target/wheels/seocho_core-*.whl\n"
    "  Then re-run. Per §21, UNINSTALL afterwards to restore the default-off state:\n"
    "    pip uninstall -y seocho-core\n"
    "  (To profile the data plane WITHOUT native, use run_discovery.py — no build needed.)"
)

DIM = 1536  # text-embedding-3-small width (matches the real linker input)


def _timed(fn, k, warmup=200):
    """Return (min, median, p90) per-call seconds over k calls, GC off, warmed."""
    for _ in range(warmup):
        fn()
    samples = []
    gc.disable()
    try:
        for _ in range(k):
            t = time.perf_counter_ns()
            fn()
            samples.append(time.perf_counter_ns() - t)
    finally:
        gc.enable()
    samples.sort()
    n = len(samples)
    return (samples[0] / 1e9,
            samples[n // 2] / 1e9,
            samples[int(n * 0.9)] / 1e9)


# ---- pure-Python fallbacks (copied byte-for-byte from the live fallback paths) ----
def py_cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y; na += x * x; nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return max(min(dot / (math.sqrt(na) * math.sqrt(nb)), 1.0), -1.0)


def main():
    if not HAVE_NATIVE:
        print("=" * 78)
        print("seocho-core $0 Python-vs-Rust benchmark — SKIPPED")
        print("=" * 78)
        print(_BUILD_HINT)
        return
    print("=" * 78)
    print("seocho-core $0 Python-vs-Rust benchmark")
    print(f"native module: {sc.__file__}")
    print("=" * 78)

    # deterministic synthetic vectors (no RNG in timed loop)
    a = [math.sin(i * 0.013) for i in range(DIM)]
    b = [math.cos(i * 0.017) for i in range(DIM)]
    # correctness: rust == python fallback
    assert abs(sc.cosine_similarity(a, b) - py_cosine(a, b)) < 1e-9, "cosine parity FAIL"

    # ---------------- 1) scalar cosine @ dim 1536 (per-record call) ----------------
    K = 20000
    pr = _timed(lambda: py_cosine(a, b), K)
    rr = _timed(lambda: sc.cosine_similarity(a, b), K)
    nr = _timed(lambda: sc.noop_consume(a, b), K)
    print("\n[1] scalar cosine_similarity, dim=1536, per-call (min / median / p90), us")
    print(f"  python fallback : {pr[0]*1e6:8.3f} / {pr[1]*1e6:8.3f} / {pr[2]*1e6:8.3f}")
    print(f"  rust            : {rr[0]*1e6:8.3f} / {rr[1]*1e6:8.3f} / {rr[2]*1e6:8.3f}")
    print(f"  noop (marshal)  : {nr[0]*1e6:8.3f} / {nr[1]*1e6:8.3f} / {nr[2]*1e6:8.3f}")
    rust_compute = max(rr[0] - nr[0], 0.0)
    print(f"  -> speedup(min) python/rust = {pr[0]/rr[0]:.2f}x | "
          f"marshal floor = {nr[0]*1e6:.3f}us | est native compute = {rust_compute*1e6:.3f}us "
          f"({100*nr[0]/rr[0]:.0f}% of rust call is marshaling)")

    # ---------------- 2) cosine matrix: Rust vs NumPy(BLAS) vs Python ---------------
    print("\n[2] NxN cosine matrix, dim=1536 (the honest competitor is NumPy BLAS)")
    try:
        import numpy as np
        have_np = True
    except ImportError:
        have_np = False
        print("  numpy not available — skipping BLAS baseline")
    for N in (64, 256, 512):
        vecs = [[math.sin((i + j) * 0.01) for j in range(DIM)] for i in range(N)]
        rk = max(3, 200 // N)
        r_rust = _timed(lambda: sc.cosine_similarity_matrix(vecs), rk, warmup=2)
        line = f"  N={N:<4} rust={r_rust[0]*1e3:8.2f}ms"
        if have_np:
            X = np.asarray(vecs, dtype=np.float64)
            Xn = X / (np.linalg.norm(X, axis=1, keepdims=True) + 1e-12)
            r_np = _timed(lambda: Xn @ Xn.T, rk, warmup=2)
            line += f"  numpy(BLAS)={r_np[0]*1e3:8.2f}ms  -> numpy is {r_rust[0]/r_np[0]:.1f}x faster than naive-rust"
        if N == 64:  # python triple loop only at small N (it's O(N^2*D))
            def py_mat():
                m = [[0.0] * N for _ in range(N)]
                for i in range(N):
                    for j in range(i, N):
                        m[i][j] = py_cosine(vecs[i], vecs[j])
                return m
            r_py = _timed(py_mat, 2, warmup=1)
            line += f"  python={r_py[0]*1e3:8.1f}ms"
        print(line)

    # ---------------- 3) rules: whole-path native vs fallback + PARITY --------------
    print("\n[3] infer_rules_from_graph: whole-path native vs Python fallback")
    import seocho.rules as rules
    nodes = load_real_nodes()
    if not nodes:
        print("  no real nodes available from DozerDB — skipping rules bench")
    else:
        for size in (100, 1000, 4000, len(nodes)):
            sub = {"nodes": nodes[:size]}
            n = len(sub["nodes"])
            # parity: native vs fallback must produce identical rules
            rules._USE_NATIVE_RULES = True
            rs_native = rules.infer_rules_from_graph(sub).to_dict()
            rules._USE_NATIVE_RULES = False
            rs_py = rules.infer_rules_from_graph(sub).to_dict()
            parity = _rules_equal(rs_native, rs_py)
            # timing
            rules._USE_NATIVE_RULES = True
            tn = _timed(lambda: rules.infer_rules_from_graph(sub), max(3, 2000 // n), warmup=2)
            rules._USE_NATIVE_RULES = False
            tp = _timed(lambda: rules.infer_rules_from_graph(sub), max(3, 2000 // n), warmup=2)
            print(f"  nodes={n:<6} native={tn[0]*1e3:8.2f}ms  python={tp[0]*1e3:8.2f}ms  "
                  f"-> {tp[0]/tn[0]:.2f}x  | n_rules={len(rs_native['rules'])} | "
                  f"PARITY={'OK' if parity else 'MISMATCH!!'}")
        rules._USE_NATIVE_RULES = True


def _rules_equal(a, b):
    ka = sorted((r["label"], r["property_name"], r["kind"], json.dumps(r["params"], sort_keys=True))
                for r in a["rules"])
    kb = sorted((r["label"], r["property_name"], r["kind"], json.dumps(r["params"], sort_keys=True))
                for r in b["rules"])
    return ka == kb


def load_real_nodes():
    """Pull {label, properties} node dicts from an existing DozerDB graph ($0)."""
    try:
        from seocho.store.graph import Neo4jGraphStore
        gs = Neo4jGraphStore(os.environ["NEO4J_URI"], os.environ.get("NEO4J_USER", "neo4j"),
                             os.environ.get("NEO4J_PASSWORD", ""))
    except Exception as e:
        print(f"  (graph store unavailable: {type(e).__name__})")
        return []
    out = []
    try:
        for db in ("yitae0531deepseek", "cgbc3minimaxm25"):
            try:
                recs = gs.query(
                    "MATCH (n) RETURN labels(n) AS labels, properties(n) AS props LIMIT 12000",
                    database=db)
                for r in recs:
                    labs = r.get("labels") or []
                    props = dict(r.get("props") or {})
                    props.pop("_workspace_id", None)
                    out.append({"label": labs[0] if labs else "Entity", "properties": props})
                if out:
                    break
            except Exception:
                continue
    finally:
        gs.close()
    return out


if __name__ == "__main__":
    main()
