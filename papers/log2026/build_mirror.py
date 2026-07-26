#!/usr/bin/env python3
"""Generate PAPER.md as a read-only mirror of paper.tex.

paper.tex is the single source of truth for the LoG 2026 submission (references
live in references.bib, appendix in appendix.tex). PAPER.md exists only so the
paper can be read and reviewed without a LaTeX toolchain.

Keeping the Markdown generated rather than hand-maintained removes a defect
class that previously shipped: the two documents drifted, so the reference list
gained duplicate numbers, a pre-registered baseline row went missing from one
table, and a Reproducibility paragraph existed in only one of them.

Usage:  python3 papers/log2026/build_mirror.py
"""
from __future__ import annotations

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEX = HERE / "paper.tex"
APPENDIX = HERE / "appendix.tex"
BIB = HERE / "references.bib"
OUT = HERE / "PAPER.md"

HEADER = """<!-- GENERATED FILE - DO NOT EDIT.
     Source of truth: paper.tex (+ references.bib, appendix.tex).
     Regenerate with: python3 papers/log2026/build_mirror.py -->

"""


def bib_order() -> list[str]:
    """Citation keys in the order plainnat/natbib would number them (sorted)."""
    keys = re.findall(r"@\w+\{([^,]+),", BIB.read_text())
    return sorted(keys)


def inline(text: str, cites: dict[str, int], labels: dict[str, str]) -> str:
    """Convert inline LaTeX markup to Markdown."""
    def cite_repl(match: re.Match[str]) -> str:
        nums = sorted(cites.get(k.strip(), 0) for k in match.group(1).split(","))
        return "[" + ", ".join(str(n) for n in nums) + "]"

    # Leave the "~" in place; it becomes the space LaTeX would typeset.
    text = re.sub(r"\\citep\{([^}]*)\}", cite_repl, text)
    text = re.sub(r"\\ref\{([^}]*)\}", lambda m: labels.get(m.group(1), "?"), text)
    text = re.sub(r"\\textbf\{([^{}]*)\}", r"**\1**", text)
    text = re.sub(r"\\emph\{([^{}]*)\}", r"*\1*", text)
    text = re.sub(r"\\texttt\{([^{}]*)\}", r"`\1`", text)
    text = text.replace("\\%", "%").replace("\\&", "&").replace("\\_", "_")
    text = text.replace("{,}", ",")
    text = text.replace("---", "—").replace("--", "–")
    text = text.replace("``", '"').replace("''", '"')
    # Unwrap single-symbol math so the mirror does not render bare "$×$".
    for tex_sym, uni in (("\\times", "×"), ("\\geq", "≥"), ("\\leq", "≤"),
                         ("\\varnothing", "∅"), ("\\rightarrow", "→")):
        text = text.replace(f"${tex_sym}$", uni).replace(tex_sym, uni)
    # Unwrap math that is only plain characters, so "$-$" and "$[0,.041]$" read
    # as text. "[^$]*" keeps delimiters correctly paired; the decision is made
    # per span, so real formulas (backslashes, sub/superscripts) stay in math.
    def unwrap_math(match: re.Match[str]) -> str:
        inner = match.group(1)
        plain = not any(ch in inner for ch in "\\_^{}")
        return inner if plain else f"${inner}$"

    text = re.sub(r"\$([^$]*)\$", unwrap_math, text)
    text = re.sub(r"~", " ", text)
    return re.sub(r" {2,}", " ", text).strip()


def convert_table(body: str, cites: dict[str, int], labels: dict[str, str]) -> list[str]:
    rows: list[list[str]] = []
    for raw in body.split("\\\\"):
        line = raw.strip()
        if not line or line.startswith(("\\toprule", "\\midrule", "\\bottomrule")):
            line = re.sub(r"\\(top|mid|bottom)rule", "", line).strip()
            if not line:
                continue
        cells = [inline(c, cites, labels) for c in line.split("&")]
        cells = [re.sub(r"\\multirow\{\d+\}\{[^}]*\}\{([^}]*)\}", r"\1", c) for c in cells]
        if any(cells):
            rows.append(cells)
    if not rows:
        return []
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |",
           "|" + "|".join(["---"] * width) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return out


def render(body: str, cites: dict[str, int], labels: dict[str, str],
           heading_shift: bool = False) -> list[str]:
    """Walk one LaTeX body and emit Markdown lines.

    heading_shift demotes appendix headings one level so they nest under the
    single appendix heading rather than competing with the paper's sections.
    """
    lines: list[str] = []
    i = 0
    while i < len(body):
        # Abstract.
        m = re.compile(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", re.S).match(body, i)
        if m:
            lines += ["\n## Abstract\n", inline(m.group(1), cites, labels), ""]
            i = m.end()
            continue
        # Figures.
        m = re.compile(r"\\begin\{figure\}.*?\\end\{figure\}", re.S).match(body, i)
        if m:
            block = m.group(0)
            img = re.search(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]*)\}", block)
            cap = re.search(r"\\caption\{(.*?)\}\s*\n", block, re.S)
            src = (img.group(1).replace(".pdf", ".png") if img else "")
            text = inline(cap.group(1), cites, labels) if cap else ""
            lines += [f"\n![{text}]({src})\n"]
            i = m.end()
            continue
        # Tables.
        m = re.compile(r"\\begin\{table\}.*?\\end\{table\}", re.S).match(body, i)
        if m:
            block = m.group(0)
            cap = re.search(r"\\caption\{(.*?)\}\s*\n", block, re.S)
            tab = re.search(r"\\begin\{tabular\}\{[^}]*\}(.*?)\\end\{tabular\}", block, re.S)
            if cap:
                lines += ["", f"*{inline(cap.group(1), cites, labels)}*", ""]
            if tab:
                lines += convert_table(tab.group(1), cites, labels)
            lines.append("")
            i = m.end()
            continue
        # Equations.
        m = re.compile(r"\\begin\{equation\}(.*?)\\end\{equation\}", re.S).match(body, i)
        if m:
            lines += ["", "$$" + m.group(1).strip() + "$$", ""]
            i = m.end()
            continue
        # Enumerate.
        m = re.compile(r"\\begin\{enumerate\}(.*?)\\end\{enumerate\}", re.S).match(body, i)
        if m:
            items = [x.strip() for x in m.group(1).split("\\item") if x.strip()]
            lines.append("")
            for n, item in enumerate(items, 1):
                lines.append(f"{n}. {inline(item, cites, labels)}")
            lines.append("")
            i = m.end()
            continue
        # Headings.
        m = re.compile(r"\\(section|subsection|paragraph)\{((?:[^{}]|\{[^{}]*\})*)\}").match(body, i)
        if m:
            depth = {"section": "##", "subsection": "###", "paragraph": "####"}[m.group(1)]
            if heading_shift:
                depth += "#"
            lines += ["", f"{depth} {inline(m.group(2), cites, labels)}", ""]
            i = m.end()
            continue
        # Skip remaining structural commands.
        m = re.compile(r"\\(appendix|input\{[^}]*\}|bibliography\{[^}]*\})").match(body, i)
        if m:
            i = m.end()
            continue
        # Prose paragraph: run to the next LaTeX block command.
        nxt = re.compile(r"\\(begin|section|subsection|paragraph|appendix|input)\b").search(body, i + 1)
        end = nxt.start() if nxt else len(body)
        chunk = inline(body[i:end], cites, labels)
        if chunk:
            for para in re.split(r"\n\s*\n", chunk):
                if para.strip():
                    lines += [" ".join(para.split()), ""]
        i = end

    return lines


def main() -> int:
    tex = TEX.read_text()
    keys = bib_order()
    cites = {k: i + 1 for i, k in enumerate(keys)}

    body = tex.split("\\begin{document}", 1)[1].split("\\bibliographystyle", 1)[0]
    # Drop LaTeX-only commands that carry no prose.
    body = re.sub(r"\\maketitle|\\centering|\\small|\\itemsep\s*\S+", "", body)
    body = re.sub(r"\\label\{[^}]*\}", "", body)

    # Number sections, figures, and tables so cross-references resolve. Labels
    # map to bare numbers because the prose already writes "Figure~\ref{...}".
    labels: dict[str, str] = {}
    fig_n = tab_n = sec_n = sub_n = 0
    token = re.compile(
        r"\\(section|subsection)\{|\\begin\{(figure|table)\}|\\label\{([^}]*)\}")
    pending = None
    for m in token.finditer(tex):
        if m.group(1) == "section":
            sec_n, sub_n, pending = sec_n + 1, 0, str(sec_n + 1)
        elif m.group(1) == "subsection":
            sub_n += 1
            pending = f"{sec_n}.{sub_n}"
        elif m.group(2) == "figure":
            fig_n += 1
            pending = str(fig_n)
        elif m.group(2) == "table":
            tab_n += 1
            pending = str(tab_n)
        elif m.group(3) and pending is not None:
            labels[m.group(3)] = pending

    # The appendix is a separate file but shares the float counters and is
    # numbered A.1, A.2, ... Without this pass, "Appendix~\ref{app:data}" in the
    # body would render with no number at all.
    app_n = 0
    pending = None
    for m in token.finditer(APPENDIX.read_text()):
        if m.group(1) == "subsection":
            app_n += 1
            pending = f"A.{app_n}"
        elif m.group(2) == "figure":
            fig_n += 1
            pending = str(fig_n)
        elif m.group(2) == "table":
            tab_n += 1
            pending = str(tab_n)
        elif m.group(3) and pending is not None:
            labels[m.group(3)] = pending

    lines: list[str] = []
    title = re.search(r"\\title(?:\[[^\]]*\])?\{(.+?)\}\s*\n", tex, re.S)
    if title:
        parts = [p.strip() for p in title.group(1).split("\\\\")]
        lines.append(f"# {inline(parts[0], cites, labels)}")
        for p in parts[1:]:
            lines.append(f"\n## {inline(p, cites, labels)}")
        lines.append("")

    lines += render(body, cites, labels)

    # The appendix carries substantive content (the fallback table and the
    # lineage/category figures), so a mirror that omitted it would be materially
    # incomplete for anyone reading without a LaTeX toolchain.
    appendix = re.sub(r"\\label\{[^}]*\}", "",
                      re.sub(r"\\centering|\\small", "", APPENDIX.read_text()))
    lines += ["", "## Appendix A. Reproducibility Protocol", ""]
    lines += render(re.sub(r"^\\section\{[^}]*\}", "", appendix.strip()), cites, labels,
                    heading_shift=True)

    # Reference list, numbered exactly as natbib would.
    entries = {}
    for block in re.findall(r"@\w+\{(.+?)\n\}", BIB.read_text(), re.S):
        key = block.split(",", 1)[0].strip()
        title = re.search(r"title=\{+(.+?)\}+,", block)
        author = re.search(r"author=\{(.+?)\},", block)
        year = re.search(r"year=\{(\d{4})\}", block)
        venue = re.search(r"(?:booktitle|journal)=\{+(.+?)\}+[,}]", block)
        bits = []
        if author:
            bits.append(re.sub(r"\{|\}", "", author.group(1)))
        if title:
            bits.append(f'"{title.group(1)}"')
        if venue:
            bits.append(venue.group(1))
        if year:
            bits.append(year.group(1))
        entries[key] = ". ".join(bits) + "."
    lines += ["", "## References", ""]
    for key in keys:
        lines.append(f"{cites[key]}. {entries.get(key, key)}")
    lines.append("")

    text = HEADER + "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    OUT.write_text(text)
    print(f"{OUT}  ({len(text.splitlines())} lines, {len(keys)} references)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
