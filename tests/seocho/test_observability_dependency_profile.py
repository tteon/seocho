from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
OBS = ROOT / "examples" / "observability"


def test_dependency_profile_pins_exporter_and_uses_secret_file() -> None:
    payload = yaml.safe_load(
        (OBS / "docker-compose.dependencies.yml").read_text(encoding="utf-8")
    )
    exporter = payload["services"]["postgres-exporter"]
    assert exporter["image"].endswith(":v0.19.1")
    assert exporter["environment"]["DATA_SOURCE_PASS_FILE"].startswith(
        "/run/secrets/"
    )
    assert "DATA_SOURCE_PASS" not in exporter["environment"]
    assert exporter["profiles"] == ["dependencies"]


def test_dependency_scrapes_are_only_in_overlay_config() -> None:
    core = (OBS / "prometheus.yml").read_text(encoding="utf-8")
    dependency = (OBS / "prometheus.dependencies.yml").read_text(encoding="utf-8")
    assert "seocho-postgresql" not in core
    assert "seocho-etcd" not in core
    assert "postgres-exporter:9187" in dependency
    assert "host.docker.internal:52379" in dependency
    assert "DozerDB Community" in dependency
