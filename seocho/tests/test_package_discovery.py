from __future__ import annotations

from pathlib import Path
import tomllib

from setuptools import find_packages


def test_root_package_discovery_stays_within_publishable_sdk_namespace() -> None:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    parsed = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    setuptools_config = parsed["tool"]["setuptools"]
    config = setuptools_config["packages"]["find"]

    assert setuptools_config["include-package-data"] is False
    assert config["include"] == ["seocho", "seocho.*"]
    assert config["exclude"] == ["seocho.tests", "seocho.tests.*"]
    assert config["namespaces"] is False

    packages = find_packages(
        where=str(pyproject.parent),
        include=config["include"],
        exclude=config["exclude"],
    )

    assert "seocho" in packages
    assert "seocho.tests" not in packages
    assert not any(pkg.startswith("seocho_core") for pkg in packages)
