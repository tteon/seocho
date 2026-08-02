#!/usr/bin/env python3
"""Does the pipe connect at all — question in, Cypher out, rows back?

Before designing a comparison of schema descriptions it is worth establishing
that a model handed one of them can write a query this store will execute. That
is not obvious and it is cheap to check, and designing a large experiment on an
untested mechanism is the most common way to waste one.

Deliberately small: a handful of questions, one model, two descriptions. What it
reports is not accuracy — the sample cannot support that — but whether each
stage happens at all:

    generated    the model returned something that looks like a query
    parses       the store accepted it
    grounded     it named only labels and properties that exist
    ran          it executed without error
    returned     it came back with rows

A stage failing everywhere is a mechanism to fix before the real run. A stage
failing sometimes is what the real run is for.

It also looks at the other half of the question, which is easy to forget: what
comes back has to be usable by the answering model. Returning whole nodes fills
the answer prompt with workspace identifiers and provenance keys, so the size of
a projected row and the share of it that is bookkeeping are reported too.

Two things sit between the model and the store, and both are deliberately
condition-independent so they cannot be confused with the thing under test.

A **syntax repair** rewrites deprecated Cypher the model still writes — the
first run failed three of six on `exists(n.prop)`, removed in Neo4j 5. That is
not a schema-description failure and letting it stand would bury the comparison
under noise the descriptions cannot affect. The repair is deterministic, applied
identically everywhere, and every firing is recorded, so how often it was needed
is itself a result.

A **single retry** hands the store's error back and asks again. Also identical
across conditions, also counted. One, not many: a loop would let a weak
description be rescued by persistence, which is exactly what the comparison is
trying to see.

Costs a few model calls on the loosest-quota model.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for path in (str(ROOT / "experiments/minimal"), str(HERE), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from dotenv import dotenv_values  # noqa: E402

for _key, _value in dotenv_values(ROOT / ".env").items():
    if _value is not None:
        os.environ.setdefault(_key, _value)

URI = os.environ.get("SEOCHO_NEO4J_URI", "bolt://localhost:7687")
OUT_ROOT = ROOT / "outputs/minimal"

SYSTEM = """You write Cypher for a Neo4j database of facts extracted from SEC filings.

{schema}

Rules:
- Return only the Cypher query. No explanation, no code fence.
- Every node carries `name`. Figures are text in `value` and, where a figure
  could be parsed, a number in `value_numeric`. Compare with `value_numeric`.
- Limit results to 20 rows.
"""

QUESTIONS = [
    "What is the recorded value of Sales for 2024?",
    "Which recorded figures are larger than one million?",
    "How many facts carry a value for the year 2023?",
    "Which facts have a recorded value and a year, listed with both?",
    "What is the largest recorded figure and what is it called?",
]


def auth() -> tuple[str, str]:
    return (os.environ.get("NEO4J_USER", "neo4j"),
            os.environ.get("NEO4J_PASSWORD", ""))


# Deprecated forms a model trained on older Cypher still produces. Each is a
# rewrite with identical meaning, not a correction of intent.
REPAIRS: list[tuple[str, str, str]] = [
    (r"\bexists\s*\(\s*([A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*)\s*\)",
     r"\1 IS NOT NULL",
     "exists(n.prop) was removed in Neo4j 5; IS NOT NULL replaces it"),
    (r"\bid\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)", r"elementId(\1)",
     "id() is deprecated and elementId() is the supported form"),
    (r"\bsize\s*\(\s*\(", "COUNT { (",
     "size() over a pattern was replaced by a COUNT subquery"),
]


def repair(cypher: str) -> tuple[str, list[str]]:
    """Rewrite deprecated syntax, and say what was rewritten."""
    fired = []
    for pattern, replacement, why in REPAIRS:
        repaired, count = re.subn(pattern, replacement, cypher)
        if count:
            fired.append(why)
            cypher = repaired
    return cypher, fired


def strip_fence(text: str) -> str:
    body = str(text or "").strip()
    body = re.sub(r"^```(?:cypher|sql)?\s*", "", body)
    body = re.sub(r"\s*```$", "", body)
    return body.strip()


def descriptions(driver, database: str) -> dict[str, str]:
    """The two ends of the comparison: everything, and the ninety-percent core."""
    with driver.session(database=database) as session:
        labels = {r["l"]: r["c"] for r in session.run(
            "MATCH (n) UNWIND labels(n) AS l RETURN l, count(*) AS c "
            "ORDER BY c DESC")}
        rels = sorted({r["t"] for r in session.run(
            "MATCH ()-[r]->() RETURN DISTINCT type(r) AS t")})
        keys = sorted({r["k"] for r in session.run(
            "MATCH (n) UNWIND keys(n) AS k RETURN DISTINCT k AS k")})

    total = sum(labels.values()) or 1
    core, running = [], 0
    for label, count in sorted(labels.items(), key=lambda kv: -kv[1]):
        core.append(label)
        running += count
        if running / total >= 0.90:
            break
    domain = [k for k in keys if not k.startswith("_")]

    everything = (f"Node labels: {', '.join(sorted(labels))}\n"
                  f"Relationship types: {', '.join(rels)}\n"
                  f"Node properties: {', '.join(keys)}")
    focused = (f"Node labels: {', '.join(sorted(core))}\n"
               f"Relationship types: {', '.join(rels)}\n"
               f"Node properties: {', '.join(domain)}")
    return {"everything": everything, "ninety_percent_core": focused,
            "_labels": set(labels), "_keys": set(keys)}


def check_query(cypher: str, labels: set[str], keys: set[str]) -> dict[str, Any]:
    """Grounding, before execution: does it name things that exist?"""
    named_labels = set(re.findall(r":([A-Za-z_][A-Za-z0-9_]*)", cypher))
    named_props = set(re.findall(r"\.([A-Za-z_][A-Za-z0-9_]*)", cypher))
    invented_labels = sorted(named_labels - labels)
    invented_props = sorted(named_props - keys)
    # A comparison operator applied to the text property rather than the number.
    text_comparison = bool(re.search(r"\.value\s*[<>]", cypher))
    return {"labels_named": sorted(named_labels),
            "invented_labels": invented_labels,
            "invented_properties": invented_props,
            "grounded": not invented_labels and not invented_props,
            "compares_text_as_number": text_comparison}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", default="v2")
    ap.add_argument("--category", default="Financials")
    ap.add_argument("--model", default="gpt-oss-120b",
                    help="the loosest MARA quota, so this cannot starve a sweep")
    args = ap.parse_args()

    import observe
    from neo4j import GraphDatabase
    from seocho.store.llm import create_llm_backend

    database = (f"{re.sub(r'[^a-z0-9]', '', args.tag.lower())}cat"
                f"{re.sub(r'[^a-z0-9]', '', args.category.lower())}")

    run = observe.Run(OUT_ROOT, "query-smoke", {"decisive": {
        "tag": args.tag, "category": args.category, "model": args.model,
        "questions": QUESTIONS,
        "descriptions": ["everything", "ninety_percent_core"], "seed": 42}})

    driver = GraphDatabase.driver(URI, auth=auth())
    llm = create_llm_backend(provider="mara", model=args.model)
    rows: list[dict[str, Any]] = []
    try:
        with run.stage("describe", database=database) as out:
            described = descriptions(driver, database)
            labels, keys = described.pop("_labels"), described.pop("_keys")
            out.update({k: len(v) for k, v in described.items()})

        for name, schema in described.items():
            for question in QUESTIONS:
                with run.stage("ask", description=name,
                               question=question) as out:
                    started = time.perf_counter()
                    record: dict[str, Any] = {"description": name,
                                              "question": question}
                    try:
                        reply = llm.complete(
                            system=SYSTEM.format(schema=schema),
                            user=question, temperature=0.0, max_tokens=400)
                        cypher = strip_fence(
                            getattr(reply, "content", None)
                            or getattr(reply, "text", ""))
                    except Exception as exc:  # noqa: BLE001 — recorded
                        record.update({"stage_failed": "generation",
                                       "error": f"{type(exc).__name__}: {exc}"})
                        rows.append(record)
                        out.update(record)
                        continue

                    record["cypher_raw"] = cypher
                    cypher, fired = repair(cypher)
                    record["cypher"] = cypher
                    record["repairs_applied"] = fired
                    record["generated"] = bool(cypher)
                    record.update(check_query(cypher, labels, keys))

                    def execute(query: str):
                        with driver.session(database=database) as session:
                            return session.run(query).data()

                    result, error, retried = None, "", False
                    try:
                        result = execute(cypher)
                    except Exception as exc:  # noqa: BLE001 — recorded
                        error = f"{type(exc).__name__}: {str(exc)[:300]}"
                        # One retry with the store's own error handed back.
                        # Identical across conditions, so it cannot flatter one.
                        try:
                            retried = True
                            reply = llm.complete(
                                system=SYSTEM.format(schema=schema),
                                user=(f"{question}\n\nYour previous query "
                                      f"failed:\n{cypher}\n\nThe database "
                                      f"said:\n{error}\n\nWrite a corrected "
                                      f"query."),
                                temperature=0.0, max_tokens=400)
                            cypher, more = repair(strip_fence(
                                getattr(reply, "content", None)
                                or getattr(reply, "text", "")))
                            record["cypher_retry"] = cypher
                            record["repairs_applied"] += more
                            result = execute(cypher)
                            error = ""
                        except Exception as second:  # noqa: BLE001
                            error = f"{type(second).__name__}: {str(second)[:300]}"
                    record["retried"] = retried

                    if error:
                        record["ran"] = False
                        record["error"] = error
                    else:
                        record["ran"] = True
                        record["rows"] = len(result)
                        if result:
                            first = result[0]
                            flat = {k: v for k, v in first.items()}
                            nested = [v for v in first.values()
                                      if isinstance(v, dict)]
                            projected = nested[0] if nested else flat
                            book = sum(1 for k in projected
                                       if str(k).startswith("_"))
                            record["returned_keys"] = len(projected)
                            record["bookkeeping_keys_returned"] = book
                            record["row_characters"] = len(json.dumps(
                                first, default=str))
                            record["sample_row"] = json.dumps(
                                first, default=str)[:300]
                    record["seconds"] = round(time.perf_counter() - started, 2)
                    rows.append(record)
                    out.update({k: v for k, v in record.items()
                                if k not in ("cypher", "sample_row")})
    finally:
        driver.close()

    def share(predicate) -> str:
        hits = sum(1 for r in rows if predicate(r))
        return f"{hits}/{len(rows)}"

    payload = {
        "contract": "log2026.query_smoke.v1",
        "question": ("Does a model given a schema description write Cypher this "
                     "store will run, and is what comes back usable?"),
        "method": (f"{len(QUESTIONS)} questions x 2 descriptions on "
                   f"{args.model} against the {args.category} database; each "
                   f"query checked for grounding before execution and then "
                   f"executed"),
        "claim_boundary": ("A smoke test. It says whether each stage happens at "
                           "all, not how often it succeeds — the sample cannot "
                           "support a rate and none is reported."),
        "attempts": len(rows),
        "generated": share(lambda r: r.get("generated")),
        "grounded": share(lambda r: r.get("grounded")),
        "ran": share(lambda r: r.get("ran")),
        "returned_rows": share(lambda r: r.get("rows", 0) > 0),
        "compared_text_as_number": share(
            lambda r: r.get("compares_text_as_number")),
        "needed_syntax_repair": share(lambda r: r.get("repairs_applied")),
        "needed_a_retry": share(lambda r: r.get("retried")),
        "results": rows,
    }
    (run.dir / "query_smoke.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    print()
    print(f"{'description':20s} {'gen':>4s} {'grnd':>5s} {'ran':>4s} "
          f"{'rows':>5s}  question")
    for record in rows:
        mark = lambda k: "y" if record.get(k) else "n"  # noqa: E731
        print(f"{record['description']:20s} {mark('generated'):>4s} "
              f"{mark('grounded'):>5s} {mark('ran'):>4s} "
              f"{str(record.get('rows', '-')):>5s}  "
              f"{record['question'][:44]}")
    print(f"\ngenerated {payload['generated']}, grounded {payload['grounded']}, "
          f"ran {payload['ran']}, returned rows {payload['returned_rows']}")
    print(f"compared text with a numeric operator: "
          f"{payload['compared_text_as_number']}")
    print(f"needed the syntax repair: {payload['needed_syntax_repair']}, "
          f"needed a retry: {payload['needed_a_retry']}")
    reasons: dict[str, int] = {}
    for record in rows:
        for why in record.get("repairs_applied", []):
            reasons[why] = reasons.get(why, 0) + 1
    for why, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
        print(f"  {count:3d}  {why}")

    returning = [r for r in rows if r.get("returned_keys")]
    if returning:
        book = sum(r["bookkeeping_keys_returned"] for r in returning)
        total = sum(r["returned_keys"] for r in returning)
        chars = sum(r["row_characters"] for r in returning) / len(returning)
        print(f"\nwhat comes back: {total} keys across {len(returning)} rows, "
              f"{book} of them bookkeeping, {chars:.0f} characters a row")
        print("  " + returning[0]["sample_row"][:220])
    for record in rows:
        if record.get("error"):
            print(f"\n  {record['description']} / {record['question'][:38]}")
            print(f"    {record['error'][:160]}")
            if record.get("cypher"):
                print(f"    {record['cypher'][:160]}")

    run.finish({"generated": payload["generated"], "ran": payload["ran"],
                "returned_rows": payload["returned_rows"],
                "artifact": str((run.dir / "query_smoke.json").relative_to(ROOT))})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
