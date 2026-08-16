#!/usr/bin/env python3
"""Keep `.env.example` honest about what the code and compose actually read.

Measured before writing this: the code reads 73 environment variables, compose
files reference 66, and `.env.example` declares 44 — with 61 code variables and
27 compose variables undocumented, and four documented names used nowhere at
all. Three surfaces, three different answers, and the one a new user reads is
the smallest.

That matters more than tidiness. `DOZERDB_URI`, `DOZERDB_PASSWORD` and
`MARA_API_KEY` were all missing, so the documented surface omitted the graph
backend and the default LLM provider — a reader could not start the system from
it. And it blocks the etcd direction outright: a configuration surface nobody
can enumerate cannot be moved into a key space, because nobody knows what to put
there.

This does not generate `.env.example`. The file carries hand-written grouping and
guidance ("Host-side scripts should use localhost; docker services override
this") that a generator would destroy, and that guidance is the part a reader
needs. Instead it reports the gap in both directions and fails on it, so the
file stays hand-written and cannot drift.

Deliberately not flagged:

  well-known third-party names   OPENAI_API_KEY, HF_TOKEN and friends are read
                                 by libraries, not only by us; documenting them
                                 is useful but their absence is not a defect
  test-only variables            anything read solely under tests/ or scripts/

Usage:
    python3 scripts/ci/check-env-contract.py            # check, exit 1 on gap
    python3 scripts/ci/check-env-contract.py --list     # print the gap, exit 0
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, Set

ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = ROOT / ".env.example"

#: Surfaces whose configuration a user must be able to discover.
CODE_PATHS = ("src/seocho", "runtime", "extraction")

_READ = re.compile(
    r'(?:getenv|environ\.get)\(\s*"([A-Z][A-Z0-9_]*)"'
    r'|environ\[\s*"([A-Z][A-Z0-9_]*)"\]',
    re.S,
)
# A commented-out declaration still documents the variable. It must, because
# an uncommented empty one is actively harmful: compose injects `NAME=` as a
# present-but-empty key, and os.getenv("NAME", default) then returns "" rather
# than the default. Documenting a variable must not change behaviour.
_DECLARED = re.compile(r"^#?\s*([A-Z][A-Z0-9_]*)=", re.M)
_COMPOSE_SUBST = re.compile(r"\$\{([A-Z][A-Z0-9_]*)")
_COMPOSE_ENV = re.compile(r"^\s*-\s*([A-Z][A-Z0-9_]*)=", re.M)

# Read by libraries we depend on rather than only by us. Their absence from
# .env.example is a documentation nicety, not a broken contract.
THIRD_PARTY = {
    "HF_TOKEN", "HF_HOME", "HUGGINGFACE_HUB_CACHE", "GITHUB_TOKEN",
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "ANTHROPIC_API_KEY",
    "PYTHONPATH", "PATH", "HOME", "USER", "TZ", "VIRTUAL_ENV",
    "OTEL_EXPORTER_OTLP_ENDPOINT", "NO_PROXY", "HTTP_PROXY", "HTTPS_PROXY",
}


def code_variables() -> Dict[str, str]:
    """name -> the first file that reads it, for a message that can be acted on.

    Matched against whole file text rather than per line. A line-based scan
    misses the call black formats across two lines --

        registry = os.getenv(
            "SEOCHO_GRAPH_REGISTRY_FILE", default)

    -- and `extraction/config.py:281` is exactly that shape, so the variable
    read a normal way by production code was reported as read nowhere.
    """
    found: Dict[str, str] = {}
    for base in CODE_PATHS:
        result = subprocess.run(
            ["git", "ls-files", "--", f"{base}/**.py", f"{base}/*.py"],
            cwd=ROOT, capture_output=True, text=True,
        )
        for rel in result.stdout.split():
            try:
                text = (ROOT / rel).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for first, second in _READ.findall(text):
                name = first or second
                if name:
                    found.setdefault(name, rel)
    return found


def script_variables() -> Set[str]:
    """Names read by scripts/ — used only to suppress the orphan check.

    A variable read by an operator script (`scripts/setup/agent-memory-postgres.py`
    reads SEOCHO_POSTGRES_DSN) is documented for a reason and is not orphaned.
    Scripts are deliberately excluded from CODE_PATHS, though: a script-only
    variable does not have to appear in .env.example, because .env.example is
    the surface for running the system, not for running every tool in the repo.
    """
    found: Set[str] = set()
    result = subprocess.run(
        ["git", "ls-files", "--", "scripts/**.py", "scripts/*.py"],
        cwd=ROOT, capture_output=True, text=True,
    )
    for rel in result.stdout.split():
        try:
            text = (ROOT / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for first, second in _READ.findall(text):
            if first or second:
                found.add(first or second)
    return found


def provider_key_variables() -> Dict[str, str]:
    """Env names that live as DATA rather than as literals in a getenv call.

    `ProviderSpec.api_key_env` / `api_key_env_aliases` are dataclass fields, and
    `_resolve_client_kwargs` looks them up through a variable
    (`os.getenv(env_name)`). A regex over call sites cannot see them.

    That is not a cosmetic gap: `SEOCHO_VLLM_API_KEY` is the credential for the
    vLLM serving tier, and the checker was reporting "all three surfaces agree"
    while it was undocumented. A gate that reports success on the one variable
    the target deployment most needs is worse than no gate.
    """
    found: Dict[str, str] = {}
    try:
        import sys

        sys.path.insert(0, str(ROOT / "src"))
        from seocho.store.llm import list_provider_specs
    except Exception:
        return found
    for spec in list_provider_specs().values():
        for name in (spec.api_key_env, *spec.api_key_env_aliases):
            if name:
                found.setdefault(name, f"src/seocho/store/llm.py (provider {spec.name!r})")
    return found


def _strip_yaml_comments(text: str) -> str:
    """Drop whole-line comments before scanning for variables.

    A comment explaining the substitution rule -- "compose reads .env for
    ${VAR} substitution" -- is prose, not a reference, and counting it demanded
    that a variable named `VAR` be documented. Only full-line comments are
    removed: a trailing `#` inside a value is part of the value, and passwords
    routinely contain one.
    """
    return "\n".join(
        "" if line.lstrip().startswith("#") else line
        for line in text.splitlines()
    )


def compose_variables() -> Dict[str, str]:
    found: Dict[str, str] = {}
    for pattern in ("docker-compose*.yml", "compose*.yaml", "docker/*.yaml",
                    "docker/*.yml"):
        for path in sorted(ROOT.glob(pattern)):
            text = _strip_yaml_comments(path.read_text(encoding="utf-8"))
            for name in _COMPOSE_SUBST.findall(text) + _COMPOSE_ENV.findall(text):
                found.setdefault(name, str(path.relative_to(ROOT)))
    return found


def declared_variables() -> Set[str]:
    if not ENV_EXAMPLE.exists():
        return set()
    return set(_DECLARED.findall(ENV_EXAMPLE.read_text(encoding="utf-8")))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true",
                        help="report the gap without failing")
    args = parser.parse_args()

    code = code_variables()
    # Names carried as provider-preset data, invisible to the regex.
    for name, origin in provider_key_variables().items():
        code.setdefault(name, origin)
    compose = compose_variables()
    declared = declared_variables()

    missing_code = {n: p for n, p in sorted(code.items())
                    if n not in declared and n not in THIRD_PARTY}
    missing_compose = {n: p for n, p in sorted(compose.items())
                       if n not in declared and n not in THIRD_PARTY
                       and n not in code}
    # A documented name nothing reads is its own defect: it tells a user to set
    # something that has no effect.
    orphaned = sorted(declared - set(code) - set(compose)
                      - THIRD_PARTY - script_variables())

    print(f"env contract: code reads {len(code)}, compose references "
          f"{len(compose)}, .env.example declares {len(declared)}")

    if missing_code:
        print(f"\nread by code, absent from .env.example ({len(missing_code)}):")
        for name, path in missing_code.items():
            print(f"  {name:38s} {path}")
    if missing_compose:
        print(f"\nreferenced by compose only, absent from .env.example "
              f"({len(missing_compose)}):")
        for name, path in missing_compose.items():
            print(f"  {name:38s} {path}")
    if orphaned:
        print(f"\ndeclared but read nowhere ({len(orphaned)}):")
        for name in orphaned:
            print(f"  {name}")

    if not (missing_code or missing_compose or orphaned):
        print("\nall three surfaces agree.")
        return 0

    if args.list:
        return 0
    print("\n.env.example is the surface a new user reads. A variable missing "
          "from it\nis a setting nobody can discover; a variable in it that "
          "nothing reads is a\nsetting that does nothing. Both are failures.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
