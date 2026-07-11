import ast
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "benchmarks" / "load_exchange_calibrated_postgres.py"


def test_loader_uses_real_postgres_and_checks_four_way_parity() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert "PostgreSQLMemoryRepository.connect" in source
    assert "applied == unique_events == revision_count == outbox_count" in source
    assert "ThreadPoolExecutor" in ast.dump(tree)


def test_loader_reports_duplicate_delivery_separately_from_lost_commit() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert '"duplicate_deliveries"' in source
    assert '"idempotent_replays"' in source
    assert '"lost_commits"' in source
