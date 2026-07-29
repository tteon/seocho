#!/usr/bin/env python3
"""Bundle the LaTeX sources into an Overleaf-importable zip.

There is no LaTeX toolchain in the development environment, so the page count
cannot be verified locally. This produces a flat, self-contained archive that
Overleaf can compile directly, which is how the 9-page body limit gets checked.

The file list is derived from the sources rather than hardcoded: every
\\includegraphics target and \\input is resolved, so a figure added to the paper
cannot be silently left out of the bundle.

Usage:  python3 papers/log2026/build_overleaf.py
Output: papers/log2026/overleaf.zip  (gitignored; rebuild on demand)
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Two submissions share one source tree: the 9-page proceedings paper and the
# 4-page extended abstract, which reuses the same figures, bibliography, and
# appendix and carries the omitted results in its own appendix section.
TARGETS = {
    "paper.tex": "overleaf-paper.zip",
    "abstract.tex": "overleaf-abstract.zip",
}


def collect(main: str) -> list[str]:
    """Resolve the transitive source set starting from the main file."""
    needed: list[str] = []
    pending = [main]
    seen: set[str] = set()

    while pending:
        name = pending.pop(0)
        if name in seen:
            continue
        seen.add(name)
        path = HERE / name
        if not path.is_file():
            raise SystemExit(f"missing source referenced by the paper: {name}")
        needed.append(name)
        text = path.read_text()

        # \input{appendix} -> appendix.tex
        for target in re.findall(r"\\input\{([^}]*)\}", text):
            pending.append(target if target.endswith(".tex") else f"{target}.tex")
        # \usepackage[...]{log_2026} -> log_2026.sty, when it ships with the paper
        for target in re.findall(r"\\usepackage(?:\[[^\]]*\])?\{([^}]*)\}", text):
            for pkg in (t.strip() for t in target.split(",")):
                if (HERE / f"{pkg}.sty").is_file():
                    pending.append(f"{pkg}.sty")
        # \bibliography{references} -> references.bib
        for target in re.findall(r"\\bibliography\{([^}]*)\}", text):
            pending.append(target if target.endswith(".bib") else f"{target}.bib")
        # Figures, kept as the .pdf the sources ask for.
        for target in re.findall(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]*)\}", text):
            if not (HERE / target).is_file():
                raise SystemExit(f"missing figure referenced by the paper: {target}")
            needed.append(target)

    return sorted(set(needed))


def main() -> int:
    for main_tex, out_name in TARGETS.items():
        if not (HERE / main_tex).is_file():
            print(f"skipping {out_name}: {main_tex} not present")
            continue
        files = collect(main_tex)
        out = HERE / out_name
        out.unlink(missing_ok=True)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for name in files:
                zf.write(HERE / name, name)
        limit = 9 if main_tex == "paper.tex" else 4
        print(f"{out.name}  {out.stat().st_size/1024:.0f} KiB  "
              f"main={main_tex}  body limit {limit} pages")
        for name in files:
            print(f"    {'figure' if name.endswith('.pdf') else 'source'}  {name}")
        print()

    print("Overleaf: New Project -> Upload Project -> select a zip. Set the main "
          "document if it is not detected automatically.")
    print("Check where the References heading falls; the body must end inside the "
          "page limit for that track.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
