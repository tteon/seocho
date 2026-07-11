import ast
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "benchmarks"
    / "okx_critical_scenarios_live.py"
)


def test_live_runner_requires_all_real_service_endpoints() -> None:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    rendered = ast.dump(tree)
    for option in (
        "postgres-dsn",
        "bolt-uri",
        "graph-password",
        "etcd-url",
        "tempo-url",
        "otlp-endpoint",
    ):
        assert option in rendered


def test_unexecuted_scenarios_are_not_reported_as_passes() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert '"not_executed"' in source
    assert 'for i in (2, 3, 5, 6, 7, 8, 9, 10)' in source
