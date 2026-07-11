# Critical Agent-Memory Scenarios for an OKX-Style AI Infrastructure Role

These scenarios evaluate infrastructure behavior, not model eloquence. Every
answer must identify the authoritative memory sequence, graph watermark,
evidence provenance, policy/prompt/ontology versions, missing information, and
support status. A fluent answer with stale or unauthorized evidence fails.

## S1. Read-your-write after order submission

**Incident:** An execution agent commits a BTC-USDT-SWAP order intent to
PostgreSQL. The support agent asks about it before DozerDB projects the outbox.

**User query:**

> 방금 execution agent가 제출한 BTC-USDT-SWAP 주문의 현재 상태와 승인한
> agent를 알려줘. 아직 graph에 반영되지 않았다면 그 사실도 명시해줘.

**Failure injection:** Hold the projector after memory sequence `N` while the
request requires causal token `N+1`.

**Required behavior:** Wait within budget, fall back to authoritative memory,
or return `support_status=stale`. Never silently answer from graph sequence
`N`. The receipt names required/current watermarks.

**Critical signals:** `projection.lag`, `context.stale_targets`, fallback
source, wait duration, answer support status.

**Gate:** zero silent stale answers across concurrency 1/8/32/64.

## S2. Conflicting concurrent agent decisions

**Incident:** A risk agent approves an intent while a strategy agent requests
cancellation and a second execution worker retries an earlier submission.

**User query:**

> 동일 intent에 approve, place, cancel 이벤트가 동시에 들어왔을 때 어떤
> 전이가 canonical이며 거절된 전이는 무엇인가?

**Failure injection:** Submit conflicting transitions against one intent with
different idempotency keys and two projector workers.

**Required behavior:** PostgreSQL advisory lock and state machine serialize a
single valid history. Invalid transitions roll back without outbox entries.
Worker ownership requires a live etcd lease and fencing token.

**Gate:** no lost commit, no double order, no outbox for rejected transitions,
and a monotonic memory revision.

## S3. Disputed historical fill

**Incident:** A customer disputes an answer given before a partial fill became
fully settled.

**User query:**

> sequence 82,400에서 이 주문을 partial fill이라고 답한 근거와 현재
> settled 상태의 근거를 분리해서 보여줘. 당시 답변을 재현할 수 있는가?

**Required behavior:** Point-in-time read returns only revisions visible at
82,400. Current evidence is a separate bundle. Both answer receipts retain
memory/provenance references and prompt/policy versions.

**Gate:** historical answer is reproducible after later updates and projection
rebuild; no current fact leaks into the historical section.

## S4. Projector crash between graph write and acknowledgement

**Incident:** DozerDB accepts a batch, but the projector dies before marking
PostgreSQL outbox rows projected.

**Operator query:**

> 재시작한 projector가 sequence 10,001~10,100을 다시 처리했을 때 graph와
> watermark가 정확히 한 번 적용된 것과 동일한가?

**Failure injection:** Kill the worker after graph write and before ack.

**Required behavior:** Stable IDs make DozerDB upserts idempotent; replay may
repeat writes but not logical nodes/edges. Ack and watermark advance only after
successful replay.

**Gate:** graph cardinality parity 100%, pending outbox eventually zero,
watermark monotonic, no missing evidence.

## S5. Long-horizon cross-session memory under a token budget

**Incident:** One user has 1 million transaction/agent events over thousands
of sessions. Only a small causal subset explains the latest action.

**User query:**

> 지난 90일 동안 반복적으로 interaction한 agent 중 이번 cancel 결정에
> 직접 영향을 준 기억만 근거와 함께 요약해줘.

**Comparison arms:** full history, recency-only, and Context Graph selection.
Keep model, answer contract, retrieved truth, and concurrency fixed.

**Required behavior:** Prompt receipt reports candidates, selected revisions,
exclusion reasons, token budget, compression, missing slots, and provenance.

**Gate:** at least 30% input-token reduction, no more than 1 percentage-point
quality loss, no provenance loss, no disclosure violation.

## S6. Partial multi-database federation

**Incident:** `user-hot` responds, `transaction-history` is slow, and
`settlement-audit` is unavailable.

**User query:**

> 현재 미완료 주문과 관련 과거 interaction 및 settlement 근거를 합쳐서
> 알려줘. 조회하지 못한 database가 있다면 숨기지 마.

**Required behavior:** Bounded parallel fan-out, per-target timeout,
deduplication, and an evidence bundle that names missing targets/slots. The LLM
cannot turn partial evidence into a fully supported claim.

**Gate:** healthy targets remain available; partial status and missing slots
are exact; no request exceeds the total latency budget.

## S7. Policy and ontology drift during an active session

**Incident:** A support session began under disclosure policy v3. A subject
restriction and ontology v4 are published before the next turn.

**User query:**

> 이전 답변과 동일한 주문 세부정보를 다시 보여줘. 달라진 정책 때문에
> 숨겨진 필드가 있다면 어떤 정책 버전이 적용됐는지만 알려줘.

**Required behavior:** etcd points to the active compact descriptor; durable
policy bodies remain in PostgreSQL/artifact storage. Disclosure runs before
prompting. Restricted values do not enter Mara, traces, or metrics.

**Gate:** forbidden-field leakage zero; receipt identifies policy/ontology
version change without revealing the denied value.

## S8. Chain reorganization after settlement evidence

**Incident:** An on-chain settlement was treated as canonical, then its block
was replaced.

**User query:**

> 이전에 confirmed라고 답한 settlement가 왜 reversed 되었으며 어떤 block
> revision이 기존 근거를 orphaned 처리했는가?

**Required behavior:** Append orphan and replacement revisions, issue
compensating projection work, preserve the earlier answer receipt, and cite the
new block provenance. Never delete audit history.

**Gate:** canonical aggregate parity, graph rebuild parity, old/new answer
reproducibility, and explicit reorg trace.

## S9. Model output degradation and provider throttling

**Incident:** MiniMax returns a reasoning preamble, fenced JSON, an evidence
echo plus answer array, then one call is rate-limited.

**User query:** Any grounded transaction explanation from S1-S8.

**Required behavior:** Select the unique object satisfying the output schema,
normalize provenance shape, retry only within budget, and never persist raw
reasoning. Invalid output fails closed rather than becoming an answer.

**Gate:** schema validity, exact provenance, leakage zero, bounded retry, and
provider error visible in `gen_ai.chat`.

## S10. Certificate rotation during concurrent retrieval

**Incident:** An intra-cluster certificate rotates while QPS traffic reaches
coordinators, data instances, and replicas.

**Operator query:**

> 인증서 reload 전후 handshake failure, replica lag, partial graph answer,
> causal stale rate가 어떻게 변했는가?

**Required behavior:** Existing/new connections recover within budget, no
plaintext fallback occurs, reload outcome is observable, and application
answers remain explicit when graph access is partial.

**Current gate status:** blocked for the current DozerDB 5.26.3.0 image because
no matching dynamic TLS reload/SSL policy settings were exposed. Run against a
TLS-enabled Neo4j Enterprise profile before making the claim.

## Scorecard contract

Every scenario emits:

```json
{
  "scenario_id": "S1",
  "dataset_manifest": "...",
  "service_versions": {},
  "concurrency": 8,
  "memory_sequence": 635,
  "projection_watermark": 635,
  "support_status": "supported",
  "required_slots": [],
  "missing_slots": [],
  "provenance_coverage": 1.0,
  "disclosure_violations": 0,
  "latency_ms": {},
  "trace_id": "...",
  "live_services": [],
  "skipped_gates": []
}
```

Mocks may exercise the same assertions but cannot populate `live_services` or
serve as performance evidence.

