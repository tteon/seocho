"""Build the three context forms — vector, graph, both — under one budget.

The behaviour layer of the testbed. No GPU, no serving engine: this only decides
what text each arm puts in front of the model, which is the variable the whole
comparison turns on.

Two confounds are already documented in `ADR-0105` and both are designed out
here, because either one alone makes the result unattributable:

  serializer  the graph lane answered from typed structure while its stored raw
              text was withheld, against a vector lane serving raw chunks
  budget      the fix over-corrected and emitted the whole 225K-char novel —
              ~483K chars per question against vector's top-4 chunks, so any
              win could be context quantity

How this differs from ADR-0105's framing, and why the serializer confound does
not apply. That ADR compared *systems* (does graph retrieval beat vector
retrieval), where withholding text the graph holds is unfair. This compares
*context forms* given the same knowledge: every arm is built from the same gold
facts, so structure-only vs prose-only vs both is the intended contrast rather
than a handicap. The knowledge-parity check is what makes that legitimate, and
it is asserted mechanically rather than assumed.

  vector          the supporting passages, prose, relations implicit
  graph           the same facts as `(subject) -[RELATION]-> (object)`, deduplicated
  both            both forms, each at HALF the budget
  graph_unstructured
                  the same triples with the relation markup stripped to a flat
                  bag of terms — same facts, same order, near-identical length,
                  no structure

The fourth arm is the control that isolates structure, and it is the second
design of it. The first was `vector_matched`: passages trimmed to the graph
arm's actual character length. That arm was invalid and the first run proved it
— the gold string was absent from 8 of 12 contexts, because prose stating a
fact is inherently longer than the triple stating it, so trimming to the
triple's length drops the fact. Its 1/12 measured information deprivation, not
compactness, and could not tell "structure helped" from "less text helped"
(seocho-alm).

Length cannot be held constant while facts are, so length stops being a control
and becomes an outcome — reported per arm, in tokens, by the runner.
`graph_unstructured` varies only the thing under test: it carries exactly the
same triples in exactly the same order, with `(A) -[REL]-> (B)` flattened to
`A REL B`. Same facts, same units, same sequence, and within a few percent on
length. If `graph` beats it, stated structure is doing the work; if they tie,
the graph form's advantage is compression and deduplication, which the token
counts already measure.

The `both` arm is the other thing people get wrong. If it were graph + vector
concatenated it would carry twice the tokens, and a `both` win would be budget,
not complementarity. It gets the same total as the other two.

Budget is enforced, reported, and auditable — `budget_unit` says whether it was
counted in tokens or characters, so the two are never silently interchanged.
Pass `--tokenizer` for real token accounting; without it the unit is characters
and every output row says so.

Usage:
    python scripts/serve_track/make_context_arms.py \\
        --questions outputs/serve_track/questions.jsonl \\
        --out outputs/serve_track/arms.jsonl --budget 2000
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

SCHEMA_VERSION = 1
ARMS = ("vector", "graph", "both", "graph_unstructured")


def _char_counter(text: str) -> int:
    return len(text)


def _make_counter(tokenizer_name: Optional[str]) -> Tuple[Callable[[str], int], str]:
    """Return (counter, unit). Falls back to characters, loudly."""
    if not tokenizer_name:
        return _char_counter, "chars"
    try:
        from transformers import AutoTokenizer

        tok = AutoTokenizer.from_pretrained(tokenizer_name)
        return (lambda t: len(tok.encode(t, add_special_tokens=False))), "tokens"
    except Exception as exc:  # noqa: BLE001 - the fallback must be visible, not silent
        print(f"WARN: tokenizer {tokenizer_name!r} unavailable ({type(exc).__name__}); "
              f"falling back to character budget")
        return _char_counter, "chars"


def _dedup(items: List[str]) -> List[str]:
    """Order-preserving dedup. The graph form's whole point is saying a thing once."""
    seen, out = set(), []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def render_vector(passages: List[str]) -> List[str]:
    return _dedup([p.strip() for p in passages if p and p.strip()])


def render_graph(edges: List[List[str]]) -> List[str]:
    lines = []
    for edge in edges:
        if len(edge) != 3:
            continue
        subject, relation, obj = (str(x).strip() for x in edge)
        lines.append(f"({subject}) -[{relation.upper().replace(' ', '_')}]-> ({obj})")
    return _dedup(lines)


def render_graph_unstructured(edges: List[List[str]]) -> List[str]:
    """The same triples with the markup stripped: `(A) -[REL]-> (B)` -> `A REL B`.

    Same facts, same order, same number of units — only the structural markers
    are gone. This is the arm that isolates whether STATED structure helps, as
    opposed to the compression and deduplication the graph form also brings.
    Relation names keep their underscores rather than being re-spaced, so the
    two arms differ in punctuation only and stay within a few percent on length.
    """
    lines = []
    for edge in edges:
        if len(edge) != 3:
            continue
        subject, relation, obj = (str(x).strip() for x in edge)
        lines.append(f"{subject} {relation.upper().replace(' ', '_')} {obj}")
    return _dedup(lines)


def _fill(units: List[str], budget: int, count: Callable[[str], int],
          joiner: str = "\n") -> Tuple[str, int, int]:
    """Greedily take whole units until the next one would exceed the budget.

    Whole units, never a truncated one: a half-written triple or a clipped
    sentence changes what the arm *means*, not merely how long it is.
    """
    kept: List[str] = []
    used = 0
    for unit in units:
        cost = count(unit) + (count(joiner) if kept else 0)
        if used + cost > budget:
            continue
        kept.append(unit)
        used += cost
    return joiner.join(kept), used, len(kept)


def build_arms(item: Dict[str, Any], budget: int,
               count: Callable[[str], int], unit: str) -> Dict[str, Any]:
    passages = render_vector(item.get("corpus") or [])
    triples = render_graph(item.get("gold_edges") or [])

    vector_text, vector_used, vector_n = _fill(passages, budget, count)
    graph_text, graph_used, graph_n = _fill(triples, budget, count)

    # `both` is capped at what the LARGEST single arm actually used, not at the
    # nominal budget. Capping at the budget is not enough and the first run proved
    # it: at budget=2000 nothing came close (max 869), so the split never engaged
    # and `both` degenerated to graph+vector concatenated in 12 of 12 items — the
    # doubling confound ADR-0105 recorded, reappearing because a loose budget
    # silently disables the guard. Tying the cap to observed usage makes the
    # guarantee hold whether or not the budget binds.
    both_budget = min(budget, max(vector_used, graph_used))
    half = both_budget // 2
    both_graph, both_graph_used, both_graph_n = _fill(triples, half, count)
    both_vector, both_vector_used, both_vector_n = _fill(
        passages, both_budget - both_graph_used, count
    )
    both_text = "\n".join(t for t in (both_graph, both_vector) if t)

    # Structure control: the same triples, same order, markup stripped. Facts and
    # units are held constant and only stated structure varies, which the
    # replaced `vector_matched` arm could not do — see the module docstring.
    flat = render_graph_unstructured(item.get("gold_edges") or [])
    flat_text, flat_used, flat_n = _fill(flat, budget, count)

    return {
        "schema_version": SCHEMA_VERSION,
        "id": item.get("id"),
        "question": item.get("question"),
        "answer": item.get("answer"),
        "excluded": item.get("excluded") or [],
        "strata": item.get("strata", {}),
        "budget": budget,
        "budget_unit": unit,
        "arms": {
            "vector": {"context": vector_text, "used": vector_used, "units": vector_n},
            "graph": {"context": graph_text, "used": graph_used, "units": graph_n},
            "both": {
                "context": both_text,
                "used": both_graph_used + both_vector_used,
                "units": both_graph_n + both_vector_n,
            },
            "graph_unstructured": {
                "context": flat_text, "used": flat_used, "units": flat_n
            },
        },
        # Availability, not fairness: an item whose graph form is empty cannot
        # speak to the comparison at all and must be excluded, not scored as a
        # vector win.
        "comparable": bool(vector_text) and bool(graph_text),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("outputs/serve_track/arms.jsonl"))
    parser.add_argument("--budget", type=int, default=2000)
    parser.add_argument("--tokenizer", default=None,
                        help="HF tokenizer name; without it the budget is characters")
    args = parser.parse_args()

    count, unit = _make_counter(args.tokenizer)
    rows = [json.loads(line) for line in args.questions.read_text(encoding="utf-8").splitlines() if line.strip()]

    built = [build_arms(row, args.budget, count, unit) for row in rows]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for row in built:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    comparable = [r for r in built if r["comparable"]]
    print(f"wrote {len(built)} items to {args.out} (budget {args.budget} {unit})")
    print(f"  comparable (both forms non-empty): {len(comparable)}")
    for arm in ARMS:
        used = [r["arms"][arm]["used"] for r in comparable]
        units = [r["arms"][arm]["units"] for r in comparable]
        if used:
            print(f"  {arm:7s} mean_used={sum(used)/len(used):7.1f} {unit}  "
                  f"mean_units={sum(units)/len(units):4.1f}  max_used={max(used)}")
    print("\nEvery arm is capped at the same budget and `both` splits it rather than "
          "doubling it.\n`graph` vs `graph_unstructured` is the structure test: same "
          "facts, same order,\nsame count, markup only. `graph` vs `vector` is a "
          "compression measurement, not\na fair accuracy contest — read the token "
          "counts the runner prints alongside it.")


if __name__ == "__main__":
    main()
