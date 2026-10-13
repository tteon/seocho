# MARA agent-as-tool live verification — 2026-10-09

Actual MARA requests using OpenAI Agents SDK 0.18.2 and OpenAI Python SDK 2.45.0
passed single-specialist and two-specialist parallel context/return probes.
No Neo4j or DozerDB service was used. The local probe tool returns random
verification markers; all agent reasoning/tool selection uses the MARA service.

## Interface findings

The [MARA overview](https://mara-cloud-docs-qa.vercel.app/get-started/overview)
uses `https://api.cloud.mara.com/v1` with the OpenAI client.
The [function calling documentation](https://mara-cloud-docs-qa.vercel.app/api-features/function-calling-and-json-mode)
describes tool calls, tool results and JSON-object output; schema strict=true
is unsupported. The probe uses Chat Completions, JSON-object output, SDK-side
Pydantic validation and no hidden transport retries. Cloud SDK tracing is disabled.

MiniMax-M2.7 and MiniMax-M2.5 each returned HTTP 422/model_not_in_plan.
GET /v1/models returned DeepSeek-V3.1, DeepSeek-V3.2, MiniMax-M3,
gemma-4-31B-it and gpt-oss-120b for this account. Both successful probes used
DeepSeek-V3.1, which is documented to support tool calling. API keys were supplied
only through a hidden process input and were not written to reports or source.

## Observed behavior

The single-specialist probe made four actual model requests and two tool
invocations. Manager passed a fresh marker to the Specialist; the Specialist
called its private read_probe tool, returned the separate secret reply marker,
and Manager returned both values exactly. Provider usage reported 1614 input
and 199 output tokens across all four calls.

The parallel probe made six actual model requests and four tool invocations.
Supervisor received two independent requests and their distinct context markers,
constructed delegated task descriptions, and called ask_left and ask_right.
Both Specialists obtained their own private reply; Supervisor combined both
without altering either result. Invocation intervals overlapped by 1.726 seconds.
Provider usage reported 2955 input and 421 output tokens across all six calls.
The overlap establishes concurrent execution in this run, not a speedup claim.

The single probe allowed eight model calls, four tool calls, 1024 output tokens
per model call and ninety seconds. The parallel probe allowed twelve model calls,
six tool calls, 2048 output tokens per call and 120 seconds. These are compatibility
probes with different caps, not a fair cost/quality benchmark. Each was run once,
without warmup. SDK parallel agent-tool execution was enabled for two Specialists.
Remote service build, model revision and hardware limits are not exposed/verified.

## Artifacts and limits

Local JSON receipts are in outputs/collaboration-mara/:

- live-tool-smoke-20261009.json (MiniMax-M2.7 rejection)
- live-tool-smoke-m25-20261009.json (MiniMax-M2.5 rejection)
- live-tool-smoke-deepseek-20261009.json (single-specialist success)
- live-parallel-deepseek-20261009.json (parallel Supervisor success)

Those artifacts stay ignored. No key is included. The inputs are synthetic random
markers, not FinDER questions or financial documents. The parallel task set is
preidentified; this does not demonstrate arbitrary question decomposition,
planning quality, native handoff compatibility, graph retrieval, production
readiness, scalability, or relative design quality. The FinDER sdk_manager mode
remains distinct from this Supervisor-parallel probe and still requires a separate
implementation/comparison for multi-specialist parallel dispatch.
