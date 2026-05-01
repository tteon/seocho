"""End-to-end finance-compliance usecase: load ontology, ingest mock filings,
ask questions that cross entity boundaries.

Run from the repository root:

    OPENAI_API_KEY=... python examples/finance-compliance/quickstart.py

Swap to another provider with --llm (e.g. deepseek/deepseek-chat).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Make `ontology.py` importable when running this file from any cwd.
sys.path.insert(0, str(Path(__file__).parent))

from ontology import build_ontology  # noqa: E402

from seocho import Seocho  # noqa: E402


QUESTIONS = [
    "Which regulations is Acme Financial Services subject to, and who enforces them?",
    "What incidents have been reported, and which regulations do they relate to?",
    "Which control evidence mitigates incident I-2026-007?",
    "Which policies govern trade surveillance at Acme Financial Services?",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--llm", default="openai/gpt-4o", help="Provider/model string.")
    parser.add_argument(
        "--skip-query",
        action="store_true",
        help="Only ingest; skip the natural-language Q&A pass.",
    )
    args = parser.parse_args()

    if args.llm.startswith("openai/") and not os.getenv("OPENAI_API_KEY"):
        print(
            "OPENAI_API_KEY not set. Export the key or pass "
            "--llm deepseek/deepseek-chat (requires DEEPSEEK_API_KEY) etc.",
            file=sys.stderr,
        )
        return 2

    docs_dir = Path(__file__).parent / "sample_docs"
    doc_paths = sorted(docs_dir.glob("*.txt"))
    if not doc_paths:
        print(f"No sample docs found under {docs_dir}", file=sys.stderr)
        return 2

    onto = build_ontology()
    s = Seocho.local(onto, llm=args.llm)

    for path in doc_paths:
        print(f"ingesting {path.name}")
        s.add(path.read_text())

    if args.skip_query:
        print("\ningest complete (--skip-query set)")
        return 0

    print()
    for question in QUESTIONS:
        print(f"Q: {question}")
        answer = s.ask(question)
        print(f"A: {answer}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
