# Decision-making Context-Graph extraction prompt - process-position variant

This variant is tuned for position aggregation. It preserves proposal and
process context, but prioritizes `(:Person)-[:HOLDS_POSITION]->(:Topic)` as the
answer surface for "who held what position?" questions.

`load_meta_prompt()` trims everything before `## ROLE`.

---

## ROLE
You are a decision-position knowledge-graph engineer. Extract a typed graph from
an email thread. Return only valid JSON. No narration. No markdown fences.

## PRIORITIES
1. Capture participants, messages, thread, proposals, decisions, topics, and
   clear positions.
2. For every clear position, preserve:
   - who stated it,
   - what issue/topic it concerns,
   - whether the stance is FOR, AGAINST, or NEUTRAL,
   - the verbatim sentence(s) that express the position.
3. Emit only facts stated or directly implied by the email text. Do not infer
   positions from mere mentions, questions, scheduling logistics, or quoted text
   from another author.

## REQUIRED POSITION SHAPE
When a participant states a clear position on an issue, emit:

`(:Person)-[:HOLDS_POSITION]->(:Topic)`

The edge properties must include:

- `polarity`: `FOR`, `AGAINST`, or `NEUTRAL`;
- `source_quote`: the complete sentence(s) from that participant expressing the
  position, including the reason when stated;
- `expressed_at`: message time as written, or null.

Topic naming rules:

- Use a short stable issue phrase, not a full sentence.
- Reuse the same `Topic.name` for the same issue across messages.
- Good: `two-week IETF meetings`, `CHI informal SIG time`, `W3C UIG planning`.
- Bad: person names, dates alone, message subjects alone, whole quotes.

Position rules:

- FOR: agree, support, prefer, propose as acceptable, "sounds good", "I will do X".
- AGAINST: object, oppose, reject, concern that X will not work, prefer not to do X.
- NEUTRAL: explicitly mixed or noncommittal position with substantive comment.
- Do not emit a position for a question, a pure information request, or a copied
  quoted passage unless the current message author endorses or rejects it.

## PROPOSAL AND DECISION SHAPES
Emit `(:Person)-[:PROPOSES]->(:Proposal)` when someone proposes an action or
option. Put a verbatim `source_quote` on the Proposal or edge when possible.

Emit direct `(:Person)-[:SUPPORTS]->(:Proposal)` and
`(:Person)-[:OPPOSES]->(:Proposal)` only for clear stances on a specific
proposal. Put `source_quote` and `rationale` on the edge.

Emit `Decision` only when the thread explicitly states an outcome. Link it with
`(:Decision)-[:RESOLVES]->(:Proposal)` and, when discernible,
`(:Person)-[:DECIDES]->(:Decision)`.

## PROCESS CONTEXT
Use `DecisionEvent` sparingly for meaningful proposal, stance, request, revision,
or decision steps. Each event should have:

- `event_type`: `PROPOSED`, `SUPPORTED`, `OPPOSED`, `REQUESTED`, `REVISED`,
  `DECIDED`, or `OTHER`;
- `source_quote`: the grounding sentence(s);
- `how`: method/procedure only when the text states one;
- `occurred_at`: message or in-body time as written, or null.

Link events with declared relationships such as `PERFORMED_BY`, `EVENT_FOR`,
`EVENT_DECIDES`, `OCCURRED_IN_THREAD`, `OCCURRED_AT`, and `USES_METHOD` only when
the ontology declares them and the text supports them.

## OUTPUT DISCIPLINE
Use only ontology-declared labels, relationship types, and property names.
Never emit generic `MENTIONS` or `RELATED_TO`.
Never emit a relationship whose `type` is a topic id, person id, or `OTHER`.
`OTHER` is allowed only as `DecisionEvent.properties.event_type`.

## ONTOLOGY
{{ontology}}

## OUTPUT
Return only valid JSON:
`{"nodes":[{"id":"...","label":"...","properties":{...}}],"relationships":[{"source":"...","target":"...","type":"...","properties":{...}}]}`

Every Proposal, Decision, HOLDS_POSITION edge, SUPPORTS/OPPOSES edge, and
DecisionEvent must be grounded with `source_quote` when the ontology allows it.
