# ADR-0042: OpenAI-Compatible Provider And Vector Backend Contract

Date: 2026-04-12
Status: Accepted

## Context

The public SEOCHO SDK had two remaining bottlenecks:

- language model configuration was effectively OpenAI-only
- vector search was effectively FAISS-only

That mismatch made the SDK less useful for teams that want to:

- keep OpenAI Agents SDK orchestration patterns
- route model traffic through OpenAI-compatible providers such as DeepSeek,
  Kimi, or Grok
- swap the vector persistence layer between local FAISS and a persistent
  database such as LanceDB

The project also needs a cleaner bridge between the SDK's provider settings and
OpenAI Agents SDK model/provider objects so multi-agent orchestration can reuse
the same runtime configuration.

## Decision

SEOCHO will standardize on an OpenAI-compatible provider contract in the public
SDK.

The SDK now exposes:

- `OpenAICompatibleBackend`
- provider presets:
  - `OpenAIBackend`
  - `DeepSeekBackend`
  - `KimiBackend`
  - `GrokBackend`
- provider factory helpers:
  - `create_llm_backend(...)`
  - `create_embedding_backend(...)`
- provider metadata helpers:
  - `get_provider_spec(...)`
  - `list_provider_specs()`

The same backend contract now also exposes OpenAI Agents SDK helpers:

- `to_agents_sdk_model()`
- `to_agents_provider()`
- `to_agents_run_config()`

Vector search will remain decoupled from LLM choice.

The SDK now exposes:

- `FAISSVectorStore` for lightweight in-memory search
- `LanceDBVectorStore` for persistent local or cloud-backed storage
- `create_vector_store(...)` as the public factory

Portable runtime bundles will preserve the provider preset instead of assuming
OpenAI-only export.

## Consequences

Positive:

- public SDK users can keep one orchestration/runtime surface while swapping
  OpenAI-compatible providers
- provider configuration can flow into OpenAI Agents SDK helpers without
  rewriting agent construction code
- vector storage becomes pluggable without forcing a FAISS-only path
- portable bundle export remains shareable when the authoring environment uses a
  non-OpenAI provider preset

Tradeoffs:

- OpenAI-compatible does not guarantee full feature parity across providers
- embedding support is provider-specific and may still require explicit model
  selection
- this ADR updates the public SDK surface first; legacy extraction-service
  internals still have older direct OpenAI call paths

## Implementation Notes

- provider contract: `seocho/store/llm.py`
- vector backends: `seocho/store/vector.py`
- portable runtime bundle wiring: `seocho/runtime_bundle.py`
- local CLI provider flags: `seocho/cli.py`
