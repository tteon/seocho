"""Project scaffolds for first-run SEOCHO CLI workflows."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping


@dataclass(slots=True)
class ScaffoldResult:
    """Files created by ``seocho new``."""

    path: Path
    sample: str
    files: List[Path]


COMPANY_SCHEMA = """\
name: hello_company
description: Tiny company ontology for the SEOCHO first run.
nodes:
  Company:
    description: A business organization.
    properties:
      name:
        type: STRING
        constraint: UNIQUE
      headquarters:
        type: STRING
  Person:
    description: A person such as an executive or founder.
    properties:
      name:
        type: STRING
        constraint: UNIQUE
      role:
        type: STRING
  Product:
    description: A product or service offered by a company.
    properties:
      name:
        type: STRING
        constraint: UNIQUE
  Sector:
    description: A market sector.
    properties:
      name:
        type: STRING
        constraint: UNIQUE
relationships:
  CEO_OF:
    source: Person
    target: Company
    description: Person leads the company as chief executive.
  FOUNDED:
    source: Person
    target: Company
    description: Person founded the company.
  OFFERS:
    source: Company
    target: Product
    description: Company offers a product or service.
  ACQUIRED:
    source: Company
    target: Company
    description: Company acquired another company.
  OPERATES_IN:
    source: Company
    target: Sector
    description: Company participates in a sector.
"""


COMPANY_RUN_SPEC = """\
name: hello-company
description: "First SEOCHO run: index sample company notes and ask evidence-backed questions."

ontology:
  path: ./schema.yaml
  enforcement: guided

documents:
  path: ./docs/
  recursive: true

models:
  default: mara/MiniMax-M2.5

agent:
  execution_mode: pipeline
  routing_policy: balanced

query:
  reasoning_mode: true
  repair_budget: 1
  answer_style: evidence
  limit: 5

questions:
  - question: Who is the CEO of Acme Corp?
    expect: Jane Park
  - question: What product does Acme Corp offer?
    expect: Atlas Platform
  - question: Which company did Acme Corp acquire?
    expect: Beta Analytics
"""


COMPANY_README = """\
# SEOCHO Hello Company

This directory is a runnable SEOCHO sample project. It has:

- `schema.yaml`: the ontology that defines allowed graph facts
- `docs/`: small source documents to index
- `seocho.run.yaml`: the end-to-end run spec

## Run

```bash
export MARA_API_KEY=...
seocho run --dry-run
seocho run
```

From a repository checkout, use `uv run seocho ...` instead of `seocho ...`.

The run uses the embedded LadybugDB graph store by default, so no graph server
is required. The generated report lands under `runs/`.

## Try Edits

Change a sentence in `docs/`, add a relationship to `schema.yaml`, or add a
question to `seocho.run.yaml`, then rerun:

```bash
seocho run --force
```
"""


COMPANY_DOC_ACME = """\
# Acme Corp

Acme Corp is a software company headquartered in Seoul. Jane Park is the CEO of
Acme Corp. The company offers Atlas Platform, a workflow automation product for
operations teams.

In 2025, Acme Corp acquired Beta Analytics to strengthen its analytics product
line.
"""


COMPANY_DOC_BETA = """\
# Beta Analytics

Beta Analytics is a data analytics company. Olivia Chen founded Beta Analytics
in 2021. The company operates in the analytics sector.
"""


COMPANY_DOC_MARKET = """\
# Market Notes

Acme Corp operates in the workflow automation sector. Beta Analytics operates
in the analytics sector. Atlas Platform is positioned for operations teams that
need graph-backed process intelligence.
"""


_SAMPLES: Mapping[str, Dict[str, str]] = {
    "company": {
        "schema.yaml": COMPANY_SCHEMA,
        "seocho.run.yaml": COMPANY_RUN_SPEC,
        "README.md": COMPANY_README,
        "docs/acme.md": COMPANY_DOC_ACME,
        "docs/beta.md": COMPANY_DOC_BETA,
        "docs/market.md": COMPANY_DOC_MARKET,
    }
}


def create_sample_project(
    target: "str | Path",
    *,
    sample: str = "company",
    force: bool = False,
) -> ScaffoldResult:
    """Create a runnable sample project for ``seocho run``.

    Existing non-empty directories are refused unless ``force`` is set. With
    ``force``, only scaffold-owned files are overwritten; unrelated files are
    left alone.
    """

    if sample not in _SAMPLES:
        available = ", ".join(sorted(_SAMPLES))
        raise ValueError(f"Unknown sample {sample!r}. Available samples: {available}.")

    root = Path(target)
    if root.exists() and not root.is_dir():
        raise FileExistsError(f"{root} exists and is not a directory.")
    if root.exists() and not force:
        existing = [item for item in root.iterdir() if item.name != ".DS_Store"]
        if existing:
            raise FileExistsError(f"{root} is not empty. Choose an empty directory or pass --force.")

    root.mkdir(parents=True, exist_ok=True)
    created: List[Path] = []
    for relative, content in _SAMPLES[sample].items():
        destination = root / relative
        if destination.exists() and not force:
            raise FileExistsError(f"{destination} already exists. Pass --force to overwrite scaffold files.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
        created.append(destination)

    return ScaffoldResult(path=root, sample=sample, files=created)


__all__ = ["ScaffoldResult", "create_sample_project"]
