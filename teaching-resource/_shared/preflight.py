"""Pre-flight diagnostic for the teaching curriculum.

Run BEFORE chapter-00-setup.ipynb to validate the environment:

    python -m _shared.preflight

Outputs a 4-row table:
    [Providers]  [Opik]  [FinDER]  [Neo4j]

Each row is OK / WARN / FAIL with a one-line hint. Exit code is non-zero if
any row is FAIL (so this can also gate CI / shell scripts).

Design goals
------------
- Zero side effects beyond reading env + a single tiny network probe per row.
- Never prints API keys or secret values.
- Friendly hints for the most common misconfigurations.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


@dataclass
class CheckResult:
    name: str
    status: str  # "OK" | "WARN" | "FAIL"
    detail: str
    hint: Optional[str] = None


# ---------------------------------------------------------------------------
# Individual probes
# ---------------------------------------------------------------------------


def check_providers() -> CheckResult:
    """Inspect 4 provider keys. OK if ≥2 configured, WARN if only OpenAI, FAIL otherwise."""
    keys = {
        "openai": "OPENAI_API_KEY",
        "kimi": "MOONSHOT_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "grok": "XAI_API_KEY",
    }
    have = {name: bool(os.getenv(env)) for name, env in keys.items()}
    configured = [n for n, ok in have.items() if ok]
    detail = "configured: " + (", ".join(configured) if configured else "<none>")

    if len(configured) >= 2:
        return CheckResult("Providers", "OK", detail)
    if "openai" in configured:
        return CheckResult(
            "Providers",
            "WARN",
            detail,
            hint="Only OpenAI configured — 4-provider comparison cells will skip 3/4 rows. "
            "Add MOONSHOT_API_KEY / DEEPSEEK_API_KEY / XAI_API_KEY to .env for full demos.",
        )
    return CheckResult(
        "Providers",
        "FAIL",
        detail,
        hint="No usable provider key found. At minimum set OPENAI_API_KEY in .env.",
    )


def check_opik() -> CheckResult:
    """Verify Opik can construct a client with the current env."""
    user = os.getenv("OPIK_USER", "").strip()
    workspace = os.getenv("OPIK_WORKSPACE", "seocho").strip()
    api_key = os.getenv("OPIK_API_KEY", "").strip()

    if not user:
        return CheckResult(
            "Opik",
            "WARN",
            f"workspace={workspace}, user=<unset>",
            hint="Set OPIK_USER (e.g. OPIK_USER=hardy) so chapter notebooks create "
            "'teaching-ch{N}-{user}' projects scoped to you.",
        )

    if not api_key:
        return CheckResult(
            "Opik",
            "WARN",
            f"workspace={workspace}, user={user}, api_key=<unset>",
            hint="JSONL fallback only. Set OPIK_API_KEY to push traces to comet.com.",
        )

    try:
        import opik  # noqa: F401
    except ImportError:
        return CheckResult(
            "Opik",
            "FAIL",
            "opik package not installed",
            hint="pip install opik",
        )

    return CheckResult(
        "Opik",
        "OK",
        f"workspace={workspace}, user={user}, sdk installed, api_key set",
    )


def check_finder(refresh: bool = False) -> CheckResult:
    """Confirm FinDER is loadable. Counts records + 8 categories."""
    try:
        from _shared.finder_loader import load_finder, FINDER_CATEGORIES
    except ImportError as exc:
        return CheckResult("FinDER", "FAIL", f"loader import failed: {exc}")

    cache = Path(os.getenv("FINDER_CACHE_DIR", "./data")) / "finder_corpus.parquet"
    cache_state = "cached" if cache.exists() else "will download"

    if not cache.exists() and not refresh:
        return CheckResult(
            "FinDER",
            "WARN",
            f"{cache_state} (cache miss)",
            hint=f"Run `python -m _shared.preflight --refresh-finder` or open "
            f"chapter-00-setup.ipynb cell 'load_finder()' once to populate {cache}.",
        )

    try:
        ds = load_finder(refresh=refresh)
        n = len(ds)
        cats = set(ds["category"]) if n else set()
        missing = [c for c in FINDER_CATEGORIES if c not in cats]
        if missing:
            return CheckResult(
                "FinDER",
                "WARN",
                f"{n} records, {len(cats)} categories ({cache_state})",
                hint=f"Missing categories: {missing}. The HF dataset coordinates may have moved.",
            )
        return CheckResult(
            "FinDER",
            "OK",
            f"{n} records, 8 categories ({cache_state})",
        )
    except Exception as exc:
        return CheckResult(
            "FinDER",
            "FAIL",
            f"{type(exc).__name__}: {exc}",
            hint="Check FINDER_HF_REPO / FINDER_HF_SUBSET / FINDER_HF_SPLIT env vars.",
        )


def check_neo4j() -> CheckResult:
    """Probe Neo4j/DozerDB with a trivial component lookup."""
    uri = os.getenv("NEO4J_URI")
    pwd = os.getenv("NEO4J_PASSWORD")
    user = os.getenv("NEO4J_USER", "neo4j")
    if not (uri and pwd):
        return CheckResult(
            "Neo4j",
            "WARN",
            "NEO4J_URI / NEO4J_PASSWORD unset",
            hint="Ch 1, 2 require this. Ch 3, 4 run partial demos without it.",
        )

    try:
        from neo4j import GraphDatabase
    except ImportError:
        return CheckResult(
            "Neo4j",
            "FAIL",
            "neo4j driver not installed",
            hint="pip install neo4j",
        )

    try:
        drv = GraphDatabase.driver(uri, auth=(user, pwd))
        with drv.session() as s:
            comp = s.run(
                "CALL dbms.components() YIELD name, versions RETURN name, versions[0] AS v"
            ).data()
        drv.close()
        if not comp:
            return CheckResult("Neo4j", "WARN", f"{uri} reachable but empty version reply")
        primary = comp[0]
        return CheckResult(
            "Neo4j",
            "OK",
            f"{uri} · {primary['name']} {primary['v']}",
        )
    except Exception as exc:
        return CheckResult(
            "Neo4j",
            "FAIL",
            f"{type(exc).__name__}: {str(exc)[:120]}",
            hint="Confirm the bolt URI is reachable and the password is correct. "
            "For DozerDB also verify `apoc.*` / `n10s.*` / `gds.*` are exposed.",
        )


# ---------------------------------------------------------------------------
# Output rendering
# ---------------------------------------------------------------------------


_GLYPH = {"OK": "✅", "WARN": "⚠️ ", "FAIL": "❌"}


def render(results: list[CheckResult], *, color: bool = True) -> str:
    def _w(s: str, code: str) -> str:
        if not color:
            return s
        return f"\033[{code}m{s}\033[0m"

    code = {"OK": "32", "WARN": "33", "FAIL": "31"}
    lines = []
    lines.append(_w("─" * 78, "90"))
    lines.append(_w(f"{'Check':<14} {'Status':<8} Detail", "90"))
    lines.append(_w("─" * 78, "90"))
    for r in results:
        status = _w(f"{_GLYPH[r.status]} {r.status:<5}", code[r.status])
        lines.append(f"{r.name:<14} {status}  {r.detail}")
        if r.hint and r.status != "OK":
            lines.append(_w(f"{'':<14} hint   →  {r.hint}", "90"))
    lines.append(_w("─" * 78, "90"))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for cand in [Path.cwd() / ".env", Path(__file__).resolve().parent.parent / ".env",
                 Path(__file__).resolve().parent.parent.parent / ".env"]:
        if cand.exists():
            load_dotenv(cand, override=False)
            return


def run(*, refresh_finder: bool = False, color: bool = True) -> int:
    _load_env()
    results = [
        check_providers(),
        check_opik(),
        check_finder(refresh=refresh_finder),
        check_neo4j(),
    ]
    print(render(results, color=color))
    if any(r.status == "FAIL" for r in results):
        return 2
    if any(r.status == "WARN" for r in results):
        return 1
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Teaching-resource environment preflight")
    ap.add_argument(
        "--refresh-finder",
        action="store_true",
        help="Force re-download of FinDER (overrides parquet cache)",
    )
    ap.add_argument("--no-color", action="store_true", help="Disable ANSI colors")
    args = ap.parse_args()
    sys.exit(run(refresh_finder=args.refresh_finder, color=not args.no_color))


if __name__ == "__main__":
    main()
