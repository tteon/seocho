# ADR-0166: Remove the Opik tracing backend

- Status: Accepted
- Date: 2026-08-16
- Supersedes the tracing half of the product consensus in `CLAUDE.md`
- Related: `ADR-0144` (local OTel stack), `ADR-0146` (metric contract),
  `ADR-0147` (OTLP and query workload observability), `ADR-0109`
  (AnswerShape/RouteProfile, whose evidence came from Opik-stored traces)

## Context

Opik was adopted when the repository had no observability surface of its own.
It no longer does. `src/seocho/metrics.py` declares **97 instruments** covering
all four SRE golden signals — 12 latency, 49 traffic, 13 error, 23 saturation —
and every one of them is referenced outside the declaring module: an audit found
**zero dead declarations**, with emission wired across roughly twenty modules
(`store/graph.py` 14 sites, `index/pipeline.py` 12, `store/llm.py` 11). `ADR-0144`
added a four-container local OTel stack, and `ADR-0146`/`#482` finished the
saturation signals.

Against that, Opik was a second, overlapping path to the same information, with
costs the first path does not have:

- **It sent data to a third party.** `store/llm.py` wrapped the OpenAI client in
  `track_openai` whenever the backend was on, so every prompt and completion left
  the machine. `bench_common.py` additionally called `comet.com` over REST to
  list workspaces.
- **It was a dependency in two extras.** `ci` and `dev` both pinned `opik`,
  so contributors installed it whether or not they traced anything.
- **It cost surface.** Five Opik-only keyword arguments on `enable_tracing`
  (`url`, `workspace`, `project_name`, `api_key`, `opik_mode`), an `--opik` CLI
  flag, an 8-container compose profile, three `Makefile` targets, an SDK-version
  compatibility warning, and a parallel trace path inside `SessionTrace`.

None of that bought anything the metric surface and the OTLP backend do not
already provide.

## Decision

Remove the Opik backend entirely. The tracing contract stays vendor-neutral and
becomes `none | console | jsonl | otlp`.

## What this breaks, and what it does not

`SEOCHO_TRACE_BACKEND=opik` no longer resolves. It does **not** crash: the
existing validation at `tracing.py` logs `Unsupported SEOCHO_TRACE_BACKEND=opik;
expected one of console, jsonl, none, otlp` and disables tracing. A user with
that env var loses tracing and is told why, in one line, at startup.

The five Opik-only `enable_tracing` keyword arguments are gone. No caller in the
repository passed any of them — verified across `src/`, `runtime/`,
`extraction/`, `tests/`, `examples/` and `scripts/` — so this narrows a public
signature that nothing used.

`extraction/tracing.py` keeps its functions as deliberate no-ops rather than
being deleted. Eight modules import `track`, `wrap_openai_client`,
`update_current_span` and `update_current_trace`, and `@track` is applied as a
decorator at import time; deleting the module would break those call sites for
no gain. `extraction/` is a compatibility surface under `CLAUDE.md` — it owns
legacy behaviour, not new instrumentation. The same reasoning applies to
`run_traced` / `set_trace_metadata` / `set_feedback_scores` in
`examples/finder/lib/bench_common.py`: the call sites are the only place that
records *what* each benchmark run is measuring, so removing the exporter must
not remove the labelling.

## Provenance is kept; the vendor name is not

`AnswerShape` and `RouteProfile` (`ADR-0109`) were derived from the
icml2026/kdd2026 trace corpus, which happened to be stored in Opik. Those
docstrings now name the corpus rather than the tool. The finding is the
evidence, not the software that held it — erasing the provenance would have been
the wrong kind of cleanup.

## Third-party audit

The repository was swept for other observability vendors:
`langfuse`, `langsmith`, `wandb`, `mlflow`, `arize`, `phoenix`, `braintrust`,
`helicone`, `datadog`, `sentry`, `honeycomb`, `neptune`, `clearml`, `traceloop`,
`signoz`. **Opik/Comet was the only integration.** The apparent hits elsewhere
are false positives: `datadog`, `neptune` and `phoenix` appear in
`src/seocho/semantic_layer/cik_table.json` as SEC registrant names, and
`langfuse`/`phoenix` appear once in a docs comparison table.
`prometheus`/`grafana`/`tempo`/`jaeger` are the repository's own OSS stack from
`ADR-0144`, not a hosted vendor, and stay.

## Consequences

- The four golden signals are served by one surface, not two.
- No prompt or completion leaves the machine by default; the teaching notebooks
  now write traces to a local JSONL file a learner can open, with no signup.
- `pip install seocho[ci]` and `[dev]` get one less transitive dependency.
- Anyone who wants a hosted backend implements `TracingBackend` and passes the
  instance — the plugin seam that made this removal cheap in the first place.

## Follow-ups

- Six teaching notebooks still call `setup_opik` / `teardown_opik` /
  `opik_console_link`; `examples/teaching/_shared/trace_setup.py` keeps those as
  aliases. Remove them when the notebooks are next regenerated.
- Prose and generated slide HTML under `docs/` and `examples/teaching/` still
  mention Opik. Those are documentation and generated artifacts, not behaviour,
  and are tracked separately rather than rewritten in this change.
