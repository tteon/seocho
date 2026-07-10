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

Public sanctions data supplies a high-confidence labelled seed, not complete
ground truth for illicit activity. Precision and recall claims beyond direct
seed matching require a separately governed labelled dataset and human review.
