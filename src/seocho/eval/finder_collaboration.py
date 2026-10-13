"""FinDER collaboration workload built from original 10-K documents.

Gold excerpts are evaluation-only. No question, answer, category annotation, or
case-to-gold membership is written into the source graph.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import unicodedata
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

from ..benchmarking import FinDERBenchmarkCase, compare_answers, score_answer_slots
from ..query.contracts import CypherPlan
from ..query.cypher_validator import CypherQueryValidator
from ..store.graph import validate_database_name
from .benchmarks.finder import _normalize_row

UPSTREAM = "https://huggingface.co/datasets/Linq-AI-Research/FinDER"
SLICES = ("single_reference", "multi_reference", "compositional", "numeric")
_AMBIGUOUS_SYMBOLS = {"IR", "IT", "AI", "IP"}
_STOPWORDS = set("a an the and or of to in on for from by as at is are was were with how what which "
                 "does do did explain describe discuss company inc corp corporation fiscal year "
                 "vs versus about its their this that be has have had can would could please".split())


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() if hasattr(hashlib, "file_digest") else digest(stream.read())


def compact(text: str) -> str:
    """Whitespace-insensitive matching without consulting answers."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).casefold()


@dataclass(frozen=True)
class WorkItem:
    case: FinDERBenchmarkCase
    references: tuple[str, ...]
    anchors: tuple[str, ...]
    slice_id: str

    def public_payload(self) -> dict[str, Any]:
        # Keep evaluation-only fields out of the planner/worker/answerer input.
        return {"question": self.case.question, "anchors": list(self.anchors),
                "required_slots": ["source_evidence", "question_answer"]}


@dataclass(frozen=True)
class Passage:
    evidence_id: str
    ticker: str
    filing_id: str
    section: str
    ordinal: int
    text: str
    source: str
    start: int
    end: int
    normalized_start: int
    normalized_end: int

    def graph_row(self) -> dict[str, Any]:
        return {k: v for k, v in vars(self).items() if k not in {"normalized_start", "normalized_end"}}


@dataclass
class Filing:
    ticker: str
    filing_id: str
    source: str
    year: str
    text: str
    passages: list[Passage]
    normalized_text: str


@dataclass
class Corpus:
    corpus_id: str
    dataset_sha256: str
    documents_sha256: str
    filings: list[Filing]
    selected: list[WorkItem]
    excluded: dict[str, int]
    seed: int
    chunk_chars: int
    overlap_chars: int
    dataset_records: int
    eligible_by_slice: dict[str, int]
    gold_mapping: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def manifest(self) -> dict[str, Any]:
        return {
            "schema_version": "seocho.finder-collaboration.v1", "corpus_id": self.corpus_id,
            "upstream": UPSTREAM, "dataset_sha256": self.dataset_sha256,
            "documents_sha256": self.documents_sha256, "selection_seed": self.seed,
            "chunk_chars": self.chunk_chars, "overlap_chars": self.overlap_chars,
            "companies": sorted(f.ticker for f in self.filings),
            "filings": len(self.filings), "passages": sum(len(f.passages) for f in self.filings),
            "slice_counts": dict(Counter(i.slice_id for i in self.selected)),
            "category_counts": dict(Counter(i.case.category for i in self.selected)),
            "excluded": self.excluded, "dataset_records": self.dataset_records,
            "eligible_by_slice": self.eligible_by_slice,
            "cases": [{"case_id": i.case.case_id, "slice": i.slice_id, "category": i.case.category,
                       "reasoning_type": i.case.reasoning_type, "question": i.case.question,
                       "anchors": list(i.anchors)} for i in self.selected],
            "gold_mapping": self.gold_mapping,
            "scope": "Original-filing document graph; company resolution from question ticker only. "
                     "Reference count/type determine selection, never source construction or retrieval. "
                     "Slices are proxies, not verified graph-hop difficulty. Answer correctness requires review.",
        }


class FilingHTMLParser(HTMLParser):
    """Read visible text and filing metadata, preserving table row boundaries.

    Hidden inline-XBRL facts are metadata only, never duplicate answer passages.
    This parser does not execute HTML, extract archives, or deserialize pickles.
    """

    _BREAKS = {"p", "div", "br", "tr", "h1", "h2", "h3", "h4", "li", "table"}
    _VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool]] = []
        self.parts: list[str] = []
        self.metadata: dict[str, str] = {}
        self.capture: tuple[str, list[str]] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        style = re.sub(r"\s+", "", attr.get("style") or "").lower()
        hidden = (bool(self.stack and self.stack[-1][1]) or tag in {"head", "script", "style", "ix:header", "ix:hidden", "ix:resources"}
                  or "display:none" in style or "visibility:hidden" in style or "hidden" in attr)
        if tag == "ix:nonnumeric" and attr.get("name", "").lower() in {
            "dei:documentfiscalyearfocus", "dei:entityregistrantname", "dei:tradingSymbol".lower(),
        }:
            self.capture = (attr["name"].lower(), [])
        if not hidden:
            if tag in self._BREAKS:
                self.parts.append("\n")
            elif tag in {"td", "th"}:
                self.parts.append("\t")
        if tag not in self._VOID:
            self.stack.append((tag, hidden))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in self._VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag == "ix:nonnumeric" and self.capture:
            key, parts = self.capture
            self.metadata[key] = "".join(parts).strip()
            self.capture = None
        hidden = bool(self.stack and self.stack[-1][1])
        if not hidden and tag in self._BREAKS:
            self.parts.append("\n")
        # HTML filings may omit closing tags. Pop only to the matching open tag.
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        if self.capture:
            self.capture[1].append(data)
        if not self.stack or not self.stack[-1][1]:
            self.parts.append(data)

    def visible_text(self) -> str:
        text = unicodedata.normalize("NFKC", "".join(self.parts)).replace("\r", "\n")
        text = re.sub(r"[^\S\n]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n", text)
        return text.strip()


def load_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".parquet":
        try:
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise ImportError("Parquet input requires pyarrow (uv pip install pyarrow); alternatively supply JSON") from exc
        return pq.read_table(path).to_pylist()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("FinDER JSON must contain a list of raw or normalized records")
    return data


def document_members(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as archive:
        result: dict[str, str] = {}
        for name in archive.namelist():
            p = Path(name)
            if name.startswith("__MACOSX/") or p.suffix.lower() != ".html":
                continue
            ticker = p.stem.upper()
            if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", ticker):
                continue
            if ticker in result:
                raise ValueError(f"Ambiguous original filing for ticker {ticker}")
            result[ticker] = name
        if not result:
            raise ValueError("Document ZIP has no ticker-named HTML filings")
        return result


def question_anchors(question: str, tickers: Iterable[str]) -> tuple[str, ...]:
    """Conservative pilot resolver: exact uppercase symbols, no gold lookup.

    One-character symbols are ambiguous with prose and are excluded. Common
    acronym collisions require an explicit company context. Company
    names/aliases not explicitly containing a supported symbol remain unresolved.
    """
    words = set(re.findall(r"(?<![A-Za-z0-9])[A-Z][A-Z0-9.-]+(?![A-Za-z0-9])", question))
    def explicit_company_context(ticker: str) -> bool:
        return bool(re.search(rf"\b(?:ticker|for|at|from|of|by)\s+{re.escape(ticker)}\b|"
                              rf"\b{re.escape(ticker)}['’]s\b", question))

    return tuple(sorted(t for t in tickers if len(t) > 1 and t in words and t.lower() not in _STOPWORDS
                        and (t not in _AMBIGUOUS_SYMBOLS or explicit_company_context(t))))


def normalize_items(rows: list[dict[str, Any]], tickers: Iterable[str]) -> tuple[list[WorkItem], dict[str, int]]:
    items: list[WorkItem] = []
    excluded: Counter[str] = Counter()
    seen: set[str] = set()
    for raw in rows:
        data = raw if "question" in raw else _normalize_row(raw)
        case_id = str(data.get("id") or data.get("_id") or "")
        refs = data.get("references")
        if not isinstance(refs, (list, tuple)) or not refs or any(not isinstance(r, str) or not r.strip() for r in refs):
            excluded["invalid_references"] += 1
            continue
        question = str(data.get("question") or "").strip()
        expected = str(data.get("answer") or data.get("expected_answer") or "").strip()
        if not case_id or not question or not expected:
            excluded["missing_id_question_or_answer"] += 1
            continue
        if case_id in seen:
            raise ValueError(f"Duplicate FinDER case ID: {case_id}")
        seen.add(case_id)
        anchors = question_anchors(question, tickers)
        if not anchors:
            excluded["unresolved_question_company"] += 1
            continue
        rtype = str(data.get("type") or data.get("reasoning_type") or "None")
        if rtype == "Compositional":
            slice_id = "compositional"
        elif rtype in {"Addition", "Subtract", "Subtraction", "Multiplication", "Division"}:
            slice_id = "numeric"
        elif len(refs) > 1:
            slice_id = "multi_reference"
        else:
            slice_id = "single_reference"
        case = FinDERBenchmarkCase(case_id=case_id, text="\n\n".join(refs), question=question,
                                  expected_answer=expected, category="".join(str(data.get("category") or "general").split()),
                                  reasoning_type=rtype)
        items.append(WorkItem(case=case, references=tuple(refs), anchors=anchors, slice_id=slice_id))
    return items, dict(excluded)


def select_pilot(items: list[WorkItem], *, per_slice: int = 10, seed: int = 42) -> list[WorkItem]:
    if per_slice < 1:
        raise ValueError("per_slice must be positive")
    groups: dict[str, list[WorkItem]] = defaultdict(list)
    for item in items:
        groups[item.slice_id].append(item)
    selected: list[WorkItem] = []
    for index, slice_id in enumerate(SLICES):
        # Category round-robin after seeded shuffle limits category dominance.
        categories: dict[str, list[WorkItem]] = defaultdict(list)
        for item in sorted(groups[slice_id], key=lambda i: i.case.case_id):
            categories[item.case.category].append(item)
        rng = random.Random(seed + index)
        for group in categories.values():
            rng.shuffle(group)
        count = 0
        while count < per_slice and any(categories.values()):
            for category in sorted(categories):
                if categories[category] and count < per_slice:
                    selected.append(categories[category].pop())
                    count += 1
    return selected


def parse_filing(raw: bytes, *, ticker: str, source: str, chunk_chars: int = 3500,
                 overlap_chars: int = 350) -> Filing:
    if chunk_chars < 500 or overlap_chars < 0 or overlap_chars >= chunk_chars:
        raise ValueError("chunk_chars >= 500 and 0 <= overlap_chars < chunk_chars required")
    parser = FilingHTMLParser()
    parser.feed(raw.decode("utf-8-sig", errors="replace"))
    text = parser.visible_text()
    if not text:
        raise ValueError(f"No visible filing text: {source}")
    filing_id = f"filing:{ticker}:" + digest(raw)[:24]
    headings = [(m.start(), "item" + m.group(1).lower()) for m in re.finditer(
        r"(?im)^\s*item\s+(\d{1,2}[abc]?)(?:\s*[.:-]|\s+[A-Za-z])", text)]
    passages: list[Passage] = []
    normalized_offsets = [0]
    for char in text:
        normalized_offsets.append(normalized_offsets[-1] + (0 if char.isspace() else len(char.casefold())))
    start = 0
    ordinal = 0
    while start < len(text):
        end = min(start + chunk_chars, len(text))
        if end < len(text):
            boundary = text.rfind("\n", start + chunk_chars // 2, end)
            if boundary > start:
                end = boundary
        section = next((s for pos, s in reversed(headings) if pos <= start), "unclassified")
        piece = text[start:end]
        passages.append(Passage(
            evidence_id=f"{filing_id}:p{ordinal}", ticker=ticker, filing_id=filing_id,
            section=section, ordinal=ordinal, text=piece, source=source, start=start, end=end,
            normalized_start=normalized_offsets[start], normalized_end=normalized_offsets[end],
        ))
        if end == len(text):
            break
        start = max(start + 1, end - overlap_chars)
        ordinal += 1
    return Filing(ticker=ticker, filing_id=filing_id, source=source,
                  year=parser.metadata.get("dei:documentfiscalyearfocus", "unknown"),
                  text=text, passages=passages, normalized_text=compact(text))


def map_gold(item: WorkItem, filings: dict[str, Filing]) -> list[dict[str, Any]]:
    """Map exact normalized gold spans after source construction; never rank by gold."""
    result: list[dict[str, Any]] = []
    for index, reference in enumerate(item.references):
        needle = compact(reference)
        matches = []
        for ticker in item.anchors:
            filing = filings[ticker]
            start = filing.normalized_text.find(needle)
            while start >= 0:
                end = start + len(needle)
                spans = [{"evidence_id": p.evidence_id, "start": max(start, p.normalized_start),
                          "end": min(end, p.normalized_end)} for p in filing.passages
                         if p.normalized_start < end and p.normalized_end > start]
                matches.append({"ticker": ticker, "evidence_refs": [p["evidence_id"] for p in spans],
                                "gold_start": start, "gold_end": end, "spans": spans})
                start = filing.normalized_text.find(needle, start + 1)
        result.append({"reference_index": index, "status": "mapped" if matches else "unmapped",
                       "matches": matches})
    return result


def prepare_corpus(dataset: Path, documents: Path, *, per_slice: int = 10, seed: int = 42,
                   chunk_chars: int = 3500, overlap_chars: int = 350,
                   case_id: str | None = None) -> Corpus:
    members = document_members(documents)
    rows = load_rows(dataset)
    items, excluded = normalize_items(rows, members)
    selected = select_pilot(items, per_slice=per_slice, seed=seed)
    if case_id:
        selected = [item for item in items if item.case.case_id == case_id]
    if not selected:
        raise ValueError("No eligible FinDER cases; check ticker resolution or --case")
    tickers = sorted({ticker for item in selected for ticker in item.anchors})
    filings = []
    with zipfile.ZipFile(documents) as archive:
        for ticker in tickers:
            member = members[ticker]
            filings.append(parse_filing(archive.read(member), ticker=ticker,
                                       source="FinDER/10-k.zip:" + member,
                                       chunk_chars=chunk_chars, overlap_chars=overlap_chars))
    dataset_hash, documents_hash = file_digest(dataset), file_digest(documents)
    identity = json.dumps([documents_hash, tickers, chunk_chars, overlap_chars, "visible-html-v1"])
    corpus = Corpus(corpus_id="finder:" + digest(identity.encode())[:24], dataset_sha256=dataset_hash,
                    documents_sha256=documents_hash, filings=filings, selected=selected, excluded=excluded,
                    seed=seed, chunk_chars=chunk_chars, overlap_chars=overlap_chars,
                    dataset_records=len(rows), eligible_by_slice=dict(Counter(i.slice_id for i in items)))
    by_ticker = {f.ticker: f for f in filings}
    corpus.gold_mapping = {i.case.case_id: map_gold(i, by_ticker) for i in selected}
    return corpus


RECIPE_PATHS = {
    "filing_passages": "Company-HAS_FILING->Filing-HAS_SECTION->Section-CONTAINS->Passage; keyword search over original text",
    "financial_passages": "Company-HAS_FILING->Filing-HAS_SECTION->Section(item7/item8)-CONTAINS->Passage; if empty try filing_passages",
    "risk_passages": "Company-HAS_FILING->Filing-HAS_SECTION->Section(item1a)-CONTAINS->Passage; if empty try filing_passages",
}
_READ = (
    "MATCH (c:FinCompany)-[a:HAS_FILING]->(f:FinFiling)-[b:HAS_SECTION]->(s:FinSection)"
    "-[d:CONTAINS]->(p:FinPassage) "
    "WHERE c.ticker = $node_id AND c._workspace_id = $workspace_id "
    "AND f._workspace_id = $workspace_id AND s._workspace_id = $workspace_id "
    "AND p._workspace_id = $workspace_id AND a._workspace_id = $workspace_id "
    "AND b._workspace_id = $workspace_id AND d._workspace_id = $workspace_id "
    "AND f.corpus_id = $corpus_id AND s.corpus_id = $corpus_id AND p.corpus_id = $corpus_id "
    "AND (size($sections) = 0 OR s.kind IN $sections) "
    "WITH c, f, s, p, reduce(score = 0, term IN $terms | "
    "score + CASE WHEN toLower(p.text) CONTAINS term THEN 1 ELSE 0 END) AS score "
    "WHERE score > 0 "
    "RETURN p.evidence_id AS evidence_id, c.ticker AS company, f.year AS filing_year, "
    "s.kind AS section, p.text AS text, p.source AS source, p.ordinal AS ordinal "
    "ORDER BY score DESC, p.ordinal ASC LIMIT 4"
)


def search_terms(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return list(dict.fromkeys(w for w in words if len(w) > 1 and w not in _STOPWORDS))[:24]


class FinderRetriever:
    def __init__(self, store: Any, *, workspace_id: str, corpus_id: str,
                 question: str, database: str = "neo4j") -> None:
        if not workspace_id.strip() or not corpus_id.strip():
            raise ValueError("workspace_id and corpus_id are required")
        self.store = store
        self.workspace_id = workspace_id
        self.corpus_id = corpus_id
        self.question = question
        self.database = database if database == "neo4j" else validate_database_name(database)

    def search(self, recipe_id: str, node_id: str, search_text: str) -> list[dict[str, Any]]:
        if recipe_id not in RECIPE_PATHS:
            raise ValueError("Unknown FinDER recipe")
        terms = search_terms(search_text or self.question)
        if not terms:
            return []
        sections = {"filing_passages": [], "financial_passages": ["item7", "item7a", "item8"],
                    "risk_passages": ["item1a"]}[recipe_id]
        params = {"node_id": node_id, "workspace_id": self.workspace_id, "corpus_id": self.corpus_id,
                  "terms": terms, "sections": sections}
        plan = CypherPlan(database=self.database, query=_READ, params=params,
                          strategy="finder_collaboration_recipe", anchor_entity=node_id)
        verdict = CypherQueryValidator().validate(plan, {
            "allowed_labels": ["FinCompany", "FinFiling", "FinSection", "FinPassage"],
            "allowed_relationship_types": ["HAS_FILING", "HAS_SECTION", "CONTAINS"],
            "allowed_properties": ["ticker", "_workspace_id", "corpus_id", "kind", "evidence_id", "text", "source", "ordinal", "year"],
            "max_result_rows": 4, "max_graph_hops": 3,
        })
        if not verdict["ok"]:
            raise ValueError(f"Invalid FinDER recipe: {verdict['violations']}")
        return self.store.query(_READ, params=params, database=self.database,
                                workspace_id=self.workspace_id, enforce_workspace_filter=True)

    def __call__(self, recipe_id: str, node_id: str) -> list[dict[str, Any]]:
        return self.search(recipe_id, node_id, "")


_SEED = """
UNWIND $rows AS row
MERGE (c:FinCompany {ticker: row.ticker, _workspace_id: $workspace_id})
MERGE (f:FinFiling {filing_id: row.filing_id, corpus_id: $corpus_id, _workspace_id: $workspace_id})
SET f.year = row.year, f.source = row.source
MERGE (c)-[:HAS_FILING {_workspace_id: $workspace_id}]->(f)
MERGE (s:FinSection {section_id: row.section_id, corpus_id: $corpus_id, _workspace_id: $workspace_id})
SET s.kind = row.section
MERGE (f)-[:HAS_SECTION {_workspace_id: $workspace_id}]->(s)
MERGE (p:FinPassage {evidence_id: row.evidence_id, corpus_id: $corpus_id, _workspace_id: $workspace_id})
SET p.text = row.text, p.source = row.source, p.ordinal = row.ordinal, p.start = row.start, p.end = row.end
MERGE (s)-[:CONTAINS {_workspace_id: $workspace_id}]->(p)
"""
_INVENTORY = (
    "MATCH (p:FinPassage) WHERE p._workspace_id = $workspace_id AND p.corpus_id = $corpus_id "
    "RETURN p.evidence_id AS evidence_id, p.text AS text, p.source AS source, "
    "p.ordinal AS ordinal, p.start AS start, p.end AS end"
)


def seed_corpus(store: Any, corpus: Corpus, *, workspace_id: str, database: str = "neo4j") -> None:
    # Validate before writes. No reset, DELETE, or production graph mutation.
    FinderRetriever(store, workspace_id=workspace_id, corpus_id=corpus.corpus_id, question="preflight", database=database)
    rows = []
    for filing in corpus.filings:
        for passage in filing.passages:
            rows.append({**passage.graph_row(), "year": filing.year,
                         "section_id": f"{filing.filing_id}:{passage.section}"})
    for offset in range(0, len(rows), 100):
        store.execute_write(_SEED, params={"rows": rows[offset:offset + 100],
                                          "workspace_id": workspace_id, "corpus_id": corpus.corpus_id},
                            database=database)


def verify_corpus(store: Any, corpus: Corpus, *, workspace_id: str, database: str = "neo4j") -> None:
    retriever = FinderRetriever(store, workspace_id=workspace_id, corpus_id=corpus.corpus_id,
                               question="preflight", database=database)
    rows = store.query(_INVENTORY, params={"workspace_id": workspace_id, "corpus_id": corpus.corpus_id},
                       database=retriever.database, workspace_id=workspace_id, enforce_workspace_filter=True)
    expected = [{key: getattr(p, key) for key in ("evidence_id", "text", "source", "ordinal", "start", "end")}
                for f in corpus.filings for p in f.passages]
    if sorted(rows, key=lambda r: r["evidence_id"]) != sorted(expected, key=lambda r: r["evidence_id"]):
        raise ValueError("FinDER source graph is absent, partial, or changed; use --seed in an isolated workspace")
    # Also inspect reachable source rows so missing/extra edges cannot pass a passage-only gate.
    topology = store.query(
        "MATCH (c:FinCompany)-[a:HAS_FILING]->(f:FinFiling)-[b:HAS_SECTION]->(s:FinSection)"
        "-[d:CONTAINS]->(p:FinPassage) WHERE c._workspace_id = $workspace_id "
        "AND f._workspace_id = $workspace_id AND s._workspace_id = $workspace_id "
        "AND p._workspace_id = $workspace_id AND a._workspace_id = $workspace_id "
        "AND b._workspace_id = $workspace_id AND d._workspace_id = $workspace_id "
        "AND f.corpus_id = $corpus_id AND s.corpus_id = $corpus_id AND p.corpus_id = $corpus_id "
        "RETURN c.ticker AS ticker, f.filing_id AS filing_id, f.year AS year, "
        "s.kind AS section, p.evidence_id AS evidence_id",
        params={"workspace_id": workspace_id, "corpus_id": corpus.corpus_id}, database=retriever.database,
        workspace_id=workspace_id, enforce_workspace_filter=True,
    )
    expected_paths = [{"ticker": f.ticker, "filing_id": f.filing_id, "year": f.year,
                       "section": p.section, "evidence_id": p.evidence_id}
                      for f in corpus.filings for p in f.passages]
    if sorted(topology, key=lambda r: (r["evidence_id"], r["ticker"])) != sorted(expected_paths, key=lambda r: (r["evidence_id"], r["ticker"])):
        raise ValueError("FinDER graph topology differs from the frozen source corpus")


def score_report(report: dict[str, Any], item: WorkItem,
                 mapping: list[dict[str, Any]]) -> dict[str, Any]:
    retrieved = {ref for receipt in report["retrieval_receipts"] for row in receipt["rows"]
                 if (ref := row.get("evidence_id"))}
    selected = {ref for packet in report["packets"] for ref in packet["evidence_refs"]}
    answer = report.get("answer") or {}
    cited = {ref for claim in answer.get("claims", []) for ref in claim["evidence_refs"]}
    mapped = [ref for ref in mapping if ref["status"] == "mapped"]

    def covered(match: dict[str, Any], refs: set[str]) -> bool:
        # Overlapping chunks are alternatives, not additional required gold.
        cursor = match["gold_start"]
        for span in sorted(match["spans"], key=lambda span: span["start"]):
            if span["evidence_id"] not in refs:
                continue
            if span["start"] > cursor:
                return False
            cursor = max(cursor, span["end"])
            if cursor >= match["gold_end"]:
                return True
        return False

    def coverage(refs: set[str]) -> float | None:
        if not mapped:
            return None
        count = sum(any(covered(match, refs) for match in ref["matches"]) for ref in mapped)
        return count / len(mapped)

    text = "\n".join(claim["text"] for claim in answer.get("claims", []))
    exact, contains = compare_answers(item.case.expected_answer, text)
    return {
        "gold_reference_count": len(mapping), "mapped_reference_count": len(mapped),
        "unmapped_reference_count": len(mapping) - len(mapped),
        "retrieved_reference_coverage": coverage(retrieved),
        "selected_reference_coverage": coverage(selected), "cited_reference_coverage": coverage(cited),
        "exact_match_diagnostic": bool(text) and exact, "contains_match_diagnostic": bool(text) and contains,
        "answer_slot_diagnostics": score_answer_slots(item.case.expected_answer, text),
        "answer_correctness": None,
        "note": "Coverage is conditional on exact normalized gold-to-source mapping. "
                "String/slot matches are diagnostics, not an entailment judgment; review unmapped gold separately.",
    }
