# ADR: SDCR product boundary

## Decision

Adopt isolated graph views, slot-aware conservative routing, protected
evidence filtering, conflict verification, and decision receipts as the first
SEOCHO product slice derived from the FinDER study.

## Rationale

The study shows that graph diversity alone does not guarantee answer gains.
Routing correctness and evidence isolation are therefore product primitives;
network statistics are tie-break signals rather than an authorization or
selection authority.

## Non-goals

This decision does not add GNN inference, full OWL reasoning to the hot path,
or a claim that LLM-as-a-judge replaces human annotation.
