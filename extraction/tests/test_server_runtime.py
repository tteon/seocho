import os
import sys


ROOT_DIR = os.path.join(os.path.dirname(__file__), "..", "..")
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)


from server_runtime import (
    ServerContext,
    get_agent_factory_service,
    get_backend_specialist_agent_service,
    get_db_manager_service,
    get_frontend_specialist_agent_service,
    get_neo4j_connector_service,
    get_platform_session_store_service,
)


def test_server_context_tracks_trace_and_tool_budget() -> None:
    context = ServerContext(user_id="alex", workspace_id="default", tool_budget=2)

    assert context.consume_tool_budget() is True
    assert context.consume_tool_budget() is True
    assert context.consume_tool_budget() is False

    context.log_activity("router")
    context.log_activity("router")
    context.log_activity("semantic")
    assert context.trace_path == ["router", "semantic"]


def test_runtime_service_getters_return_singletons() -> None:
    assert get_neo4j_connector_service() is get_neo4j_connector_service()
    assert get_db_manager_service() is get_db_manager_service()
    assert get_agent_factory_service() is get_agent_factory_service()
    assert get_platform_session_store_service() is get_platform_session_store_service()
    assert get_backend_specialist_agent_service() is get_backend_specialist_agent_service()
    assert get_frontend_specialist_agent_service() is get_frontend_specialist_agent_service()
