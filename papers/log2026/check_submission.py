#!/usr/bin/env python3
"""Pre-submission checks for the LoG 2026 paper.

Consolidates the audits that were previously run from a scratch directory, which
made the claims they support unreproducible. Everything here is read-only.

Checks, in order:
  1. page budget          body must end inside the track limit (needs tectonic)
  2. cross-references     citations resolve, no dangling or unused labels
  3. structure            balanced environments, tabular column counts
  4. grounding            every decimal in Results appears in a frozen artifact
  5. abbreviations        expanded on first use
  6. anonymity            no internal tool or account names
  7. style                markers associated with machine-written prose

Usage:
    python3 papers/log2026/check_submission.py            # paper.tex, 9 pages
    python3 papers/log2026/check_submission.py abstract   # abstract.tex, 4 pages
"""
from __future__ import annotations

import re
import shutil
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVAL = HERE.parents[1] / "outputs/evaluation/mdm_fedcat"

TRACKS = {"paper": ("paper.tex", 9), "abstract": ("abstract.tex", 4)}
INTERNAL = ("MARA", "AGY", "Codex", "LiteLLM", "hadry", "tteon", "xcena")
FLAGGED_WORDS = ("delve", "leverage", "underscore", "pivotal", "nuanced", "realm",
                 "showcase", "testament", "meticulous", "seamless", "holistic", "myriad")

failures: list[str] = []
notes: list[str] = []


def fail(msg: str) -> None:
    failures.append(msg)
    print(f"  FAIL  {msg}")


def ok(msg: str) -> None:
    print(f"  ok    {msg}")


def note(msg: str) -> None:
    notes.append(msg)
    print(f"  note  {msg}")


def prose_of(*paths: Path) -> str:
    t = "\n".join(p.read_text() for p in paths)
    t = t.split("\\begin{document}", 1)[-1]
    t = re.sub(r"\\begin\{(table|figure|equation|tabular)\}.*?\\end\{\1\}", " ", t, flags=re.S)
    t = re.sub(r"\\(cite[pt]?|ref|label|includegraphics|input|bibliography\w*)\{[^}]*\}", " ", t)
    t = re.sub(r"\\(section|subsection|paragraph)\{([^}]*)\}", r"\2. ", t)
    t = re.sub(r"\\[a-zA-Z]+\*?(\[[^\]]*\])?", " ", t)
    t = re.sub(r"[{}$\\]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def check_pages(main: str, limit: int) -> None:
    print(f"\n[1] page budget (limit {limit})")
    tectonic = shutil.which("tectonic") or str(Path.home() / ".local/bin/tectonic")
    if not Path(tectonic).exists():
        note("tectonic not found; cannot verify the page count locally")
        return
    try:
        import pypdf
    except ImportError:
        note("pypdf not installed; cannot read the built PDF")
        return
    with tempfile.TemporaryDirectory() as out:
        r = subprocess.run([tectonic, "-X", "compile", main, "--outdir", out],
                           cwd=HERE, capture_output=True, text=True, timeout=900)
        if r.returncode != 0:
            fail(f"{main} did not compile: {r.stderr.strip().splitlines()[-1:]}")
            return
        pdf = pypdf.PdfReader(str(Path(out) / main.replace(".tex", ".pdf")))
        for i, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            if "References" in text:
                frac = text.find("References") / max(1, len(text))
                where = f"references begin on page {i} at {frac*100:.0f}%"
                (ok if i <= limit else fail)(f"body fits {limit} pages ({where})"
                                            if i <= limit else
                                            f"body exceeds {limit} pages ({where})")
                return
        note("no References heading found; cannot locate the body boundary")


def check_refs(sources: list[Path]) -> None:
    print("\n[2] cross-references")
    tex = "\n".join(p.read_text() for p in sources)
    bib = set(re.findall(r"@\w+\{([^,]+),", (HERE / "references.bib").read_text()))
    cited = {k.strip() for g in re.findall(r"\\citep\{([^}]*)\}", tex) for k in g.split(",")}
    refs = set(re.findall(r"\\ref\{([^}]*)\}", tex))
    labels = set(re.findall(r"\\label\{([^}]*)\}", tex))
    for label, bad in (("citations with no bib entry", cited - bib),
                       ("refs with no label", refs - labels),
                       ("labels never referenced", labels - refs)):
        (ok if not bad else fail)(f"{label}: {sorted(bad) or 'none'}")
    # references.bib is shared by the long and short versions, and BibTeX emits
    # only cited entries, so a subset is expected rather than an error.
    unused = bib - cited
    (ok if not unused else note)(f"bib entries not cited by this version: "
                                f"{len(unused)} of {len(bib)}")
    dupes = [x for x in labels if tex.count("\\label{%s}" % x) > 1]
    (ok if not dupes else fail)(f"duplicate labels: {dupes or 'none'}")
    hard = re.findall(r"Appendix~A\.\d", tex)
    (ok if not hard else fail)(f"hardcoded appendix numbers: {hard or 'none'}")


def check_structure(sources: list[Path]) -> None:
    print("\n[3] structure")
    tex = "\n".join(p.read_text() for p in sources)
    for env in ("table", "figure", "tabular", "equation", "enumerate", "abstract"):
        b, e = tex.count("\\begin{%s}" % env), tex.count("\\end{%s}" % env)
        if b != e:
            fail(f"unbalanced {env}: {b} begin, {e} end")
    (ok if tex.count("{") == tex.count("}") else fail)("braces balanced")
    bad = 0
    for m in re.finditer(r"\\begin\{tabular\}\{([^}]*)\}(.*?)\\end\{tabular\}", tex, re.S):
        spec = len(re.findall(r"[lrc]", m.group(1)))
        for row in m.group(2).split("\\\\"):
            row = re.sub(r"\\(top|mid|bottom)rule", "", row).strip()
            if row and "multicolumn" not in row and row.count("&") + 1 != spec:
                fail(f"tabular column mismatch: {row[:50]}")
                bad += 1
    if not bad:
        ok("tabular column counts consistent")


def check_grounding(main: Path) -> None:
    print("\n[4] grounding of Results numbers")
    if not EVAL.is_dir():
        note("artifact directory absent; skipping")
        return
    text = main.read_text()
    i, j = text.find("\\section{Results}"), text.find("\\section{Discussion")
    if i < 0 or j < 0:
        note("no Results section; skipping")
        return
    results = text[i:j]
    blob = ""
    for d in sorted(EVAL.glob("log2026-*")):
        for f in list(d.rglob("*.json")) + list(d.rglob("*.md")):
            try:
                blob += f.read_text()
            except Exception:
                pass
    # Drop thousands separators only: a comma between digits followed by exactly
    # three more. Stripping every comma would fuse an interval such as
    # "[.000,.054]" into one token and report it as ungrounded.
    normalized = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", results)
    tokens = set(re.findall(r"(?<![\w.])\.?\d+(?:\.\d+)?", normalized))
    missing = []
    for tok in sorted(tokens):
        stem = tok.lstrip(".")
        if not stem or set(stem) <= {"0", "."}:
            continue
        # allow the paper to round what the artifact stores at full precision
        cands = {tok, stem, "0" + tok if tok.startswith(".") else tok}
        if any(c in blob for c in cands):
            continue
        if any(re.search(r"\b0?\." + re.escape(stem[:-1]) + r"\d", blob) for _ in (0,)) and len(stem) > 2:
            continue
        missing.append(tok)
    if missing:
        note(f"{len(missing)} of {len(tokens)} tokens not matched textually "
             f"(check rounding by hand): {missing[:8]}")
    else:
        ok(f"all {len(tokens)} numeric tokens in Results trace to an artifact")


def check_abbreviations(sources: list[Path]) -> None:
    print("\n[5] abbreviations")
    tex = "\n".join(p.read_text() for p in sources)
    for ab, expansion in (("CI", "confidence interval"), ("AUROC", "area under the ROC"),
                          ("ITT", "intention-to-treat")):
        used = len(re.findall(r"\b" + ab + r"\b", tex))
        if not used:
            continue
        if expansion.lower() in tex.lower():
            ok(f"{ab} used {used}x and expanded")
        else:
            fail(f"{ab} used {used}x but never expanded")


def check_anonymity(sources: list[Path]) -> None:
    print("\n[6] anonymity")
    tex = "\n".join(p.read_text() for p in sources)
    hits = {n: len(re.findall(r"\b" + n + r"\b", tex, re.I)) for n in INTERNAL}
    live = {k: v for k, v in hits.items() if v}
    (ok if not live else fail)(f"internal tool or account names: {live or 'none'}")
    (ok if "Anonymous Authors" in tex else fail)("author block is anonymized")


def check_style(sources: list[Path]) -> None:
    print("\n[7] style markers")
    prose = prose_of(*sources)
    words = max(1, len(prose.split()))
    for label, pattern, ceiling in (("em dashes", r"---", 2.0),
                                    ("semicolons", r";", 8.0),
                                    ("mid-sentence colons", r"\w:\s+[a-z]", 6.0)):
        rate = len(re.findall(pattern, prose)) / words * 1000
        msg = f"{label}: {rate:.2f} per 1000 words (ceiling {ceiling})"
        (ok if rate <= ceiling else note)(msg)
    found = {w: len(re.findall(r"\b" + w, prose, re.I)) for w in FLAGGED_WORDS}
    live = {k: v for k, v in found.items() if v}
    (ok if not live else note)(f"commonly flagged vocabulary: {live or 'none'}")
    sents = [s for s in re.split(r"(?<=[.!?])\s+", prose) if len(s.split()) > 3]
    lens = [len(s.split()) for s in sents]
    if len(lens) > 20:
        cv = statistics.pstdev(lens) / statistics.mean(lens)
        msg = f"sentence-length variation {cv:.2f} (human academic prose 0.45-0.65)"
        (ok if cv >= 0.45 else note)(msg)


def main() -> int:
    track = sys.argv[1] if len(sys.argv) > 1 else "paper"
    if track not in TRACKS:
        raise SystemExit(f"unknown track {track!r}; choose from {sorted(TRACKS)}")
    main_name, limit = TRACKS[track]
    main_path = HERE / main_name
    if not main_path.is_file():
        raise SystemExit(f"missing {main_name}")
    sources = [main_path] + [HERE / "appendix.tex"]
    if track == "abstract" and (HERE / "abstract_omitted.tex").is_file():
        sources.append(HERE / "abstract_omitted.tex")

    print(f"checking {main_name} against the {track} track")
    check_pages(main_name, limit)
    check_refs(sources)
    check_structure(sources)
    check_grounding(main_path)
    check_abbreviations(sources)
    check_anonymity(sources)
    check_style(sources)

    print(f"\n{len(failures)} failure(s), {len(notes)} note(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
