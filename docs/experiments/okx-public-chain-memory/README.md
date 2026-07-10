# OKX Public-Chain Long-Term-Memory Benchmark

This benchmark evaluates SEOCHO's blockchain-memory contract with public,
current Bitcoin data instead of treating synthetic fixtures as product proof.
It downloads the current OFAC SDN XML, extracts entries explicitly typed as
`Digital Currency Address - XBT`, and reads confirmed transactions for a small
bounded sample through the Blockstream Esplora API.

The benchmark measures canonical block ingestion, event/outbox counts,
throughput of the selected transaction runner, and idempotent replay. It maps
only direct address interactions. It does not infer that addresses in the same
transaction share an owner, and it does not call every interacting address a
sanctioned wallet. The public label remains attached only to the address
published by OFAC.

Run from the repository root:

    uv run python scripts/benchmarks/okx_public_chain_memory.py \
      --max-addresses 1 \
      --max-pages 1 \
      --output outputs/evaluation/okx-public-chain-memory.json

Raw addresses are needed transiently to query the public API. The report keeps
only opaque SHA-256 references and aggregate counts. Raw API payloads are not
persisted. `outputs/` remains ignored.

The checked-in result was collected on 2026-07-11 Asia/Seoul. The source
contained 518 XBT labels. The bounded run fetched two confirmed transactions,
derived 102 address-interaction events across two blocks, created 102 projection
outbox entries, and made both block replays no-ops. Local reference-runner
throughput was about 7,907 events/second. This is not a FoundationDB performance
claim; a live cluster benchmark remains required.

The complete validation ladder is:

1. Deterministic fixtures prove idempotency, atomic rollback, reorg history,
   aggregate compensation, and causal watermark behavior in CI.
2. This public-mainnet lane proves that current external schemas and real
   high-fan-out transactions pass through the contract without leaking raw
   addresses into reports.
3. A live FoundationDB lane must measure commit latency, conflict/retry rate,
   sustained events/second, hot-key pressure, and recovery after worker loss.
   It must also replace or validate the reference per-workspace sequence key
   and define bounded partition manifests for observations above 128 events.
4. A DozerDB projection lane must measure outbox lag and direct/one-to-four-hop
   risk retrieval against a frozen labelled seed set.
5. A Mara explanation lane must measure evidence coverage, unsupported-claim
   rate, disclosure leakage, token cost, and latency with the answerer fixed.

The LLM smoke lane is now available independently of the public-chain fetch:

    uv run python scripts/benchmarks/okx_risk_llm_e2e.py \
      --dataset examples/okx-risk-preflight/llm_e2e_dataset.jsonl \
      --model MiniMax-M2.5

It runs six deterministic risk cases through Mara when `MARA_API_KEY` is set.
Without the key it reports an explicit skip. It records disposition accuracy,
provenance coverage, leakage count, response hash, and latency; it does not
persist completions.

The integrated vertical slice combines all layers in one run:

    set -a; source .env; set +a
    uv run --extra local python scripts/benchmarks/okx_full_vertical_slice.py \
      --model gpt-oss-120b --max-addresses 1 --max-pages 1 \
      --max-cases 6 --concurrency 3

The verified run fetched two real transactions, produced 102 versioned memory
events and 102 outbox entries, replayed two blocks as no-ops, compiled six
approved risk recipes, and completed six concurrent Mara explanations. All six
dispositions and provenance references matched, with zero leakage cases and a
2.69-second LLM p95. This result is a smoke demonstration, not an SLA: live
FoundationDB/DozerDB and sustained-load runs remain separate gates.

Public sanctions data supplies a high-confidence labelled seed, not complete
ground truth for illicit activity. Precision and recall claims beyond direct
seed matching require a separately governed labelled dataset and human review.
