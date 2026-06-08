import pytest
from langchain_core.messages import HumanMessage

from services.notebook_graphs import (
    _is_obvious_chitchat,
    _retrieval_scores_high,
    route_after_coverage,
    route_after_intent,
    route_after_load,
    route_after_memory_check,
)


pytestmark = pytest.mark.unit


def _config(**kwargs):
    return {"configurable": kwargs}


def test_route_after_load_web_search_confirm():
    state = {"pending_web_search_query": "天氣如何"}
    assert route_after_load(state, _config(confirm_web_search=True)) == "web_search"


def test_route_after_load_chitchat_fast():
    state = {"messages": [HumanMessage(content="你好")]}
    assert route_after_load(state, _config()) == "chitchat_fast"


def test_route_after_load_memory_check():
    state = {
        "messages": [HumanMessage(content="我的專案進度如何")],
        "memory_context": "- 使用者專案 A 已完成第一階段",
    }
    assert route_after_load(state, _config()) == "memory_check"


def test_route_after_load_parallel():
    state = {"messages": [HumanMessage(content="我的 SOP 怎麼寫")]}
    assert route_after_load(state, _config()) == "parallel"


def test_route_after_memory_hit():
    state = {"memory_can_answer": True, "final_response": "專案 A 已完成第一階段"}
    assert route_after_memory_check(state, _config()) == "memory_hit"


def test_route_after_memory_parallel():
    state = {"memory_can_answer": False}
    assert route_after_memory_check(state, _config()) == "parallel"


def test_is_obvious_chitchat():
    assert _is_obvious_chitchat("你好！")
    assert not _is_obvious_chitchat("我的 SOP 怎麼寫")


def test_retrieval_scores_high():
    assert _retrieval_scores_high([{"Score": 0.8}, {"Score": 0.7}])
    assert not _retrieval_scores_high([{"Score": 0.8}, {"Score": 0.5}])


def test_route_after_intent_chitchat():
    state = {"query_intent": "chitchat"}
    assert route_after_intent(state, _config()) == "chitchat"


def test_route_after_intent_general_web():
    state = {"query_intent": "general_knowledge"}
    assert route_after_intent(state, _config()) == "general_web"


def test_route_after_intent_doc_with_results():
    state = {"query_intent": "doc_query", "retrieval_results": [{"Text": ["x"]}]}
    assert route_after_intent(state, _config()) == "has_docs"


def test_route_after_intent_doc_no_results_offers_web():
    state = {
        "query_intent": "doc_query",
        "retrieval_results": [],
        "messages": [HumanMessage(content="我的 SOP 怎麼寫")],
    }
    assert route_after_intent(state, _config()) == "offer_web"


def test_route_after_intent_needs_clarification():
    state = {
        "query_intent": "unclear",
        "needs_clarification": True,
        "clarification_question": "您想查詢哪份文件？",
    }
    assert route_after_intent(state, _config()) == "clarify"


def test_route_after_coverage_sufficient():
    assert route_after_coverage({"doc_sufficient": True}, _config()) == "sufficient"


def test_route_after_coverage_insufficient():
    assert route_after_coverage({"doc_sufficient": False}, _config()) == "insufficient"
