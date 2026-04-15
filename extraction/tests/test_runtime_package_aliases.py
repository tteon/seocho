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
