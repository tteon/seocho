I identified a performance improvement opportunity regarding file parsing: multiple places in the `scripts/` directory use `.read_text(encoding="utf-8").splitlines()` which buffers the whole file into memory, rather than lazy file iteration via `with path.open('r', encoding='utf-8') as f:`.
This applies to:
- scripts/benchmarks/okx_risk_llm_e2e.py
- scripts/benchmarks/finder_full_corpus_profile.py
- scripts/serve_track/run_arms.py
- scripts/serve_track/erb_adapter.py
- scripts/serve_track/sample_bench_strata.py
- scripts/serve_track/make_context_arms.py
- scripts/serve_track/profile_rag.py
- scripts/cache_sim/simulator.py
- scripts/ci/check_adr_index.py
- scripts/ci/triage_metadata.py
