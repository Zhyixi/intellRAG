import pytest

from common.langfuse_tracing import (
    build_agent_run_config,
    build_langgraph_invoke_config,
    create_langfuse_handler,
    langfuse_configured,
    langfuse_environment,
    resolve_chain_observation_id,
    resolve_trace_id,
)
from langfuse.langchain import CallbackHandler
from langfuse.types import TraceContext


pytestmark = pytest.mark.unit


def test_langfuse_environment_is_non_empty():
    assert langfuse_environment() not in ("", "default")


@pytest.mark.skipif(not langfuse_configured(), reason="Langfuse credentials required")
def test_build_agent_run_config_uses_trace_context_handler():
    config = build_agent_run_config(
        session_id="session-1",
        retrice_service=object(),
        tb_repo=object(),
        llm=object(),
        translator=None,
        version="v3",
        syslang="ZH",
        intent="notebook",
        trace_name="notebook_chat",
        trace_id="abc123",
        domain="notebook",
        user_role="10038437",
    )

    handler = config["callbacks"][0]
    assert getattr(handler, "_trace_context") == {"trace_id": "abc123"}
    assert config["metadata"]["trace_id"] == "abc123"
    assert config["metadata"]["domain"] == "notebook"
    assert config["metadata"]["app_env"] == langfuse_environment()
    assert config["metadata"]["user_role"] == "10038437"
    assert config["configurable"]["session_id"] == "session-1"


@pytest.mark.skipif(not langfuse_configured(), reason="Langfuse credentials required")
def test_build_langgraph_invoke_config_binds_parent_span():
    config = build_langgraph_invoke_config(
        trace_id="trace-1",
        parent_span_id="parent-1",
        session_id="session-1",
        trace_name="summary_parallel_graph",
        configurable={"retrive_service": object()},
    )
    handler = config["callbacks"][0]
    assert getattr(handler, "_trace_context") == {
        "trace_id": "trace-1",
        "parent_span_id": "parent-1",
    }
    assert config["metadata"]["trace_id"] == "trace-1"
    assert config["metadata"]["langfuse_trace_name"] == "summary_parallel_graph"


@pytest.mark.skipif(not langfuse_configured(), reason="Langfuse credentials required")
def test_resolve_chain_observation_id_from_handler_runs():
    class _Obs:
        def __init__(self, name: str, oid: str):
            self.name = name
            self.id = oid

    handler = CallbackHandler(trace_context={"trace_id": "trace-1"})
    handler._runs = {"run-1": _Obs("execute_retrieve_node", "obs-123")}
    config = {"callbacks": [handler]}
    assert (
        resolve_chain_observation_id(config, node_name="execute_retrieve_node")
        == "obs-123"
    )


@pytest.mark.skipif(not langfuse_configured(), reason="Langfuse credentials required")
def test_resolve_trace_id_prefers_metadata():
    handler = create_langfuse_handler("from-handler")
    assert handler is not None
    config = {
        "metadata": {"trace_id": "from-metadata"},
        "callbacks": [handler],
        "run_id": "from-run",
    }
    assert resolve_trace_id(config) == "from-metadata"


def test_build_agent_run_config_without_langfuse_credentials():
    if langfuse_configured():
        pytest.skip("Langfuse credentials are configured")
    config = build_agent_run_config(
        session_id="session-1",
        retrice_service=object(),
        tb_repo=object(),
        llm=object(),
        translator=None,
        version="v3",
        syslang="ZH",
        intent="notebook",
        trace_name="notebook_chat",
    )
    assert config["callbacks"] == []
