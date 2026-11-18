"""Golden parity test for infer_rules_from_nodes.

The fixture ``tests/fixtures/rules_parity.json`` (shared with the Rust unit
test ``rules::tests::test_parity_fixture``) holds raw JSON inputs and the exact
string the pure-Python fallback returns for them. The Rust implementation must
produce the same bytes.

Run with ``python3 -m pytest`` or ``python3 test_parity.py``. If the native
extension is importable it is checked against the fixture as well.
``python3 test_parity.py --regen`` rewrites the expected outputs from the
fallback (the reference semantics) after editing the case inputs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURE = HERE.parent.parent / "tests" / "fixtures" / "rules_parity.json"
sys.path.insert(0, str(HERE.parent))

from seocho_core import _fallback  # noqa: E402


def _load():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _run(impl, case):
    return impl(
        case["input"],
        case.get("required_threshold", 0.98),
        case.get("enum_max_size", 20),
    )


def test_fallback_matches_fixture():
    cases = _load()["cases"]
    assert cases
    for case in cases:
        assert _run(_fallback.infer_rules_from_nodes, case) == case["expected"], case["name"]


def test_native_matches_fixture():
    try:
        from seocho_core import _native
    except ImportError:
        return  # extension not built; the Rust unit test covers it
    for case in _load()["cases"]:
        assert _run(_native.infer_rules_from_nodes, case) == case["expected"], case["name"]


def _regen():
    data = _load()
    for case in data["cases"]:
        case["expected"] = _run(_fallback.infer_rules_from_nodes, case)
    FIXTURE.write_text(json.dumps(data, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    if "--regen" in sys.argv[1:]:
        _regen()
    else:
        test_fallback_matches_fixture()
        test_native_matches_fixture()
        print("ok")
