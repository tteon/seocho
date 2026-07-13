"""End-to-end finance-compliance usecase: load ontology, ingest mock filings,
ask questions that cross entity boundaries.

Run from the repository root:

    MARA_API_KEY=... python examples/finance-compliance/quickstart.py

Swap to another provider with --llm (e.g. openai/gpt-4o).
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

PROVIDER_KEY_HINTS = {
    "mara": "MARA_API_KEY",
    "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "kimi": "MOONSHOT_API_KEY",
    "grok": "XAI_API_KEY",
    "qwen": "DASHSCOPE_API_KEY",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--llm", default="mara/MiniMax-M2.5", help="Provider/model string.")
    parser.add_argument(
        "--skip-query",
        action="store_true",
        help="Only ingest; skip the natural-language Q&A pass.",
    )
    args = parser.parse_args()

    provider = args.llm.split("/", 1)[0].lower()
    key_name = PROVIDER_KEY_HINTS.get(provider)
    if key_name and not os.getenv(key_name):
        print(
            f"{key_name} not set. Export it or pass --llm for a provider "
            "whose key is already configured.",
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
