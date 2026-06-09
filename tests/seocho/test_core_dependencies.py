from __future__ import annotations

import re
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - py310 fallback
    import tomli as tomllib  # type: ignore[import-not-found]


_REPO_ROOT = Path(__file__).resolve().parents[2]


def _core_dependency_names() -> set[str]:
    parsed = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    names: set[str] = set()
    for spec in parsed["project"]["dependencies"]:
        # strip version/extra markers: "pyyaml>=6 ; python_version<'3.0'" -> "pyyaml"
        name = re.split(r"[<>=!~;\[ ]", spec, maxsplit=1)[0]
        names.add(name.strip().lower())
    return names


def test_pyyaml_is_a_core_dependency() -> None:
    # seocho/ontology.py imports yaml at module top level, and the core public
    # API (Ontology, which store/graph.py re-imports) loads it eagerly, so a
    # plain `pip install seocho` must pull PyYAML in. Keeping it only in the
    # local/ci/dev extras reintroduces `ModuleNotFoundError: No module named
    # 'yaml'` for `from seocho import Ontology` (issue #137).
    assert "pyyaml" in _core_dependency_names()


def test_ontology_imports_yaml_eagerly() -> None:
    # Pins the assumption behind the test above: if yaml ever becomes a lazy
    # import this guard can be relaxed, but today it is module-level.
    source = (_REPO_ROOT / "src" / "seocho" / "ontology.py").read_text(encoding="utf-8")
    assert re.search(r"(?m)^import yaml\b", source)
