"""Every shipped package must have its `__init__.py` tracked in git.

This exists because the ontology package shipped broken and CI caught it after
review. `.gitignore` carried an unanchored `ontology/` pattern — the comment
said "root ontology/", but without a leading slash it matches a directory of
that name at any depth, including `src/seocho/ontology/`. The sixteen modules
survived because `git mv` stages explicitly; the newly written `__init__.py`
was skipped silently by `git add -A`.

The failure mode is the dangerous kind: the file is on disk, so every local
run passes. Only a fresh checkout notices, and then setuptools resolves the
directory as a namespace package and the error reads
"cannot import name 'Ontology' from 'seocho.ontology' (unknown location)" —
which points at the import, not at the missing file.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SHIPPED = ("src/seocho",)


def _tracked() -> set[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=_ROOT, capture_output=True, text=True, check=True
    )
    return set(out.stdout.splitlines())


def test_every_package_directory_has_a_tracked_init():
    tracked = _tracked()
    missing = []
    for base in _SHIPPED:
        for path in sorted((_ROOT / base).rglob("*.py")):
            rel_dir = path.parent.relative_to(_ROOT)
            if "__pycache__" in rel_dir.parts:
                continue
            init = f"{rel_dir}/__init__.py"
            if (_ROOT / init).exists() and init not in tracked:
                missing.append(init)
    assert not missing, (
        f"__init__.py present on disk but not tracked: {sorted(set(missing))}. "
        "Check .gitignore for an unanchored directory pattern."
    )


def test_no_shipped_python_file_is_gitignored():
    """A source file matching .gitignore is invisible to a fresh checkout."""
    for base in _SHIPPED:
        files = [
            str(p.relative_to(_ROOT))
            for p in (_ROOT / base).rglob("*.py")
            if "__pycache__" not in p.parts
        ]
        result = subprocess.run(
            ["git", "check-ignore", "--stdin"],
            cwd=_ROOT,
            input="\n".join(files),
            capture_output=True,
            text=True,
        )
        ignored = [line for line in result.stdout.splitlines() if line.strip()]
        assert not ignored, f"shipped sources are gitignored: {ignored}"


def test_public_surface_directory_patterns_are_anchored():
    """`ontology/` and `dataset/` must only exclude the ROOT directories.

    `CLAUDE.md` names them as non-tracked public surfaces at the repository
    root. Unanchored, they also exclude `src/seocho/ontology/`, which is a
    shipped package.
    """
    text = (_ROOT / ".gitignore").read_text(encoding="utf-8")
    lines = [line.strip() for line in text.splitlines()]
    for name in ("ontology", "dataset"):
        assert f"{name}/" not in lines, (
            f"'{name}/' is unanchored in .gitignore and matches any depth; "
            f"use '/{name}/'"
        )
        assert f"/{name}/" in lines, f"'/{name}/' missing from .gitignore"
