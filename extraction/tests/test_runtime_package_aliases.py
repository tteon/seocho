import os
import subprocess
import sys


ROOT_DIR = os.path.join(os.path.dirname(__file__), "..", "..")
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)


def test_policy_alias_points_to_runtime_module() -> None:
    from .. import policy
    import runtime.policy as runtime_policy

    assert policy is runtime_policy


def test_public_memory_alias_points_to_runtime_module() -> None:
    from .. import public_memory_api
    import runtime.public_memory_api as runtime_public_memory_api

    assert public_memory_api is runtime_public_memory_api


def test_server_runtime_alias_points_to_runtime_module() -> None:
    from .. import server_runtime
    import runtime.server_runtime as runtime_server_runtime

    assert server_runtime is runtime_server_runtime


def test_runtime_ingest_alias_points_to_runtime_module() -> None:
    from .. import runtime_ingest
    import runtime.runtime_ingest as runtime_runtime_ingest

    assert runtime_ingest is runtime_runtime_ingest


def test_agent_readiness_alias_points_to_runtime_module() -> None:
    from .. import agent_readiness
    import runtime.agent_readiness as runtime_agent_readiness

    assert agent_readiness is runtime_agent_readiness


def test_middleware_alias_points_to_runtime_module() -> None:
    from .. import middleware
    import runtime.middleware as runtime_middleware

    assert middleware is runtime_middleware


def test_memory_service_alias_points_to_runtime_module() -> None:
    from .. import memory_service
    import runtime.memory_service as runtime_memory_service

    assert memory_service is runtime_memory_service


def test_extraction_modules_do_not_resolve_under_a_bare_flat_name() -> None:
    """The replacement for the old flat-alias guarantee, inverted on purpose.

    This test used to assert that `import policy`, run with cwd=extraction/,
    was the same object as `runtime.policy`. That alias was a symptom, not a
    feature: `runtime/__init__.py` put extraction/ on sys.path, so every module
    here answered to two names and Python cached each spelling separately.
    `extraction/config.py` therefore loaded twice, yielding two
    `DatabaseRegistry` classes and two `db_registry` singletons (seocho-60u).

    extraction/ is a package now, so the bare name must not resolve at all.
    Run in a subprocess from the repository root because sys.modules in this
    process is already populated.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import runtime\n"
            "import extraction.config as pkg\n"
            "try:\n"
            "    import config\n"
            "except ModuleNotFoundError:\n"
            "    pass\n"
            "else:\n"
            "    raise AssertionError('flat name still resolves: %s' % config.__file__)\n",
        ],
        cwd=os.path.abspath(ROOT_DIR),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_importing_a_module_twice_yields_one_object() -> None:
    """The defect itself, pinned. Two spellings must never mean two modules."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import extraction.config as a\n"
            "import extraction.config as b\n"
            "assert a is b\n"
            "assert a.db_registry is b.db_registry\n"
            "assert a.DatabaseRegistry is b.DatabaseRegistry\n",
        ],
        cwd=os.path.abspath(ROOT_DIR),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
