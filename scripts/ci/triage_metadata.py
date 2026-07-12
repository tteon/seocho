#!/usr/bin/env python3
"""Infer public GitHub labels for issue and PR triage events."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Iterable


DEFAULT_LABELS_PATH = Path(".github/labels.json")
ALWAYS_ALLOWED = {"bug", "documentation", "enhancement", "good first issue"}

TITLE_KIND_RULES = {
    "bug": "kind-bug",
    "fix": "kind-bug",
    "feat": "kind-feature",
    "feature": "kind-feature",
    "docs": "kind-docs",
    "doc": "kind-docs",
    "test": "kind-test",
    "tests": "kind-test",
    "ci": "kind-ci",
    "build": "kind-ci",
    "refactor": "kind-refactor",
    "perf": "kind-perf",
    "performance": "kind-perf",
    "chore": "kind-maintenance",
    "maint": "kind-maintenance",
    "release": "kind-release",
}

AREA_VALUE_RULES = {
    "sdk": "area-sdk",
    "runtime": "area-runtime",
    "query": "area-query",
    "indexing": "area-indexing",
    "ontology": "area-ontology",
    "docs": "area-docs",
    "documentation": "area-docs",
    "readme": "area-docs",
    "quickstart": "area-docs",
    "contributor docs": "area-docs",
    "examples": "area-examples",
    "tutorials": "area-examples",
    "ci": "area-ci",
    "connector": "area-connector",
    "observability": "area-observability",
    "website": "area-website",
    "security": "area-security",
    "community": "area-community",
    "discord": "area-community",
}

FILE_AREA_RULES = [
    ("src/seocho/client", "area-sdk"),
    ("src/seocho/session", "area-sdk"),
    ("src/seocho/models", "area-sdk"),
    ("src/seocho/http_transport", "area-sdk"),
    ("runtime/", "area-runtime"),
    ("extraction/", "area-runtime"),
    ("src/seocho/query/", "area-query"),
    ("src/seocho/routing/", "area-query"),
    ("src/seocho/index/", "area-indexing"),
    ("src/seocho/rules.py", "area-indexing"),
    ("src/seocho/graph_projector.py", "area-indexing"),
    ("src/seocho/ontology", "area-ontology"),
    ("src/seocho/fibo/", "area-ontology"),
    ("docs/ontology/", "area-ontology"),
    ("docs/", "area-docs"),
    ("README.md", "area-docs"),
    ("QUICKSTART.md", "area-docs"),
    ("CONTRIBUTING.md", "area-docs"),
    ("examples/", "area-examples"),
    (".github/", "area-ci"),
    ("scripts/ci/", "area-ci"),
    ("website/", "area-website"),
    ("SECURITY.md", "area-security"),
]

FILE_KIND_RULES = [
    (".github/", "kind-ci"),
    ("scripts/ci/", "kind-ci"),
    ("tests/", "kind-test"),
    ("extraction/tests/", "kind-test"),
    ("docs/", "kind-docs"),
    ("README.md", "kind-docs"),
    ("QUICKSTART.md", "kind-docs"),
]


def load_allowed_labels(path: Path = DEFAULT_LABELS_PATH) -> set[str]:
    if not path.exists():
        return set(ALWAYS_ALLOWED)
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {str(item["name"]) for item in raw} | set(ALWAYS_ALLOWED)


def normalize_text(value: object) -> str:
    return str(value or "").strip()


def first_title_token(title: str) -> str:
    match = re.match(r"^\s*([a-zA-Z][a-zA-Z0-9_-]*)(?:\([^)]*\))?\s*[:\]]", title)
    return match.group(1).lower() if match else ""


def labels_from_title(title: str) -> set[str]:
    token = first_title_token(title)
    labels = {TITLE_KIND_RULES[token]} if token in TITLE_KIND_RULES else set()
    lowered = title.lower()
    if "security" in lowered or "vulnerability" in lowered or "secret" in lowered:
        labels.add("area-security")
    return labels


def parse_issue_form(body: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []
    for line in body.splitlines():
        if line.startswith("### "):
            if current:
                fields[current] = "\n".join(buffer).strip()
            current = line.removeprefix("### ").strip().lower()
            buffer = []
        elif current:
            buffer.append(line)
    if current:
        fields[current] = "\n".join(buffer).strip()
    return fields


def first_nonempty_line(value: str) -> str:
    for line in value.splitlines():
        cleaned = line.strip()
        if cleaned and cleaned != "_No response_":
            return cleaned
    return ""


def area_label(value: str) -> str | None:
    key = value.strip().lower()
    return AREA_VALUE_RULES.get(key)


def labels_from_issue_form(body: str) -> set[str]:
    fields = parse_issue_form(body)
    labels: set[str] = set()
    for field_name in ("area", "surface"):
        value = first_nonempty_line(fields.get(field_name, ""))
        label = area_label(value)
        if label:
            labels.add(label)
    contribution = first_nonempty_line(fields.get("contribution size", "")).lower()
    if contribution == "good first issue":
        labels.add("good first issue")
    if contribution in {"design discussion first", "architecture decision required"}:
        labels.add("status-needs-design")
    reproduction = first_nonempty_line(fields.get("reproduction", ""))
    if "reproduction" in fields and not reproduction:
        labels.add("status-needs-repro")
    return labels


def labels_from_files(files: Iterable[str]) -> set[str]:
    labels: set[str] = set()
    for filename in files:
        normalized = filename.strip()
        if not normalized:
            continue
        for prefix, label in FILE_AREA_RULES:
            if normalized == prefix or normalized.startswith(prefix):
                labels.add(label)
        for prefix, label in FILE_KIND_RULES:
            if normalized == prefix or normalized.startswith(prefix):
                labels.add(label)
    return labels


def infer_labels(event: dict[str, object], changed_files: Iterable[str]) -> list[str]:
    issue = event.get("issue")
    pull_request = event.get("pull_request")
    labels: set[str] = {"status-needs-triage"}

    if isinstance(pull_request, dict):
        title = normalize_text(pull_request.get("title"))
        labels.update(labels_from_title(title))
        labels.update(labels_from_files(changed_files))
    elif isinstance(issue, dict):
        title = normalize_text(issue.get("title"))
        body = normalize_text(issue.get("body"))
        labels.update(labels_from_title(title))
        labels.update(labels_from_issue_form(body))
    else:
        labels.clear()

    if not any(label.startswith("kind-") or label in {"bug", "documentation", "enhancement"} for label in labels):
        labels.add("kind-maintenance")
    return sorted(labels)


def read_changed_files(path: Path | None) -> list[str]:
    if path is None or not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]


def format_labels(labels: list[str], output_format: str) -> str:
    if output_format == "json":
        return json.dumps(labels)
    if output_format == "csv":
        return ",".join(labels)
    return "\n".join(labels)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", required=True, type=Path)
    parser.add_argument("--files", type=Path)
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS_PATH)
    parser.add_argument("--format", choices=("lines", "csv", "json"), default="lines")
    args = parser.parse_args(argv)

    event = json.loads(args.event.read_text(encoding="utf-8"))
    allowed = load_allowed_labels(args.labels)
    inferred = infer_labels(event, read_changed_files(args.files))
    labels = [label for label in inferred if label in allowed]
    print(format_labels(labels, args.format))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
