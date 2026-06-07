import pandas as pd
import pytest

from services import ae_graphs
from services.rag_service import RetriveService
from common.langfuse_tracing import build_agent_run_config
from fast_api_service.api.ae.iap_ae import normalize_session_id


pytestmark = pytest.mark.unit


class FakeTableRepo:
    def __init__(self):
        self.calls = []

    def execute_raw_sql(self, sql_query, params=None):
        self.calls.append((sql_query, params))
        return pd.DataFrame(
            [
                {
                    "brand_zh": "匯川",
                    "brand_en": "Inovance",
                    "model": "SV630A",
                    "errorcode": "E150.3",
                }
            ]
        )


def test_route_ae_sop_incomplete_uses_mixed_retrieve():
    route = ae_graphs.route_after_completeness(
        {"intent": "ae_sop", "sop_retrieve_flag": False},
        {},
    )

    assert route == "execute_retrieve_node"


@pytest.mark.asyncio
async def test_check_completeness_requires_brand_model_and_code():
    repo = FakeTableRepo()
    result = await ae_graphs.check_completeness_node(
        {
            "intent": "ae_sop",
            "brand_zh": "匯川",
            "errorcode": "E150.3",
        },
        {"configurable": {"tb_repo": repo}},
    )

    assert result["sop_retrieve_flag"] is False
    assert "請指定機型" in result["missing_info"]
    sql, params = repo.calls[0]
    assert ":errorcode" in sql
    assert params == {"errorcode": "E150.3", "brand": "匯川"}


@pytest.mark.asyncio
async def test_check_completeness_complete_sop_can_retrieve():
    repo = FakeTableRepo()
    result = await ae_graphs.check_completeness_node(
        {
            "intent": "ae_sop",
            "brand_zh": "匯川",
            "model": "SV630A",
            "errorcode": "E150.3",
        },
        {"configurable": {"tb_repo": repo}},
    )

    assert result["sop_retrieve_flag"] is True


def test_graph_cache_reuses_compiled_app_for_same_client(monkeypatch):
    compile_count = {"value": 0}

    class FakeWorkflow:
        def __init__(self, state_type):
            self.state_type = state_type

        def add_node(self, *args, **kwargs):
            return None

        def add_edge(self, *args, **kwargs):
            return None

        def add_conditional_edges(self, *args, **kwargs):
            return None

        def compile(self, checkpointer):
            compile_count["value"] += 1
            return object()

    class FakeCheckpointer:
        def __init__(self, client):
            self.client = client

    ae_graphs._GRAPH_CACHE.clear()
    monkeypatch.setattr(ae_graphs, "StateGraph", FakeWorkflow)
    shared_client = object()

    first = ae_graphs.create_graph(FakeCheckpointer(shared_client))
    second = ae_graphs.create_graph(FakeCheckpointer(shared_client))

    assert first is second
    assert compile_count["value"] == 1


def test_build_agent_run_config_contains_session_id_in_configurable():
    session_id = normalize_session_id(None)
    config = build_agent_run_config(
        session_id=session_id,
        retrice_service=object(),
        tb_repo=object(),
        llm=object(),
        translator=None,
        version="v3",
        syslang="ZH",
        intent="",
        trace_name="iap_ae_retrieve_v3",
        run_id="run-id",
        trace_id="trace-id",
        domain="iap_ae",
    )

    assert config["configurable"]["thread_id"] == session_id
    assert config["configurable"]["session_id"] == session_id
    assert config["configurable"]["syslang"] == "zh"
    assert config["metadata"]["trace_id"] == "trace-id"
    assert config["metadata"]["domain"] == "iap_ae"
    assert config["callbacks"]


def test_merge_es_filters_combines_metadata_and_keyword_id_filter():
    service = RetriveService.__new__(RetriveService)

    merged = service._merge_es_filters(
        {"term": {"metadata.faca_relevant": True}},
        service._build_id_filter(["doc-1", "doc-2", "doc-1"]),
    )

    assert merged == {
        "bool": {
            "filter": [
                {"term": {"metadata.faca_relevant": True}},
                {"terms": {"metadata.id": ["doc-1", "doc-2"]}},
            ]
        }
    }


def test_synthetic_issue_id_prefix_by_source():
    service = RetriveService.__new__(RetriveService)

    assert service._issue_id_prefix_for_index("ae_sop_file_zh") == RetriveService.ISSUE_ID_PREFIX_SOP
    assert service._issue_id_prefix_for_index("file_zh") == RetriveService.ISSUE_ID_PREFIX_OTHER

    sop_id = service._build_dummy_issue_id(
        type("Node", (), {"metadata": {"file_name": "manual.pdf"}, "page_content": "x"})(),
        prefix=RetriveService.ISSUE_ID_PREFIX_SOP,
    )
    other_id = service._build_dummy_issue_id(
        type("Node", (), {"metadata": {"file_name": "manual.pdf"}, "page_content": "x"})(),
        prefix=RetriveService.ISSUE_ID_PREFIX_OTHER,
    )
    assert sop_id.startswith(RetriveService.ISSUE_ID_PREFIX_SOP)
    assert len(sop_id) == len(RetriveService.ISSUE_ID_PREFIX_SOP) + RetriveService.ISSUE_ID_SUFFIX_LEN
    assert other_id.startswith(RetriveService.ISSUE_ID_PREFIX_OTHER)
    assert len(other_id) == len(RetriveService.ISSUE_ID_PREFIX_OTHER) + RetriveService.ISSUE_ID_SUFFIX_LEN
    assert RetriveService.is_synthetic_issue_id(sop_id)
    assert RetriveService.is_synthetic_issue_id(other_id)
    assert not RetriveService.is_synthetic_issue_id("SFCA60122028C")
