# Recorder Phase 5 local acceptance — 2026-10-09

Phase 5 implements offline population analysis; no MARA, OpenAI or graph backend
was called. Real local DuckDB reads validate storage compatibility. Deterministic
Mock responses and synthetic outcomes validate contracts, not financial answering
quality or model performance.

Implemented: pandas run/gap API; E3 and A/T over task/seed units; logical versus
physical tokens/cost/wall time; evaluator separation; final-outcome error proxies;
budget behavior; BAM fitting with residuals/R²; constrained continuous allocation;
explicit modeled/observed regret; cheapest observed successful tier; route regret;
metadata fairness; compute-accounted best-of-N; paired bootstrap, exact McNemar
and Holm/Bonferroni corrections; `obs report` CSV/PNG/JSON/Markdown output.

Commands executed:

```sh
MPLCONFIGDIR=/tmp/seocho-matplotlib XDG_CACHE_HOME=/tmp/seocho-font-cache \
  .venv/bin/obs report examples/agent-observability/report.mock.yaml
MPLCONFIGDIR=/tmp/seocho-matplotlib XDG_CACHE_HOME=/tmp/seocho-font-cache \
  .venv/bin/python -m pytest -q tests/seocho/test_agent_analytics.py
```

The installed `obs` entrypoint was exercised directly. Phase 2's actual saved
loopback Mock dataset produced 20 run rows and 20 efficiency rows. Phase 3's five
saved replay runs produced five run rows and two intervention-aware condition rows.
Both reports retained every unknown Mock verdict as a gap and produced no eligible
E3/accuracy claim. No budget-fit data was invented for missing sweep populations.
Older information fingerprints are absent, so fairness cannot be asserted.

Artifacts remain ignored under `outputs/agent-observability/phase5/phase2-report`
and `phase3-report`. Both include source fingerprints, analysis code hash, explicit
configuration, row counts and CSV tables. Cost/accuracy figures were inspected.
The source fingerprint before and after report generation matched in the
Recorder-backed fixture, confirming analysis did not rewrite original records.

Seven analytics tests pass, including known synthetic E3, exact discordance counts,
BAM parameter recovery, heterogeneous-beta stationarity, unknown handling, incomplete
pair rejection, no single-unit CI, all-candidate best-of-N accounting and an actual
Recorder-backed report. The prior 31 focused tests also passed before adding the
seventh analytics test; the complete CI outcome is recorded below after completion.

Limits: report scope is audited completed runs, not intention-to-treat. Failed and
invalid runs remain explicit in gaps. Final-error proxies do not identify true
subtask errors. No paid optimal-allocation rerun or pretrained semantic validation
was performed. Predicted E3 requires an explicit accuracy/cost model; the BAM
additive error objective alone does not supply one. Declared matching does not
prove equal consumed compute. Gold best-of-N selection is labeled oracle only.

Full basic CI: 733 passed, 5 skipped, 16 warnings in 60.72 seconds; runtime
shell, module ownership, root hierarchy and agent document checks passed.
`git diff --check` passed. This duration describes local CI only, not provider
latency or platform performance.
