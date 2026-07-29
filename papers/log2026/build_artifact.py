#!/usr/bin/env python3
"""Build the anonymized supplementary-materials zip for the LoG 2026 submission.

OpenReview accepts one .zip up to 50 MiB in Supplementary Materials, which is
where the reproducibility artifacts belong. The appendix is already merged into
paper.pdf, so this bundle carries only data and code.

Anonymity: the frozen artifacts are NOT edited in place -- they are the evaluation
record and must stay byte-stable. This copies them to a staging directory and
sanitizes the copy. One real leak exists and is why this script is necessary:
revised_blind_validation.json captured a CLI crash-reporter message containing
an absolute home-directory path, which names the author's account.

Usage:  python3 papers/log2026/build_artifact.py
Output: papers/log2026/supplementary.zip  (gitignored; rebuild on demand)
"""
from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVAL = ROOT / "outputs/evaluation/mdm_fedcat"
STAGE = ROOT / "papers/log2026/.artifact-stage"
OUT = ROOT / "papers/log2026/supplementary.zip"

# Artifact directories the paper cites, by the claim each one supports.
ARTIFACTS = {
    "log2026-full-finder-observation-v1": "paired indexing effects; full-corpus audit",
    "log2026-full-multiagent-network-v1": "category graph distributions; collapsed counts",
    "log2026-clean-entity-network-v1": "identifier-first resolution; PPR divergence",
    "log2026-entity-cleaning-ablation-v1": "phrase vs identifier AUROC ablation",
    "log2026-ontology-governance-ablation-v1": "offline ontology-governance arm",
    "log2026-mixed-routing-suite-v1": "80-query mixed routing suite composition",
    "log2026-sdcr-selector-eval-v1": "routing policy table; network tie-break ablation",
    "log2026-selector-robustness-v1": "coalition dropout; cost-weight sweep",
    "log2026-full-finder-cross-view-v1": "candidate pool, screening, revised set, answers",
    "log2026-capability-fallback-v1": "fallback replay and paid answer arm",
    "log2026-validated-issuer-pool-v1": "corrected issuer pool; core-disjoint manifest v2",
    "log2026-adversarial-answer-v3": "31-case conflict and protected-field tests",
    "log2026-factorial-mediation-v1": "orthogonal mediation calibration gate",
}

SCRIPTS = [
    "examples/mdm/46_log2026_paper_figures.py",
    "examples/mdm/73_sdcr_capability_fallback.py",
    "examples/mdm/74_validated_issuer_pool.py",
    "examples/mdm/75_fallback_answer_eval.py",
]

# Ordered so longer patterns win before their substrings.
REDACTIONS = (
    (re.compile(r"/home/[A-Za-z0-9._-]+"), "/home/anon"),
    (re.compile(r"\bhadry\b", re.I), "anon"),
    (re.compile(r"\btteon\b", re.I), "anon"),
    (re.compile(r"\bxcena\b", re.I), "anon"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", ), "anon@example.com"),
)

TEXT_SUFFIXES = {".json", ".md", ".py", ".txt", ".yaml", ".yml", ".owl", ".jsonl", ".mmd"}

README = """# Supplementary artifacts

Anonymized for double-blind review. Frozen evaluation records; not edited except
that absolute home paths, usernames, and email addresses are replaced with
placeholders (one artifact had captured a CLI crash message containing a home
path).

`outputs/` holds one directory per experiment. `scripts/` holds the analysis
entry points; each reads only from `outputs/` and performs no database writes.

## Zero-cost replay (no API keys, no database)

    python3 scripts/73_sdcr_capability_fallback.py   # fallback retrieval replay
    python3 scripts/74_validated_issuer_pool.py      # issuer-defect diagnostic
    python3 scripts/46_log2026_paper_figures.py      # all paper figures

`73` reproduces byte-identically. `74` re-derives the corrected candidate pool.

## Paid reproduction (optional, requires an inference key)

    python3 scripts/75_fallback_answer_eval.py       # 39 completions, resume-safe

Every completion is persisted immediately, so an interrupted run never repeats a
paid call.

## Claim to artifact map

| Artifact | Supports |
|---|---|
"""


def sanitize(text: str) -> str:
    for pattern, replacement in REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def copy_sanitized(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.suffix.lower() in TEXT_SUFFIXES:
        try:
            dst.write_text(sanitize(src.read_text()))
            return
        except UnicodeDecodeError:
            pass
    shutil.copy2(src, dst)


def main() -> int:
    if STAGE.exists():
        shutil.rmtree(STAGE)

    missing = [name for name in ARTIFACTS if not (EVAL / name).is_dir()]
    if missing:
        raise SystemExit(f"missing artifact directories, refusing to ship a partial bundle: {missing}")

    for name in ARTIFACTS:
        for src in sorted((EVAL / name).rglob("*")):
            if src.is_file():
                copy_sanitized(src, STAGE / "outputs" / name / src.relative_to(EVAL / name))

    for rel in SCRIPTS:
        src = ROOT / rel
        if not src.is_file():
            raise SystemExit(f"missing script {rel}")
        copy_sanitized(src, STAGE / "scripts" / src.name)

    readme = README + "".join(f"| `outputs/{k}` | {v} |\n" for k, v in ARTIFACTS.items())
    (STAGE / "README.md").write_text(readme)

    # Verify before packaging: a leak that reaches the zip breaks double-blind.
    # Check for the original identity markers, not the substitution patterns --
    # "/home/anon" matches the path pattern by construction and is not a leak.
    forbidden = (
        re.compile(r"/home/(?!anon\b)[A-Za-z0-9._-]+"),
        re.compile(r"\bhadry\b", re.I),
        re.compile(r"\btteon\b", re.I),
        re.compile(r"\bxcena\b", re.I),
        re.compile(r"(?!anon@example\.com)[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    )
    leaks: list[str] = []
    for path in STAGE.rglob("*"):
        if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
            try:
                body = path.read_text()
            except UnicodeDecodeError:
                continue
            for pattern in forbidden:
                hit = pattern.search(body)
                if hit:
                    leaks.append(f"{path.relative_to(STAGE)}: {hit.group(0)!r}")
    if leaks:
        raise SystemExit("identity strings survived sanitization:\n  " + "\n  ".join(leaks[:20]))

    OUT.unlink(missing_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(STAGE.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(STAGE))

    shutil.rmtree(STAGE)
    size_mb = OUT.stat().st_size / 1024 / 1024
    print(f"{OUT}  {size_mb:.1f} MiB  ({'OK' if size_mb < 50 else 'OVER 50 MiB LIMIT'})")
    print(f"{len(ARTIFACTS)} artifact directories, {len(SCRIPTS)} scripts, anonymity check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
