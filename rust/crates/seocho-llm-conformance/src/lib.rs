//! SEOCHO LLM I/O conformance.
//!
//! This crate currently only carries the normative `seocho.llm_exchange.v1` record spec
//! (`spec/llm-exchange-v1.md`), its JSON Schema, the golden fixtures under `spec/fixtures/`
//! and an integration test (`tests/llm_exchange_v1_fixtures.rs`) proving the fixtures are
//! consumable from Rust. The stdlib-only Python reference validator and fixture generator
//! live in `python/`. Design: `docs/design/llm-io-contract.md` at the repository root.
