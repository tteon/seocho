# Exchange-Calibrated Agent Memory Dataset

Status: normative dataset and evaluation contract  
Last updated: 2026-07-11

## 1. Objective

Create a reproducible, privacy-safe dataset large enough to validate SEOCHO
long-term memory, GraphRAG, context management, concurrency, and governance.
The corpus combines real public Bitcoin anchors with synthetic private order
lifecycles. Synthetic events preserve documented exchange semantics; they are
not represented as real customer trades or empirical venue frequencies.

## 2. Evidence classes

Every record declares one evidence class:

| Class | Meaning |
|---|---|
| `observed_public_chain` | fetched block/transaction fact with source manifest |
| `observed_public_market` | public trade/order-book/status fact from a venue API |
| `documented_exchange_semantics` | state/field/constraint taken from official docs |
| `synthetic_calibrated` | generated private lifecycle constrained by the above |
| `fault_injected` | deliberately duplicated, delayed, reordered, rejected, or lost |

No synthetic customer, account, wallet owner, order, or fill may be described
as observed. Frequencies remain versioned hypotheses until calibrated using an
authorized, aggregated production or demo export.

## 3. Official semantic calibration

### OKX v5

- Order states: `live`, `partially_filled`, `filled`, `canceled`, and
  `mmp_canceled`.
- A successful place/amend/cancel response acknowledges acceptance, not final
  exchange state. The orders channel or order query confirms the outcome.
- Immediate fills can transition `live -> partially_filled* -> filled`.
- IOC/FOK/post-only rejection can appear as `live -> canceled`.
- Duplicate order messages may occur; a trade ID is deduplicated per
  instrument. Filled messages without trade ID are deduplicated per order.
- Fill time orders trades; gateway `inTime/outTime`, create time, update time,
  and system record time have different meanings.
- Fills can precede balance/position convergence. Batch operations contain up
  to 20 orders and are not atomic; each item has its own status code.
- Fills history is bounded to three months and rate-limited, making durable
  application memory necessary.

### Binance Spot

- Preserve order status separately from execution type (`NEW`, `TRADE`,
  `CANCELED`, `REJECTED`, `EXPIRED`, `REPLACED`, `TRADE_PREVENTION`).
- A REST timeout can leave execution status unknown. Recovery must reconcile
  user-data-stream events and order queries rather than retrying blindly.
- Amend-keep-priority retains the order ID and produces a replacement event.
- STP expiry and cancel-replace partial outcomes are distinct terminal causes.

### Coinbase Advanced Trade

- User-channel status includes `PENDING`, `OPEN`, `FILLED`, `CANCEL_QUEUED`,
  `CANCELLED`, `EXPIRED`, and `FAILED`.
- Subscription begins with open-order snapshots in batches of 50, followed by
  patches. Heartbeat counters and sequence numbers detect missed messages.
- Sparse channels can close unless heartbeat is subscribed; the user endpoint
  should be paired with a market-data failover.
- Orders cover market, limit, stop, bracket, TWAP, liquidation, and scaled
  types with explicit time-in-force semantics.

## 4. Three data layers

### L1. Public source layer

- Bitcoin block hash, height, parent, confirmation time and transaction IDs;
- privacy-safe input/output address references and amounts;
- public venue trades, candles, instrument/status and optional L2 snapshots;
- immutable source URL, fetch time, response hash and schema version.

### L2. Agent transaction layer

Agents propose, risk-check, route, submit, reconcile, settle and remember an
order. Each lifecycle references an L1 anchor but is explicitly
`synthetic_calibrated`. Canonical events distinguish:

- client intent, request dispatch, gateway acknowledgement and final state;
- venue order ID, client order ID, fill/trade ID and batch item ID;
- event time, gateway in/out time, venue update time and ingest time;
- cumulative versus last fill, price, fee, maker/taker and reject/cancel cause;
- causal parent, idempotency key, sequence, policy/ontology/prompt versions;
- duplicate, late, reordered, missing, recovered and compensation markers.

### L3. Longitudinal memory layer

One synthetic subject spans days or months, multiple sessions, API keys,
agents, venues, policies and ontology versions. It includes point-in-time
snapshots, superseded revisions, reorg/orphan compensation, disclosure
bindings, prompt receipts and answer receipts.

## 5. Required lifecycle families

1. immediate full fill;
2. one or many partial fills then full fill;
3. cancel acknowledgement then confirmed cancellation;
4. cancel-fill race where final state is filled;
5. amend accepted then confirmed, including keep-priority;
6. matching/risk rejection;
7. IOC/FOK/post-only expiration or cancellation;
8. OKX MMP cancellation and Binance STP prevention;
9. batch request with mixed per-item results;
10. REST timeout with unknown execution, later reconciled;
11. duplicate WebSocket messages;
12. out-of-order and delayed events;
13. sequence gap, disconnect, snapshot plus patch recovery;
14. fill observed before balance/position convergence;
15. liquidation/ADL/forced close where supported;
16. chain confirmation followed by reorg and compensation;
17. policy or ontology version change during a session;
18. forbidden-field disclosure attempt;
19. graph projection lag and authoritative fallback;
20. projector write-before-ack replay.

## 6. Scale tiers and split

| Tier | Intents | Approx. events | Purpose |
|---|---:|---:|---|
| smoke | 100 | 700–1,200 | contract and local E2E |
| load | 10,000 | 70k–120k | concurrency and index tuning |
| long | 100,000 | 700k–1.2m | single-subject long memory |
| stress | 1,000,000 | 7m–12m | sustained ingestion and rebuild |

Use deterministic hashing of the logical intent to assign train/dev/test; do
not randomly split individual events from the same lifecycle. Golden queries
and injected failures remain hidden from prompt construction.

## 7. Frequency policy

The generator exposes basis-point weights by venue, instrument, session and
lifecycle. Defaults are workload hypotheses designed to exercise every branch,
not claims about OKX, Binance, or Coinbase traffic. Every result stores the
weights. Promotion requires comparison to an authorized aggregate export using
state-transition, fill-count, size, latency, duplicate, reconnect and error
distributions.

Public market calibration may adjust price, size, interarrival and volatility.
It must never infer private order success/failure frequencies from public
trades.

## 8. OKX-relevant query catalog

### Memory correctness

- What was the canonical order state at sequence/time X, and what superseded it?
- Which duplicate or late messages were ignored, and using which idempotency key?
- Did an accepted cancel actually become canceled, filled, or remain unknown?
- Reproduce the answer using the exact memory, policy, ontology and prompt versions.
- Show all compensation revisions caused by a Bitcoin reorg.

### Agent and graph reasoning

- Show the bounded agent handoff path from intent through settlement.
- Which orders share a strategy, API key scope, instrument, policy or chain anchor?
- Find transactions whose fill arrived before balance/position convergence.
- Find cancel-fill races within N milliseconds and explain the final authority.
- Compare exact 1–5 hop retrieval latency and result parity.

### Cross-venue operations

- Normalize the same logical lifecycle across OKX, Binance and Coinbase states.
- Which venue requests are acknowledged but not terminally confirmed?
- Which reconnects required snapshot/patch reconciliation or order REST repair?
- Which batch requests partially succeeded, and which items remain unresolved?
- Compare latency components without conflating gateway, match and ingest time.

### Context and LLM

- Select the smallest causal memory set needed to explain the latest state.
- Compare full history, recency-only and Context Graph selection with one model.
- What token reduction was achieved and did required-slot/provenance accuracy change?
- Answer after 100k prior events while including the just-committed intent.
- Produce a bounded partial answer when one federation target is unavailable.

### Governance and reliability

- What information may this role receive under the active ontology policy?
- Did policy/ontology drift invalidate cached context during the session?
- Are any prompt, trace or metric fields disallowed or high-cardinality?
- Which SLO burned first under load: commit, outbox age, graph retrieval or LLM?
- Can projection and answer receipts be rebuilt exactly after a worker crash?

## 9. Acceptance

- Every synthetic fact is labeled and reproducible from seed plus manifest.
- Canonical state is correct under duplicate, reorder, timeout and race cases.
- PostgreSQL revision/outbox parity and DozerDB rebuild parity are 100%.
- No silent stale answer, lost commit, forbidden disclosure or unsupported claim.
- Query quality is reported with provenance/slot accuracy, not LLM fluency alone.
- Scale results identify actual services, versions, limits, warmup and skipped gates.

## 10. Primary references

- [OKX API v5](https://www.okx.com/docs-v5/en/)
- [OKX API best practices](https://www.okx.com/docs-v5/trick_en/)
- [Binance Spot REST](https://developers.binance.com/en/docs/products/spot/rest-api)
- [Coinbase Advanced Trade WebSocket channels](https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/websocket/websocket-channels)
- [Coinbase Advanced Trade list orders](https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/orders/list-orders)
- [Blockstream Esplora API](https://github.com/Blockstream/esplora/blob/master/API.md)
