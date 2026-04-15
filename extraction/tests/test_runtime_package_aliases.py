import os
import sys


ROOT_DIR = os.path.join(os.path.dirname(__file__), "..", "..")
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)


def test_policy_alias_points_to_runtime_module() -> None:
    import policy
    import runtime.policy as runtime_policy

    assert policy is runtime_policy


def test_public_memory_alias_points_to_runtime_module() -> None:
    import public_memory_api
    import runtime.public_memory_api as runtime_public_memory_api

    assert public_memory_api is runtime_public_memory_api


def test_server_runtime_alias_points_to_runtime_module() -> None:
    import server_runtime
    import runtime.server_runtime as runtime_server_runtime

    assert server_runtime is runtime_server_runtime


def test_runtime_ingest_alias_points_to_runtime_module() -> None:
    import runtime_ingest
    import runtime.runtime_ingest as runtime_runtime_ingest

    assert runtime_ingest is runtime_runtime_ingest


def test_agent_readiness_alias_points_to_runtime_module() -> None:
    import agent_readiness
    import runtime.agent_readiness as runtime_agent_readiness

    assert agent_readiness is runtime_agent_readiness


def test_middleware_alias_points_to_runtime_module() -> None:
    import middleware
    import runtime.middleware as runtime_middleware

    assert middleware is runtime_middleware


def test_memory_service_alias_points_to_runtime_module() -> None:
    import memory_service
    import runtime.memory_service as runtime_memory_service

    assert memory_service is runtime_memory_service
