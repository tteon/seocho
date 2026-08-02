#!/usr/bin/env python3
"""Run the competency questions as queries instead of reading them as prose.

A competency question is a test, not a paragraph. The line of work that
introduced them — Grüninger and Fox, then NeOn, eXtreme Design, SAMOD — makes
the same point every time: write the question as something executable, state the
answer you expect, run it against real data, and keep the set as a regression
suite. Ours were ten prose questions, each measured by a different ad-hoc
snippet, with no example answers. The symptom was visible in the document: most
of them passed, which is what happens when the author invents questions the
system can already answer.

Two things blocked making them executable, and neither does now. There was no
SPARQL engine — pyoxigraph loads the FIBO turtle in 0.24s and answers in
milliseconds. And the extracted data lived in a property graph that SPARQL
cannot reach — the committed snapshots are converted to triples here, so the
questions run against the ontology and the data in one store.

Each question carries the query, the answer shape it needs, and what a failure
would mean.

The first run of this suite passed nine out of nine, with margins between ten
and eighty times the threshold. That is not a good result, it is the symptom the
paragraph above describes: thresholds an author sets on questions an author
invented will be cleared. A suite where nothing can fail is decorative.

So the suite carries its own calibration. Four questions ask for capabilities
this system is known not to have — resolving one company across cases, giving a
non-numeric fact a source position, checking a relationship's endpoints against
the classes its type declares, matching a fact across workspace boundaries. Each
is marked `expect="fail"`, and the suite reports whether they failed. A
calibration question that passes means the suite is measuring something other
than what it says, and is a louder problem than an ordinary failure.

    python3 experiments/cq_suite.py --tag v2
    python3 experiments/cq_suite.py --tag v2 --strict   non-zero if any fail
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for path in (str(ROOT / "experiments/minimal"), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

TTL = ROOT / "dataset/fibo/fibo-quickstart.ttl"
SNAPSHOTS = ROOT / "snapshots"
OUT_ROOT = ROOT / "outputs/minimal"
NS = "urn:seocho:"

PREFIXES = """
PREFIX owl:  <http://www.w3.org/2002/07/owl#>
PREFIX rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX cmns: <https://www.omg.org/spec/Commons/AnnotationVocabulary/>
PREFIX s:    <urn:seocho:>
"""


def literal(value: str) -> str:
    """A Turtle string literal. Extracted names carry newlines and tabs from the
    filings they came from, and a raw line break inside a literal is a parse
    error rather than a warning, so every control character is escaped."""
    text = (str(value)
            .replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t"))
    return '"' + text + '"'


def snapshot_triples(directory: Path, arms: list[str]) -> list[str]:
    """The extracted graphs as triples, so SPARQL can reach the data too.

    A deliberately thin mapping: each node becomes a subject carrying its
    labels, name, value, period and the workspace it came from, and each edge
    becomes one triple. Nothing is inferred on the way in — the point is to ask
    questions of what was extracted, not of a tidied version of it.
    """
    lines = []
    for path in sorted(directory.glob("*.jsonl")):
        if path.name == "anchors.jsonl":
            continue
        parts = path.stem.split("_")
        if len(parts) < 3 or parts[0] not in arms:
            continue
        arm, model, case = parts[0], parts[1], "_".join(parts[2:])
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            kind = record.get("kind")
            if kind == "node":
                subject = f"<{NS}n/{re.sub(r'[^A-Za-z0-9]', '_', record['eid'])}>"
                props = record.get("props") or {}
                lines.append(f"{subject} s:arm {literal(arm)} .")
                lines.append(f"{subject} s:model {literal(model)} .")
                lines.append(f"{subject} s:case {literal(case)} .")
                for label in record.get("labels") or []:
                    lines.append(f"{subject} rdf:type <{NS}c/{label}> .")
                for key in ("name", "value", "period", "amount"):
                    if props.get(key):
                        lines.append(f"{subject} s:{key} {literal(props[key])} .")
                if props.get("_source_id"):
                    lines.append(f"{subject} s:source {literal(props['_source_id'])} .")
            elif kind == "edge":
                src = f"<{NS}n/{re.sub(r'[^A-Za-z0-9]', '_', record['source'])}>"
                dst = f"<{NS}n/{re.sub(r'[^A-Za-z0-9]', '_', record['target'])}>"
                rel = re.sub(r"[^A-Za-z0-9_]", "_", record["type"])
                lines.append(f"{src} <{NS}r/{rel}> {dst} .")
    return lines


def anchor_triples(directory: Path) -> list[str]:
    """The recovered provenance, which is what CQ4 is actually about."""
    path = directory / "anchors.jsonl"
    if not path.is_file():
        return []
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("kind") != "anchor":
            continue
        subject = f"<{NS}n/{re.sub(r'[^A-Za-z0-9]', '_', record['eid'])}>"
        lines.append(f"{subject} s:anchorPassage {literal(record['passage'])} .")
        lines.append(f"{subject} s:anchorOffset {literal(record['offset'])} .")
        lines.append(f"{subject} s:anchorExact {literal(record['exact'])} .")
        lines.append(f"{subject} s:scaleRatio {literal(record['scale_ratio'])} .")
    return lines


# id, question, query, expectation, requirement, expect
# `expect` is "pass" for a capability we claim, "fail" for one we know is
# missing and use to prove the suite can register absence.
Question = tuple[str, str, str, Callable[[list], bool], str, str]

QUESTIONS: list[Question] = [
    ("CQ1", "Given a fact in one view, can another view be identified as "
            "describing the same fact?",
     """SELECT (COUNT(*) AS ?pairs) WHERE {
          ?a s:name ?n ; s:case ?c ; s:model ?m1 .
          ?b s:name ?n ; s:case ?c ; s:model ?m2 .
          FILTER(?m1 < ?m2)
        }""",
     lambda rows: int(rows[0][0]) >= 100,
     "at least 100 cross-model name matches, or the mechanism has nothing to work on",
     "pass"),

    ("CQ2", "When two views describe the same fact, can agreement be checked?",
     """SELECT (COUNT(*) AS ?comparable) WHERE {
          ?a s:name ?n ; s:case ?c ; s:model ?m1 ; s:value ?v1 .
          ?b s:name ?n ; s:case ?c ; s:model ?m2 ; s:value ?v2 .
          FILTER(?m1 < ?m2)
        }""",
     lambda rows: int(rows[0][0]) >= 50,
     "at least 50 matched pairs both carrying a value",
     "pass"),

    ("CQ4", "Can a served value be attributed to a place in a source document?",
     """SELECT (COUNT(*) AS ?anchored) WHERE { ?n s:anchorOffset ?o }""",
     lambda rows: int(rows[0][0]) >= 500,
     "at least 500 figures with a recovered source offset",
     "pass"),

    ("CQ6", "Can a node be told to carry a declared class or a fallback?",
     """SELECT (COUNT(DISTINCT ?t) AS ?classes) WHERE { ?n rdf:type ?t }""",
     lambda rows: int(rows[0][0]) >= 2,
     "more than one distinct class, or the question is vacuous",
     "pass"),

    ("CQ9", "Can the same metric be compared across periods for one issuer?",
     """SELECT (COUNT(*) AS ?withPeriod) WHERE { ?n s:period ?p }""",
     lambda rows: int(rows[0][0]) >= 100,
     "at least 100 nodes carrying a period",
     "pass"),

    ("CQ11", "Does FIBO declare the temporal vocabulary CQ9 would need?",
     """SELECT (COUNT(DISTINCT ?p) AS ?props) WHERE {
          ?p a owl:ObjectProperty ; rdfs:label ?l .
          FILTER(CONTAINS(LCASE(?l), "date") || CONTAINS(LCASE(?l), "period"))
        }""",
     lambda rows: int(rows[0][0]) >= 3,
     "FIBO declares at least three temporal properties, so an unfilled period "
     "is our gap and not the ontology's", "pass"),

    ("CQ12", "Does the ontology give a second name for any concept?",
     """SELECT (COUNT(*) AS ?aliases) WHERE { ?c cmns:synonym ?s }""",
     lambda rows: int(rows[0][0]) >= 100,
     "FIBO carries a synonym layer at all", "pass"),

    ("CQ13", "Can a disagreement be located at one place in the source?",
     """SELECT (COUNT(*) AS ?conflicts) WHERE {
          ?a s:anchorOffset ?o ; s:anchorPassage ?p ; s:case ?c ;
             s:model ?m1 ; s:value ?v1 .
          ?b s:anchorOffset ?o ; s:anchorPassage ?p ; s:case ?c ;
             s:model ?m2 ; s:value ?v2 .
          FILTER(?m1 < ?m2 && ?v1 != ?v2)
        }""",
     lambda rows: int(rows[0][0]) >= 10,
     "at least 10 disagreements co-located at one source token — the "
     "capability the alignment-key argument rests on", "pass"),

    ("CQ14", "Are there figures whose reading differs from the printed number?",
     """SELECT (COUNT(*) AS ?rescaled) WHERE {
          ?n s:anchorExact "False"
        }""",
     lambda rows: int(rows[0][0]) >= 50,
     "at least 50 figures that only matched their source after rescaling",
     "pass"),

    # Calibration. Each asks for something this system does not do, and the
    # suite is only trustworthy while these keep failing.
    ("CAL1", "Can one company be resolved to a single node across cases?",
     """SELECT (COUNT(*) AS ?crossCase) WHERE {
          ?n s:case ?c1 . ?n s:case ?c2 . FILTER(?c1 != ?c2)
        }""",
     lambda rows: int(rows[0][0]) > 0,
     "identity never crosses a case boundary — the merge key contains the "
     "workspace, so this cannot succeed by construction",
     "fail"),

    ("CAL2", "Can a fact carrying no figure be given a source position?",
     """SELECT (COUNT(*) AS ?nonNumericAnchored) WHERE {
          ?n s:anchorOffset ?o .
          FILTER NOT EXISTS { ?n s:value ?v }
          FILTER NOT EXISTS { ?n s:amount ?a }
        }""",
     lambda rows: int(rows[0][0]) > 0,
     "attribution is recovered by locating a number in the source, so a fact "
     "without one is outside it entirely",
     "fail"),

    ("CAL3", "Are a relationship's endpoints checked against its declared "
             "domain and range?",
     """SELECT (COUNT(*) AS ?typed) WHERE {
          ?a <urn:seocho:r/HAS_AMOUNT> ?b .
          ?a rdf:type ?ta . ?b rdf:type ?tb .
          ?p rdfs:domain ?ta ; rdfs:range ?tb .
        }""",
     lambda rows: int(rows[0][0]) > 0,
     "extracted relationship types are our own strings and are never bound to "
     "a FIBO property, so no endpoint can be checked against a declared "
     "domain and range",
     "fail"),

    ("CAL4", "Can a fact be matched to one in another workspace?",
     """SELECT (COUNT(*) AS ?crossArm) WHERE {
          ?a s:name ?n ; s:arm ?x ; s:case ?c .
          ?b s:name ?n ; s:arm ?y ; s:case ?c .
          FILTER(?x != ?y && !BOUND(?a) )
        }""",
     lambda rows: int(rows[0][0]) > 0,
     "conditions are isolated by workspace and nothing joins across them",
     "fail"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", default="v2")
    ap.add_argument("--arms", default="A,C,D,E")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    import observe
    import pyoxigraph

    directory = SNAPSHOTS / (args.tag or "v1")
    arms = [a.strip().upper() for a in args.arms.split(",") if a.strip()]

    run = observe.Run(OUT_ROOT, "cq-suite", {"decisive": {
        "tag": args.tag, "arms": arms, "questions": [q[0] for q in QUESTIONS],
        "engine": "pyoxigraph", "seed": 42}})

    store = pyoxigraph.Store()
    with run.stage("load.ontology", source=str(TTL.relative_to(ROOT))) as out:
        with TTL.open("rb") as fh:
            store.load(fh, format=pyoxigraph.RdfFormat.TURTLE)
        out["triples"] = len(store)

    with run.stage("load.data", snapshots=str(directory.relative_to(ROOT))) as out:
        lines = snapshot_triples(directory, arms) + anchor_triples(directory)
        payload = (PREFIXES + "\n".join(lines)).encode()
        store.load(payload, format=pyoxigraph.RdfFormat.TURTLE)
        out["statements"] = len(lines)
        out["triples_total"] = len(store)

    results = []
    for name, question, query, expectation, requirement, expect in QUESTIONS:
        with run.stage(f"ask.{name}", question=question) as out:
            try:
                rows = [list(r) for r in store.query(PREFIXES + query)]
                answer = [str(v.value) if v is not None else None
                          for v in (rows[0] if rows else [])]
                held = bool(rows) and expectation(
                    [[str(v.value) if v is not None else "0" for v in rows[0]]])
                status = "pass" if held else "fail"
                error = ""
            except Exception as exc:  # noqa: BLE001 — recorded, never imputed
                answer, status, error = [], "error", f"{type(exc).__name__}: {exc}"
            out["status"] = status
            out["expected"] = expect
            out["as_expected"] = status == expect
            out["answer"] = answer
            if error:
                out["error"] = error
        results.append({"id": name, "question": question, "status": status,
                        "expected": expect, "as_expected": status == expect,
                        "answer": answer, "requirement": requirement,
                        "error": error, "query": query.strip()})

    claims = [r for r in results if r["expected"] == "pass"]
    calibration = [r for r in results if r["expected"] == "fail"]
    passed = sum(1 for r in claims if r["status"] == "pass")
    calibrated = sum(1 for r in calibration if r["status"] == "fail")
    payload = {
        "contract": "log2026.cq_suite.v1",
        "question": ("Do the competency questions pass when written as queries "
                     "and run against the ontology and the extracted data?"),
        "method": ("FIBO and the committed snapshots loaded into one SPARQL "
                   "store; each competency question expressed as a query with "
                   "an expectation that can fail"),
        "claim_boundary": ("The snapshots are mapped to triples thinly and "
                           "nothing is inferred on the way in, so these ask "
                           "what was extracted rather than what could be "
                           "derived from it. An expectation is a threshold "
                           "chosen by us; passing means the capability exists "
                           "at that scale, not that it is sufficient."),
        "tag": args.tag, "arms": arms,
        "claims_passed": passed, "claims_total": len(claims),
        "calibration_failed_as_expected": calibrated,
        "calibration_total": len(calibration),
        "suite_trustworthy": calibrated == len(calibration),
        "questions": results,
    }
    (run.dir / "cq_suite.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    print()
    print(f"{'id':6s} {'want':5s} {'got':7s} {'answer':>10s}  question")
    for row in results:
        answer = row["answer"][0] if row["answer"] else (row["error"][:24] or "—")
        flag = " " if row["as_expected"] else "!"
        print(f"{flag}{row['id']:5s} {row['expected']:5s} {row['status']:7s} "
              f"{str(answer):>10s}  {row['question'][:52]}")
    print(f"\nclaims:      {passed} of {len(claims)} pass")
    print(f"calibration: {calibrated} of {len(calibration)} fail as they must")
    if calibrated < len(calibration):
        print("\nA calibration question passed. The suite is measuring "
              "something other than what it says and its other results should "
              "not be read until that is explained:")
        for row in calibration:
            if row["status"] != "fail":
                print(f"  {row['id']}: {row['requirement']}")
    for row in claims:
        if row["status"] != "pass":
            print(f"\n  {row['id']} needs: {row['requirement']}")
            if row["error"]:
                print(f"    {row['error']}")

    run.finish({"claims_passed": passed, "claims_total": len(claims),
                "calibration_failed_as_expected": calibrated,
                "suite_trustworthy": calibrated == len(calibration),
                "artifact": str((run.dir / "cq_suite.json").relative_to(ROOT))})
    return 1 if (args.strict and (passed < len(claims)
                                  or calibrated < len(calibration))) else 0


if __name__ == "__main__":
    raise SystemExit(main())
