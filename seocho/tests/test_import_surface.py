from __future__ import annotations

import json
import subprocess
import sys


def test_import_seocho_stays_lazy_for_optional_heavy_modules() -> None:
    script = """
import json
import sys

import seocho

payload = {
    "after_import": {
        "seocho.http_runtime": "seocho.http_runtime" in sys.modules,
        "seocho.store.graph": "seocho.store.graph" in sys.modules,
        "seocho.store.llm": "seocho.store.llm" in sys.modules,
        "seocho.runtime_bundle": "seocho.runtime_bundle" in sys.modules,
    },
}

_ = seocho.Seocho

payload["after_accessing_seocho_class"] = {
    "seocho.http_runtime": "seocho.http_runtime" in sys.modules,
    "seocho.store.graph": "seocho.store.graph" in sys.modules,
    "seocho.store.llm": "seocho.store.llm" in sys.modules,
    "seocho.runtime_bundle": "seocho.runtime_bundle" in sys.modules,
}

print(json.dumps(payload))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["after_import"]["seocho.http_runtime"] is False
    assert payload["after_import"]["seocho.store.graph"] is False
    assert payload["after_import"]["seocho.store.llm"] is False
    assert payload["after_import"]["seocho.runtime_bundle"] is False

    assert payload["after_accessing_seocho_class"]["seocho.http_runtime"] is False
    assert payload["after_accessing_seocho_class"]["seocho.store.graph"] is False
    assert payload["after_accessing_seocho_class"]["seocho.store.llm"] is False
    assert payload["after_accessing_seocho_class"]["seocho.runtime_bundle"] is False
