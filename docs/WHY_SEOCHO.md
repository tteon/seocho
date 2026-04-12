# Why SEOCHO

SEOCHO is not trying to be the easiest generic memory demo.

It is built for teams that want a graph-native, ontology-governed contract for
agent indexing and querying.

## The Core Idea

One ontology should control the parts of the system that usually drift apart:

- extraction prompts
- query planning prompts
- SHACL-derived validation
- graph constraints
- denormalization rules
- runtime semantic artifacts

In SEOCHO, the same ontology object can be used locally in the SDK and promoted
into runtime-safe artifacts instead of maintaining a second schema layer.

## Why Not Generic Memory Alone

Generic AI memory is good at low-friction demos. It is usually weaker at:

- enforcing a stable graph contract
- making query behavior inspectable
- keeping extraction and querying aligned
- governing schema evolution over time
- working cleanly with Neo4j or DozerDB as the primary store

SEOCHO is stronger when you care about those tradeoffs.

## What SEOCHO Treats As First-Class

### 1. Ontology

The ontology is not just reference documentation. It feeds:

- extraction context
- query context
- validation
- constraint generation
- runtime artifact generation

### 2. Graph-Native Runtime

SEOCHO assumes graph storage is central, not an afterthought. Local SDK mode and
runtime APIs are designed around Bolt-backed graph stores such as Neo4j and
DozerDB.

### 3. Governed Semantic Artifacts

The runtime path is not only prompt text. SEOCHO can derive approved artifacts,
typed prompt context, and semantic drafts from the same ontology contract.

### 4. Deterministic Direction

SEOCHO is ontology-aware today and is moving further toward deterministic query
profiles instead of relying only on free-form text-to-Cypher behavior.

## Where SEOCHO Is Strongest

- internal enterprise knowledge graphs
- regulated or audited data environments
- graph-backed agent workflows that need repeatability
- teams already committed to Neo4j or DozerDB
- projects that need ontology governance, diff, and migration warnings

## Where SEOCHO Is Not Trying To Win

- lowest-friction zero-config memory demos
- vector-only retrieval abstractions
- tools that intentionally avoid schema and governance

## How To Use SEOCHO

Choose the surface that matches your job:

- [Quickstart](QUICKSTART.md): shortest local success path
- [Python SDK Quickstart](PYTHON_INTERFACE_QUICKSTART.md): ontology-first local
  engine and HTTP client usage
- [Apply Your Data](APPLY_YOUR_DATA.md): bring your own records into the graph
- [Files and Artifacts](FILES_AND_ARTIFACTS.md): inspect ontology, runtime, and
  trace files directly

## One Contract, Multiple Surfaces

SEOCHO is strongest when these three surfaces stay aligned:

1. GitHub and repo docs define the ontology and runtime contract.
2. The local SDK uses that ontology directly for indexing and querying.
3. The runtime consumes approved artifacts built from the same ontology.

That is the product story: one ontology, one graph contract, one runtime path.
