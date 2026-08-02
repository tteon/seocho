#!/usr/bin/env python3
"""Build the findings folder: one directory per paper section, with a verdict.

The results are spread across run directories named by timestamp, which is the
right way to store them and the wrong way to read them. This assembles a second
view organised by the argument instead of by the clock:

    findings/
      README.md                  index with the status of every section
      1.0-isolation/
        FINDING.md               question, hypothesis, numbers, verdict, reading
        meta.json                the same as data
        chart.png / chart.pdf    when the section has one

Why Markdown and not a plain text file: the sections need tables, and a verdict
needs to be visually separable from the reasoning that produced it. Plain text
loses both, and a reader skimming for "did this work" would have to read prose
to find out. meta.json sits beside it so a script never has to parse the prose.

The numbers are pulled from the artifacts by contract, never typed here. The
interpretation IS typed here, deliberately and separately, because it is a
different kind of claim and CLAUDE.md 20.8 requires the two be distinguishable.
Every FINDING.md therefore separates:

    measured        what the run produced, quoted from the artifact
    verdict         supported / rejected / undecided / void, against a
                    hypothesis written before the run
    reading         what I think it means, labelled as interpretation
    limits          what the number does not support

A section whose hypothesis was rejected says so at the top. That is the whole
point of writing the hypothesis down first.
"""
from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
FINDINGS = ROOT / "findings"
INDEX = ROOT / "experiments/results_index.json"

SUPPORTED = "supported"
REJECTED = "rejected"
UNDECIDED = "undecided"
PENDING = "pending"
VOID = "void"

BADGE = {SUPPORTED: "✔ supported", REJECTED: "✘ rejected",
         UNDECIDED: "~ undecided", PENDING: "· not yet run",
         VOID: "! void — measurement was invalid"}


@dataclass
class Finding:
    slug: str
    section: str
    title: str
    question: str
    hypothesis: str
    method: str
    contract: str | None
    verdict: str
    reading: str
    limits: str
    reproduce: str
    numbers: Callable[[dict[str, Any]], list[tuple[str, Any]]] | None = None
    charts: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    prereg: str | None = None          # file under experiments/preregistration
    verdict_history: list[str] = field(default_factory=list)


def n(payload: dict[str, Any], *path: str, default: Any = "—") -> Any:
    cursor: Any = payload
    for key in path:
        if not isinstance(cursor, dict) or key not in cursor:
            return default
        cursor = cursor[key]
    return cursor


FINDING_LIST: list[Finding] = [
    Finding(
        slug="1.0-isolation",
        section="1.0",
        title="Was separating the categories necessary?",
        question=("If every category shared one graph, would merging on name "
                  "fuse things that are not the same thing?"),
        hypothesis=("Names collide across categories and mean different things, "
                    "so a shared graph would silently fuse unrelated facts and "
                    "the per-category databases are necessary rather than "
                    "cautious."),
        method=("Per-category databases are read for every distinct entity name "
                "and every relationship type. Names present in more than one "
                "category are embedded together with their graph context — "
                "label, value, neighbour names — using a local model, and their "
                "cosine similarity is compared against a control: the similarity "
                "of unrelated nodes inside a single category."),
        contract="log2026.category_contamination.v1",
        verdict=UNDECIDED,
        reading=(
            "The hypothesis is not supported in the strong form it was written. "
            "Only 1.7% of entity names appear in more than one category, and for "
            "those the context similarity is 0.700 against a control of 0.689 — "
            "a difference of about one hundredth.\n\n"
            "Both readings of that are worth stating because they point "
            "different ways. In favour of separating: a colliding name carries "
            "essentially no guarantee of shared meaning, since two nodes with the "
            "same name sit in surroundings no more alike than two arbitrary nodes "
            "do. Merging on name would therefore be fusing on a coincidence. "
            "Against: there are only 65 such names, so the practical exposure is "
            "small.\n\n"
            "The relationship side splits from the entity side and does support "
            "the claim: 21 of 28 relationship types appear in more than one "
            "category. Entities barely overlap while the edges between them "
            "overlap almost completely.\n\n"
            "There is a structural reason the entity overlap is low that has "
            "nothing to do with contamination: each category holds different "
            "filings about different companies, so most names could not collide "
            "even in principle. The sharp version of this test restricts to "
            "companies that appear in two categories and asks whether their "
            "metric names mean the same thing there. Until that runs, this "
            "section cannot claim isolation was necessary."),
        limits=("Context similarity is a proxy for meaning computed from the "
                "extracted graph, not from the source text. The categories hold "
                "different source documents, which depresses overlap for reasons "
                "unrelated to the question."),
        reproduce="python3 experiments/minimal/category_contamination.py",
        numbers=lambda p: [
            ("distinct entity names", f"{n(p, 'distinct_names_total'):,}"),
            ("in more than one category", f"{n(p, 'names_in_more_than_one_category')} "
                                          f"({n(p, 'name_overlap_rate'):.1%})"),
            ("excluded as non-entities", n(p, "excluded_as_non_entities")),
            ("relationship types shared", f"{n(p, 'relationship_types_shared')} of "
                                          f"{n(p, 'relationship_types_total')}"),
            ("context similarity, shared names", f"{n(p, 'mean_context_similarity'):.3f}"),
            ("control, unrelated nodes", f"{n(p, 'control_unrelated_within_category'):.3f}"),
        ],
    ),
    Finding(
        slug="1.1-model-dependence",
        section="1.1",
        title="Without a schema, does the output depend on which model ran?",
        question=("Do three models given the same document produce graphs whose "
                  "identifiers can be matched to each other?"),
        hypothesis=("With no schema the extractor falls back on its own "
                    "pre-training, so different models will key the same fact "
                    "differently and the graphs will not join."),
        method=("For every node in each model's graph, compare the identifier "
                "the model wrote against the slug of the name it gave the same "
                "node. Divergence means the identifier was invented rather than "
                "derived, and an invented identifier cannot match another "
                "model's."),
        contract="log2026.merge_key_reality.v1",
        verdict=SUPPORTED,
        reading=(
            "Supported, and the size of the effect is larger than expected. The "
            "identifier is derivable from the name in 42.5% of DeepSeek's nodes, "
            "26.6% of MiniMax's and 6.0% of gpt-oss's. gpt-oss systematically "
            "prefixes a type abbreviation — ma_ for monetary amount, fm_ for "
            "financial metric, rev_ for revenue — which no other model does.\n\n"
            "This is the mechanical cause of everything downstream. The graph "
            "merges on (identifier, workspace), so two models writing "
            "ma_repurchase_amount_2022 and repurchase_amount for the same figure "
            "produce two nodes that can never meet, however identical the "
            "underlying fact.\n\n"
            "What this does not yet establish is whether the disagreement is "
            "between models or simply within them. A single model run twice may "
            "disagree with itself just as much, in which case the finding is "
            "about sampling temperature rather than about pre-training. That run "
            "has not happened and the section is incomplete without it."),
        limits=("Measures naming, not content. Two graphs could disagree on every "
                "identifier and hold the same facts."),
        reproduce="see outputs/minimal/merge_key_reality.json",
        numbers=lambda p: [
            (f"{view}: identifier derivable from name",
             f"{cell['share']:.1%} of {cell['sampled']:,}")
            for view, cell in n(p, "by_view", default={}).items()
        ],
        depends_on=["a second run of one model, to separate between-model "
                    "disagreement from within-model variance"],
    ),
    Finding(
        slug="1.2-ontology-fit",
        section="1.2",
        title="Is FIBO the right ontology for these questions?",
        question=("Before asking whether the ontology helped, does it even cover "
                  "what the corpus asks about?"),
        hypothesis=("FIBO is the industry reference model for exactly this "
                    "domain, so its vocabulary should name what the questions "
                    "are about and its own competency questions should be the "
                    "same kind of question."),
        method=("Two independent checks. Vocabulary coverage: how much of each "
                "category's question vocabulary FIBO can name. Question "
                "similarity: competency questions generated mechanically from "
                "FIBO's classes and properties, compared to the real questions "
                "by meaning, against a control of how similar the real questions "
                "are to each other."),
        contract="log2026.cq_similarity.v1",
        verdict=SUPPORTED,
        reading=(
            "Supported with one clean exception. Question similarity runs 0.59 to "
            "0.72 against a within-corpus control of 0.78 to 0.83, so FIBO asks "
            "the same kind of question the corpus asks, at roughly four fifths of "
            "the similarity two real questions have to each other.\n\n"
            "Risk is the exception and it is a clean one: lowest similarity at "
            "0.59 and the highest internal control at 0.83, which is the "
            "signature of a coherent topic that the ontology does not cover. The "
            "vocabulary measurement agrees — cybersecurity, cyber and ERM are "
            "absent from real FIBO. Risk should be treated as out of scope rather "
            "than as an ontology failure.\n\n"
            "Separately, FIBO's chosen labels are not the words the filings use. "
            "Of 279 alias pairs whose counts can be trusted, the alias beats the "
            "formal label in 43: LLC over limited liability company, EBITDA over "
            "its expansion, parent company over total controlling interest party. "
            "This is a register difference rather than a coverage gap — FIBO "
            "declares all of those itself — and it is why the synonym layer had "
            "to be a separate condition rather than folded in."),
        limits=("Similarity of phrasing and topic, not of answerability. A high "
                "score means FIBO asks this kind of question, not that a graph "
                "built from it can answer one."),
        reproduce=("python3 experiments/minimal/cq_similarity.py && "
                   "python3 experiments/minimal/ontology_task_fit.py && "
                   "python3 experiments/minimal/alias_register.py"),
        numbers=lambda p: [
            (category, f"{cell['mean_best_similarity']:.3f} "
                       f"(control {cell['control_within_category']:.3f})")
            for category, cell in sorted(
                n(p, "by_category", default={}).items(),
                key=lambda kv: -kv[1]["mean_best_similarity"])
        ],
    ),
    Finding(
        slug="1.3-axioms",
        section="1.3",
        title="Does reasoning add structure the class list does not have?",
        question=("The extraction prompt receives a flat list of class names. "
                  "Would entailment give it more?"),
        hypothesis=("FIBO carries restrictions, unions and equivalences, so a "
                    "reasoner should place classes under one another and resolve "
                    "relation endpoints that the asserted axioms leave open."),
        method=("An OWL 2 RL closure over the FIBO turtle, comparing the "
                "subclass, equivalence, disjointness and domain/range relations "
                "before and after, restricted to the classes the FIBO condition "
                "actually ships."),
        contract="log2026.reasoner_pretest.v2",
        verdict=SUPPORTED,
        reading=(
            "Supported, and it justified adding a fifth condition. Within the "
            "seventy classes the FIBO condition ships, entailment more than "
            "doubles the classes that have a parent inside that set, from 7 to "
            "15, and takes relations with both endpoints resolved from 4 to 28. "
            "My own hand-written parent walk had found 12 of those 28, so the "
            "approximation I was using was missing more than half.\n\n"
            "The hierarchy it produces is directly relevant to the comparison "
            "problem: ChiefExecutiveOfficer under Employee and Executive, Lease "
            "under Agreement and Contract, Debt under Commitment. Two views "
            "answering ChiefExecutiveOfficer and Executive for one person are a "
            "mismatch under string equality and compatible under subsumption, so "
            "the hierarchy changes what the agreement measure is measuring, not "
            "only how much of it there is.\n\n"
            "Everything here is a floor. HermiT and Pellet need a JVM this "
            "machine does not have, so the engine is a pure-Python OWL 2 RL "
            "closure, and RL does not derive subsumption from complex class "
            "expressions. FIBO leans on those heavily — 2,656 restrictions and "
            "1,344 someValuesFrom in the quickstart — so a complete reasoner "
            "would find strictly more."),
        limits=("OWL 2 RL, not DL. Counts what entailment adds to the schema; "
                "says nothing about whether a richer schema improves extraction."),
        reproduce="python3 experiments/minimal/reasoner_pretest.py",
        numbers=lambda p: [
            ("classes with a parent in scope",
             f"{n(p, 'classes_with_a_parent_in_scope', 'before')} → "
             f"{n(p, 'classes_with_a_parent_in_scope', 'after')}"),
            ("relations with both endpoints resolved",
             f"{n(p, 'relations_with_both_endpoints_in_scope', 'before')} → "
             f"{n(p, 'relations_with_both_endpoints_in_scope', 'after')}"),
            ("subclass edges added within scope", n(p, "new_subclass_within_scope")),
            ("equivalences added within scope", n(p, "new_equivalent_within_scope")),
            ("disjoint pairs available", n(p, "disjoint_pairs_in_scope")),
        ],
        depends_on=["a DL reasoner, to turn these floors into values"],
    ),
    Finding(
        slug="1.4-result",
        section="1.4",
        title="Does giving the extractor an ontology make two models agree?",
        question=("Holding documents, prompt, chunking and seed fixed, does the "
                  "schema handed to the extractor change how often two models "
                  "describe the same fact under the same name?"),
        hypothesis=("Pre-registered: agreement rises from no ontology, through "
                    "the hand-written schema, to real FIBO, and highest with the "
                    "synonym layer. More shared vocabulary, more shared naming."),
        method=("Four conditions differing only in the schema. Sixteen cases, "
                "three extractor models, every extraction scored. A fact is "
                "comparable when at least two of the three models produced the "
                "same normalized name within the same case."),
        contract="log2026.arm_results.v1",
        verdict=REJECTED,
        reading=(
            "Rejected, and in the opposite direction. Giving the extractor no "
            "ontology at all produced the highest agreement, 0.375, against 0.221 "
            "for the hand-written schema and 0.193 for real FIBO. Keyed on the "
            "model's own identifier rather than the name, the gap is wider still: "
            "0.160 against 0.052.\n\n"
            "The mechanism is visible in the counts rather than the rates. "
            "Seventy classes produced 1,672 distinct fact names where no schema "
            "produced 1,334 — 25% more — and nearly every additional name was "
            "seen by one model only. Giving an extractor more ways to slice a "
            "sentence lowers the chance two extractors slice it the same way. "
            "That is a claim about mechanism and it is interpretation, not "
            "measurement; the class-count control that would confirm it has not "
            "run.\n\n"
            "The synonym layer edges plain FIBO, 0.201 against 0.193, which is "
            "the direction it predicts. It is eighteen keys. At this sample size "
            "that is not a result and is reported as a direction only.\n\n"
            "Two columns of the original table must not be read and the scripts "
            "now say so. The declared-type share is meaningless for the "
            "no-ontology condition, whose single declared class is Entity, so "
            "0.988 is a definition. The period fill rate was confounded: the "
            "FIBO conditions received a property set I wrote by hand while the "
            "baseline declared only a name, so filling `period` twice as often "
            "measures which condition was given the slot. The second run "
            "equalizes the property floor across all conditions and adds the "
            "subsumption condition."),
        limits=("Sixteen cases, three models, one run, no confidence interval. "
                "Measures whether two models NAME a fact the same way, not "
                "whether they captured the same fact, and not whether either is "
                "correct."),
        reproduce=("scripts/ops/run_reextract.sh --cases 16 && "
                   "python3 experiments/minimal/arm_results.py && "
                   "python3 experiments/minimal/plot_arms.py"),
        numbers=lambda p: [
            (f"condition {arm}", f"{cell['by_key_rule']['name']['comparable_rate']:.3f} "
                                 f"({cell['by_key_rule']['name']['comparable']} of "
                                 f"{cell['by_key_rule']['name']['keys']})")
            for arm, cell in n(p, "by_arm", default={}).items()
        ],
        charts=["papers/log2026/figures/arm_comparability.pdf",
                "papers/log2026/figures/arm_comparability.png"],
        depends_on=["a confidence interval, before any difference is stated as a "
                    "result",
                    "a correctness measure, since a schema could lower agreement "
                    "and raise accuracy",
                    "a content measure, since agreement on names is not "
                    "agreement on facts"],
    ),
    Finding(
        slug="1.5-mechanism",
        section="1.5",
        title="Why does more vocabulary lower agreement?",
        question=("Is the fragmentation caused by the number of classes, by "
                  "FIBO's particular classes, or by declaring types at all?"),
        hypothesis=("Declaring a type pushes the extractor toward specific, "
                    "idiosyncratic instance names, so typed entities are less "
                    "findable across views than untyped ones."),
        method=("Not yet run. Requires an entity-overlap comparison between the "
                "no-ontology and FIBO conditions paired by case, an alias-collapse "
                "measure between the FIBO and synonym conditions, and a control "
                "condition with seventy classes that are not FIBO's."),
        contract=None,
        verdict=PENDING,
        reading=(
            "Nothing here is measured yet, and one earlier attempt is withdrawn.\n\n"
            "The withdrawn one matters because it was reported as a finding. It "
            "compared entities carrying a declared type (0.050 overlap) against "
            "entities carrying the generic fallback (0.227) inside a single "
            "graph, and read the gap as an effect of declaring a type. It is not. "
            "What decides whether an entity gets a declared type is what kind of "
            "thing it is — companies get LegalEntity, one-off figures get "
            "MonetaryAmount — so the comparison contrasts coarse entities with "
            "fine ones. The observation that coarse entities recur and fine ones "
            "do not still stands; the causal claim does not.\n\n"
            "The control that separates 'more classes' from 'FIBO's classes' does "
            "not exist yet and is the one that decides whether the mechanism "
            "claim in section 1.4 can be made at all."),
        limits="",
        reproduce="not yet implemented",
        depends_on=["type findability, no-ontology against FIBO, paired by case",
                    "alias collapse, FIBO against FIBO-plus-synonyms",
                    "a control condition with seventy non-FIBO classes"],
    ),
]


def prereg_status(name: str) -> str:
    """When the hypothesis was committed, so 'before the run' is checkable.

    A hypothesis is only pre-registered if its commit predates the run it
    predicts. The commit date is read from git rather than from the file, since
    a file's mtime says nothing about when its contents were fixed.
    """
    import subprocess

    path = ROOT / "experiments/preregistration" / name
    if not path.is_file():
        return "**file missing**"
    try:
        stamp = subprocess.run(
            ["git", "log", "-1", "--format=%cs", "--", str(path)],
            cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except Exception:  # noqa: BLE001
        return "committed date unavailable"
    return f"committed {stamp}" if stamp else "**not committed yet**"


def load_artifact(contract: str) -> dict[str, Any] | None:
    if not INDEX.is_file():
        return None
    payload = json.loads(INDEX.read_text())
    matches = [r for r in payload.get("results", []) if r["contract"] == contract]
    if not matches:
        return None
    newest = max(matches, key=lambda r: r["modified"])
    path = ROOT / newest["path"]
    try:
        return json.loads(path.read_text()) | {"_path": newest["path"],
                                               "_run_dir": newest["run_dir"]}
    except OSError:
        return None


AUTHORED_START = "<!-- authored: kept across regeneration -->"
AUTHORED_END = "<!-- /authored -->"


def keep_authored(path: Path) -> str:
    """Carry hand-written text through a regeneration.

    The generated sections are rebuilt from the artifacts every time, which is
    the point of generating them. Everything between the two markers is not:
    it is where paper drafting happens, and losing it to a rebuild would make
    this directory unusable for the thing it exists for.
    """
    if not path.is_file():
        return ""
    text = path.read_text()
    if AUTHORED_START not in text or AUTHORED_END not in text:
        return ""
    return text.split(AUTHORED_START, 1)[1].split(AUTHORED_END, 1)[0]


def render(finding: Finding, payload: dict[str, Any] | None,
           authored: str = "") -> str:
    lines = [f"# {finding.section}  {finding.title}", "",
             f"**{BADGE[finding.verdict]}**", ""]
    if finding.prereg:
        registered = prereg_status(finding.prereg)
        lines += [f"Hypothesis registered in "
                  f"[`{finding.prereg}`](../../experiments/preregistration/"
                  f"{finding.prereg}) — {registered}", ""]
    else:
        lines += ["> No pre-registration file. The hypothesis below was written "
                  "up alongside the analysis rather than committed before the "
                  "run, and should be read as a statement of intent recovered "
                  "after the fact.", ""]
    lines += [
             "## Question", "", finding.question, "",
             "## Hypothesis", "", finding.hypothesis, "",
             "## Method", "", finding.method, ""]

    lines += ["## Measured", ""]
    if payload is None:
        lines += ["Not run.", ""]
    else:
        rows = finding.numbers(payload) if finding.numbers else []
        if rows:
            lines += ["| | |", "|---|---|"]
            lines += [f"| {label} | {value} |" for label, value in rows]
            lines.append("")
        lines += [f"Artifact: `{payload.get('_path', '')}`",
                  f"Trace: `{payload.get('_run_dir', '')}/trace.jsonl`", ""]

    if finding.verdict_history:
        lines += ["## How this verdict changed", ""]
        lines += [f"- {entry}" for entry in finding.verdict_history]
        lines.append("")
    lines += ["## Reading", "",
              "_Interpretation, separate from the measurement above._", "",
              finding.reading, ""]
    if finding.limits:
        lines += ["## What this does not support", "", finding.limits, ""]
    if finding.depends_on:
        lines += ["## Still needed before this section is complete", ""]
        lines += [f"- {item}" for item in finding.depends_on]
        lines.append("")
    lines += ["## Reproduce", "", "```bash", finding.reproduce, "```", ""]
    lines += ["---", "", "## Draft notes", "",
              AUTHORED_START,
              authored.strip() or "_Nothing yet. Text written between the two "
                                  "markers survives `findings.py --write`._",
              AUTHORED_END, ""]
    return "\n".join(lines)


def draw_overview(rows: list[dict[str, Any]], path: Path) -> None:
    """One strip per section: where the argument stands at a glance.

    A table of verdicts is readable but not scannable, and the thing a reader
    wants first is which parts are still open. Colour is carried by position and
    a symbol as well as hue, so it survives greyscale printing.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    order = [PENDING, UNDECIDED, REJECTED, SUPPORTED]
    face = {SUPPORTED: "#3f6b4a", REJECTED: "#b4472e",
            UNDECIDED: "#c9a227", PENDING: "#c4c4c4"}
    symbol = {SUPPORTED: "✔", REJECTED: "✘", UNDECIDED: "~", PENDING: "·"}

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9,
                         "figure.dpi": 160, "text.color": "#1a1a1a"})
    height = 0.55 * len(rows) + 1.5
    fig, ax = plt.subplots(figsize=(9.0, height))
    for i, row in enumerate(reversed(rows)):
        finding, verdict = row["finding"], row["verdict"]
        y = i
        ax.barh(y, order.index(verdict) + 1, height=0.5,
                color=face[verdict], zorder=3)
        ax.text(-0.12, y, f"{finding.section}", ha="right", va="center",
                fontsize=9, fontweight="bold")
        ax.text(0.08, y, f"{symbol[verdict]}  {finding.title}", ha="left",
                va="center", fontsize=8.5, color="white", zorder=4)
        pending = len(finding.depends_on)
        if pending:
            ax.text(order.index(verdict) + 1.1, y,
                    f"{pending} still needed", ha="left", va="center",
                    fontsize=7.5, color="#8a8a8a", style="italic")
    ax.set_yticks([])
    ax.set_xticks(range(1, len(order) + 1))
    ax.set_xticklabels(["not yet run", "undecided", "rejected", "supported"],
                       fontsize=8, color="#8a8a8a")
    ax.set_xlim(-2.6, len(order) + 2.2)
    ax.set_ylim(-0.8, len(rows) - 0.2)
    ax.set_title("Where each section of the argument stands", fontsize=10,
                 loc="left", pad=12)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color("#c4c4c4")
    ax.grid(axis="x", color="#eeeeee", lw=0.6, zorder=0)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    fig.savefig(path.with_suffix(".pdf"), bbox_inches="tight", facecolor="white")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    rows = []
    for finding in FINDING_LIST:
        payload = load_artifact(finding.contract) if finding.contract else None
        verdict = finding.verdict
        if finding.contract and payload is None and verdict != PENDING:
            verdict = PENDING
        rows.append({"finding": finding, "payload": payload, "verdict": verdict})

    if args.write:
        FINDINGS.mkdir(exist_ok=True)
        for row in rows:
            finding = row["finding"]
            directory = FINDINGS / finding.slug
            directory.mkdir(exist_ok=True)
            target = directory / "FINDING.md"
            authored = keep_authored(target)
            target.write_text(render(finding, row["payload"], authored))
            (directory / "meta.json").write_text(json.dumps({
                "section": finding.section, "title": finding.title,
                "question": finding.question, "hypothesis": finding.hypothesis,
                "verdict": row["verdict"], "contract": finding.contract,
                "artifact": (row["payload"] or {}).get("_path", ""),
                "run_dir": (row["payload"] or {}).get("_run_dir", ""),
                "reproduce": finding.reproduce,
                "preregistration": finding.prereg,
                "verdict_history": finding.verdict_history,
                "still_needed": finding.depends_on,
            }, indent=2, ensure_ascii=False) + "\n")
            for chart in finding.charts:
                source = ROOT / chart
                if source.is_file():
                    shutil.copy2(source, directory / source.name)

        index = ["# Findings", "",
                 "One directory per section of the argument. Each holds the "
                 "question, the hypothesis as written before the run, the "
                 "numbers quoted from the artifact, a verdict against that "
                 "hypothesis, and my reading kept separate from both.", "",
                 f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} "
                 "UTC by `experiments/findings.py --write`.", "",
                 "| Section | Question | Verdict |", "|---|---|---|"]
        for row in rows:
            finding = row["finding"]
            question = finding.question.replace("\n", " ")
            if len(question) > 84:
                question = question[:81] + "…"
            index.append(f"| [{finding.section} {finding.title}]"
                         f"({finding.slug}/FINDING.md) | {question} "
                         f"| {BADGE[row['verdict']]} |")
        outstanding = [(r["finding"], item) for r in rows
                       for item in r["finding"].depends_on]
        if outstanding:
            index += ["", "## Outstanding before the paper can close", ""]
            for finding, item in outstanding:
                index.append(f"- **{finding.section}** — {item}")
            index.append("")
        try:
            draw_overview(rows, FINDINGS / "overview.png")
            index.insert(5, "![status](overview.png)")
            index.insert(6, "")
        except Exception as exc:  # noqa: BLE001 — the catalogue still writes
            print(f"  overview chart skipped: {type(exc).__name__}: {exc}")
        (FINDINGS / "README.md").write_text("\n".join(index) + "\n")
        print(f"findings/  {len(rows)} sections")

    counts: dict[str, int] = {}
    for row in rows:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    for row in rows:
        finding = row["finding"]
        print(f"  {finding.section:5s} {BADGE[row['verdict']]:34s} {finding.title}")
    print(f"\n{counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
