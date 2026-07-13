from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_optional_memory_compose_has_health_volume_and_no_hardcoded_password() -> None:
    rendered = (ROOT / "docker-compose.memory.yml").read_text()
    compose = yaml.safe_load(rendered)
    service = compose["services"]["postgres-memory"]

    assert service["image"] == "postgres:18-alpine"
    assert "healthcheck" in service
    assert "seocho-postgres-memory-v18:/var/lib/postgresql" in service["volumes"]
    assert "POSTGRES_VOLUME_NAME:-seocho-postgres-memory-v18" in rendered
    assert "POSTGRES_PASSWORD:?" in rendered
    assert "password" not in service["environment"].get("POSTGRES_PASSWORD", "")


def test_makefile_exposes_complete_memory_lifecycle() -> None:
    makefile = (ROOT / "Makefile").read_text()
    for target in (
        "memory-up:",
        "memory-migrate:",
        "memory-status:",
        "memory-smoke:",
        "memory-logs:",
        "memory-down:",
    ):
        assert target in makefile


def test_memory_migration_uses_canonical_sdk_schema() -> None:
    script = (ROOT / "scripts/setup/agent-memory-postgres.py").read_text()
    assert "POSTGRES_MEMORY_SCHEMA_SQL" in script
    assert "PostgreSQLMemoryRepository" in script
