"""Notebook LangGraph: memory → RAG retrieve → generate / web search fallback."""
from __future__ import annotations

import json
import logging
import re
from typing import Annotated, Any, AsyncIterator, List

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.mongodb import MongoDBSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from common.langfuse_tracing import propagate_run_attributes
from common.web_search import web_search
from services.llm_factory import user_index_name

logger = logging.getLogger(__name__)

NODE_STATUS: dict[str, str] = {
    "load_memory": "載入長期記憶…",
    "retrieve_docs": "檢索個人文件…",
    "generate_from_docs": "根據文件生成回答…",
    "evaluate_coverage": "評估文件是否足夠回答…",
    "offer_web_search": "準備詢問是否上網搜尋…",
    "web_search": "上網搜尋中…",
    "generate_from_web": "根據搜尋結果生成回答…",
    "generate_fallback": "生成一般回答…",
    "suggest_followups": "生成建議追問…",
}

WEB_SEARCH_OFFER_NO_DOCS = (
    "我在您的文件中沒有找到與此問題相關的內容。\n\n"
    "是否要幫您上網搜尋最新資訊？請點擊下方「上網搜尋」按鈕，或回覆「好」/「搜尋」確認。"
)
WEB_SEARCH_OFFER_INSUFFICIENT = (
    "文件中的資料不足以完整回答您的問題。\n\n"
    "是否要幫您上網搜尋補充資訊？請點擊下方「上網搜尋」按鈕，或回覆「好」/「搜尋」確認。"
)

_CONFIRM_RE = re.compile(
    r"^(是|好|好的|可以|搜尋|搜索|上網|上網搜尋|上网|上网搜索|yes|ok|yep|search)\s*[.!？?]*$",
    re.I,
)


def _safe_add_messages(
    left: list | AnyMessage | None, right: list | AnyMessage | None
) -> list[AnyMessage]:
    if isinstance(left, list):
        left = [m for m in left if m is not None]
    if isinstance(right, list):
        right = [m for m in right if m is not None]
    return add_messages(left, right)


class NotebookState(TypedDict, total=False):
    messages: Annotated[List[AnyMessage], _safe_add_messages]
    session_id: str
    user_id: str
    user_query: str
    memory_context: str
    retrieval_results: list
    raw_context: str
    source: list
    doc_sufficient: bool
    offer_web_search: bool
    pending_web_search_query: str
    web_search_results: list
    web_search_used: bool
    final_response: str
    results: list
    suggested_questions: list


def _emit_status(node: str, *, tool: str | None = None, detail: str = "") -> None:
    try:
        writer = get_stream_writer()
        payload: dict[str, Any] = {
            "type": "status",
            "node": node,
            "message": NODE_STATUS.get(node, node),
        }
        if tool:
            payload["tool"] = tool
        if detail:
            payload["detail"] = detail
        writer(payload)
    except Exception:
        pass


def _emit_token(content: str) -> None:
    if not content:
        return
    try:
        get_stream_writer()({"type": "token", "content": content})
    except Exception:
        pass


def _last_user_message(state: NotebookState) -> str:
    history = state.get("messages") or []
    for msg in reversed(history):
        if isinstance(msg, HumanMessage):
            return str(msg.content or "").strip()
    return str(state.get("user_query") or "").strip()


def _is_web_search_confirm(text: str) -> bool:
    return bool(_CONFIRM_RE.match((text or "").strip()))


def _resolve_query(state: NotebookState, config: RunnableConfig) -> str:
    configurable = config.get("configurable") or {}
    if configurable.get("confirm_web_search"):
        return (
            configurable.get("web_search_query")
            or state.get("pending_web_search_query")
            or _last_user_message(state)
        )
    return _last_user_message(state)


def _format_notebook_results(results: list) -> list[dict]:
    out = []
    for row in results:
        texts = row.get("Text") or []
        snippet = texts[0][:500] if texts else ""
        related = row.get("relatedFile") or {}
        file_path = ""
        page = ""
        if related:
            first = next(iter(related.values()), {})
            files = [first.get(f"file_{i}") for i in range(3)]
            for f in files:
                if isinstance(f, dict) and f.get("path"):
                    file_path = f.get("path", "")
                    hits = f.get("hits") or []
                    if hits:
                        page = str(hits[0].get("page", ""))
                    break
        out.append(
            {
                "file_name": row.get("source_file_name") or row.get("ISSUE_ID", ""),
                "page": row.get("source_page_label") or page,
                "snippet": snippet.replace("passage: ", ""),
                "score": row.get("Score", 0),
                "file_path": file_path,
            }
        )
    return out


def _extract_sources(results: list) -> tuple[list[str], str]:
    detail_lines: list[str] = []
    source_names: list[str] = []
    for row in results:
        for t in row.get("Text", []):
            detail_lines.append(t.replace("passage: ", ""))
        fname = row.get("source_file_name") or row.get("ISSUE_ID", "")
        page = row.get("source_page_label", "")
        if fname and fname not in source_names:
            source_names.append(f"{fname} p.{page}" if page else fname)
    return source_names, "\n\n".join(detail_lines)


async def _stream_llm_text(llm, messages: list) -> str:
    parts: list[str] = []
    async for chunk in llm.astream(messages):
        token = chunk.content if isinstance(chunk.content, str) else ""
        if token:
            parts.append(token)
            _emit_token(token)
    return "".join(parts).strip()


async def _generate_suggested_questions(
    llm,
    *,
    user_question: str,
    answer: str,
    context_snippet: str = "",
) -> list[str]:
    from pydantic import BaseModel, Field

    class Followups(BaseModel):
        questions: list[str] = Field(
            description="用户可能想继续追问的 3 个简短问题（繁体中文）",
            min_length=1,
            max_length=3,
        )

    try:
        structured = llm.with_structured_output(Followups)
        parts = [f"用户问题：{user_question}", f"助手回答：{answer[:2000]}"]
        if context_snippet:
            parts.append(f"相关文档摘录：{context_snippet[:1000]}")
        parts.append("请输出 3 个具体、可点击的后续追问，不要重复原问题。")
        result = await structured.ainvoke([HumanMessage(content="\n\n".join(parts))])
        return [q.strip() for q in result.questions if q and q.strip()][:3]
    except Exception as exc:
        logger.warning("notebook suggested questions failed: %s", exc)
        return []


async def load_memory_node(state: NotebookState, config: RunnableConfig) -> dict:
    _emit_status("load_memory")
    memory_service = config["configurable"].get("memory_service")
    user_id = config["configurable"].get("user_id")
    memory_context = state.get("memory_context") or ""
    if memory_service and user_id and not memory_context:
        memory_context = memory_service.format_for_prompt(int(user_id))
    query = _resolve_query(state, config)
    updates: dict = {"memory_context": memory_context, "user_query": query}
    configurable = config.get("configurable") or {}
    if not configurable.get("confirm_web_search"):
        pending = state.get("pending_web_search_query")
        if pending and query != pending and not _is_web_search_confirm(query):
            updates["pending_web_search_query"] = ""
    return updates


async def retrieve_docs_node(state: NotebookState, config: RunnableConfig) -> dict:
    _emit_status("retrieve_docs", tool="retrieve")
    retrice_service = config["configurable"].get("retrice_service")
    user_id = config["configurable"].get("user_id")
    user_input = state.get("user_query") or _last_user_message(state)
    results: list = []

    if retrice_service and user_id and user_input:
        index_name = user_index_name(int(user_id))
        metadata_filter = {"user_id": str(user_id)}
        try:
            extraction = retrice_service._heuristic_query_extraction(str(user_input))
            results, _ = await retrice_service.retrieve(
                prompt=str(user_input),
                index_name=index_name,
                metadata_filter=metadata_filter,
                result_limit=5,
                query_extraction=extraction,
            )
        except Exception as exc:
            logger.warning("notebook retrieve failed: %s", exc)
            results = []

    detail = f"找到 {len(results)} 個文件片段" if results else "未找到相關文件片段"
    _emit_status("retrieve_docs", tool="retrieve", detail=detail)
    source, raw_context = _extract_sources(results) if results else ([], "")
    return {
        "retrieval_results": results,
        "results": _format_notebook_results(results),
        "source": source,
        "raw_context": raw_context,
        "offer_web_search": False,
    }


def route_after_retrieve(state: NotebookState, config: RunnableConfig) -> str:
    configurable = config.get("configurable") or {}
    user_msg = _last_user_message(state)
    pending = state.get("pending_web_search_query")

    if configurable.get("confirm_web_search") or (
        pending and _is_web_search_confirm(user_msg)
    ):
        return "web_search"

    if state.get("retrieval_results"):
        return "has_docs"

    if user_msg and not _is_web_search_confirm(user_msg):
        return "offer_web"

    return "fallback"


async def generate_from_docs_node(state: NotebookState, config: RunnableConfig) -> dict:
    _emit_status("generate_from_docs", tool="llm")
    llm = config["configurable"]["llm"]
    retrice_service = config["configurable"].get("retrice_service")
    memory_context = state.get("memory_context") or ""
    user_input = state.get("user_query") or ""
    raw_str = state.get("raw_context") or ""
    source = state.get("source") or []

    system = (
        "你是 IntelliAgnet 個人筆記本助手（類似 NotebookLM）。"
        "僅根據提供的文件片段回答，使用清晰繁體中文。"
        "若片段不足以回答，請明確說明缺少什麼資訊，不要編造。"
    )
    if memory_context:
        system += f"\n\n{memory_context}"

    messages = [
        SystemMessage(content=system),
        HumanMessage(content=f"文件片段：\n{raw_str}\n\n用户问题：{user_input}"),
    ]
    chat_response = await _stream_llm_text(llm, messages)
    if retrice_service and hasattr(retrice_service, "extract_deepseek_outputs"):
        chat_response = retrice_service.extract_deepseek_outputs(chat_response)
    if source:
        chat_response += "\n\n" + "\n".join(f"[{i}] {s}" for i, s in enumerate(source, 1))

    return {"final_response": chat_response, "doc_sufficient": True}


async def evaluate_coverage_node(state: NotebookState, config: RunnableConfig) -> dict:
    _emit_status("evaluate_coverage")
    llm = config["configurable"]["llm"]
    user_input = state.get("user_query") or ""
    answer = state.get("final_response") or ""
    raw_str = state.get("raw_context") or ""

    from pydantic import BaseModel, Field

    class Coverage(BaseModel):
        sufficient: bool = Field(description="文件片段是否足以回答用户问题")
        reason: str = Field(description="简短理由")

    try:
        structured = llm.with_structured_output(Coverage)
        prompt = (
            f"用户问题：{user_input}\n\n"
            f"文件摘录：{raw_str[:1500]}\n\n"
            f"助手回答：{answer[:1500]}\n\n"
            "请判断文件内容是否足以支撑该回答；若回答主要表示无法从文件得到答案，则 sufficient=false。"
        )
        result = await structured.ainvoke([HumanMessage(content=prompt)])
        return {"doc_sufficient": bool(result.sufficient)}
    except Exception as exc:
        logger.warning("coverage evaluation failed: %s", exc)
        insufficient_markers = ("不足以", "無法從文件", "没有找到", "缺少", "尚無", "未找到")
        sufficient = not any(m in answer for m in insufficient_markers)
        return {"doc_sufficient": sufficient}


def route_after_coverage(state: NotebookState, config: RunnableConfig) -> str:
    if state.get("doc_sufficient", True):
        return "sufficient"
    return "insufficient"


async def offer_web_search_node(state: NotebookState, config: RunnableConfig) -> dict:
    _emit_status("offer_web_search")
    user_input = state.get("user_query") or _last_user_message(state)
    has_docs = bool(state.get("retrieval_results"))
    message = WEB_SEARCH_OFFER_INSUFFICIENT if has_docs else WEB_SEARCH_OFFER_NO_DOCS
    _emit_token(message)
    return {
        "final_response": message,
        "offer_web_search": True,
        "pending_web_search_query": user_input,
        "suggested_questions": [],
        "messages": [AIMessage(content=message)],
    }


async def web_search_node(state: NotebookState, config: RunnableConfig) -> dict:
    _emit_status("web_search", tool="search_web")
    query = _resolve_query(state, config)
    results: list[dict] = []
    try:
        results = web_search(query, max_results=8)
    except Exception as exc:
        logger.warning("web_search failed: %s", exc)
        results = []

    detail = f"找到 {len(results)} 筆搜尋結果" if results else "搜尋無結果"
    _emit_status("web_search", tool="search_web", detail=detail)
    return {
        "web_search_results": results,
        "web_search_used": True,
        "pending_web_search_query": "",
        "offer_web_search": False,
    }


async def generate_from_web_node(state: NotebookState, config: RunnableConfig) -> dict:
    _emit_status("generate_from_web", tool="llm")
    llm = config["configurable"]["llm"]
    memory_context = state.get("memory_context") or ""
    user_input = state.get("user_query") or _resolve_query(state, config)
    search_results = state.get("web_search_results") or []

    if not search_results:
        message = "上網搜尋未找到相關結果，請稍後再試或換個關鍵字。"
        _emit_token(message)
        return {
            "final_response": message,
            "source": ["web_search"],
            "messages": [AIMessage(content=message)],
        }

    lines = []
    source_labels: list[str] = []
    for i, row in enumerate(search_results, 1):
        title = row.get("title") or f"結果 {i}"
        url = row.get("url") or ""
        snippet = row.get("snippet") or ""
        lines.append(f"[{i}] {title}\n{snippet}\n{url}")
        source_labels.append(title)

    system = (
        "你是 IntelliAgnet 個人筆記本助手。"
        "根據以下網路搜尋結果回答用户问题，使用清晰繁體中文。"
        "若搜尋結果仍不足，请说明。引用来源时在文末标注 [n]。"
    )
    if memory_context:
        system += f"\n\n{memory_context}"

    messages = [
        SystemMessage(content=system),
        HumanMessage(
            content=f"搜尋結果：\n\n" + "\n\n".join(lines) + f"\n\n用户问题：{user_input}"
        ),
    ]
    chat_response = await _stream_llm_text(llm, messages)
    if source_labels:
        chat_response += "\n\n" + "\n".join(
            f"[{i}] {s}" for i, s in enumerate(source_labels[:5], 1)
        )

    formatted = [
        {
            "file_name": row.get("title") or "",
            "page": "",
            "snippet": (row.get("snippet") or "")[:500],
            "score": 0,
            "file_path": row.get("url") or "",
        }
        for row in search_results
    ]
    return {
        "final_response": chat_response,
        "results": formatted,
        "source": source_labels,
        "messages": [AIMessage(content=chat_response)],
    }


async def generate_fallback_node(state: NotebookState, config: RunnableConfig) -> dict:
    _emit_status("generate_fallback", tool="llm")
    llm = config["configurable"]["llm"]
    memory_context = state.get("memory_context") or ""
    history = state.get("messages") or []

    system = (
        "你是 IntelliAgnet 助手（類似 NotebookLM）。"
        "用清晰繁體中文回答。若用户尚未上传文档，引导其到「文档」页上传。"
    )
    if memory_context:
        system += f"\n\n{memory_context}"

    messages: list = [SystemMessage(content=system)]
    for msg in history[-16:]:
        if isinstance(msg, HumanMessage):
            messages.append(HumanMessage(content=msg.content))
        elif isinstance(msg, AIMessage):
            messages.append(AIMessage(content=msg.content))

    chat_response = await _stream_llm_text(llm, messages)
    return {
        "final_response": chat_response,
        "results": [],
        "source": [],
        "messages": [AIMessage(content=chat_response)],
    }


async def suggest_followups_node(state: NotebookState, config: RunnableConfig) -> dict:
    _emit_status("suggest_followups")
    llm = config["configurable"]["llm"]
    user_input = state.get("user_query") or ""
    answer = state.get("final_response") or ""
    raw_str = state.get("raw_context") or ""
    if state.get("web_search_used"):
        raw_str = json.dumps(state.get("web_search_results") or [])[:1000]

    suggested = await _generate_suggested_questions(
        llm,
        user_question=user_input,
        answer=answer,
        context_snippet=raw_str,
    )
    return {
        "suggested_questions": suggested,
        "messages": [AIMessage(content=answer)],
        "offer_web_search": False,
        "pending_web_search_query": "",
    }


def create_notebook_graph(checkpointer: MongoDBSaver):
    graph = StateGraph(NotebookState)
    graph.add_node("load_memory", load_memory_node)
    graph.add_node("retrieve_docs", retrieve_docs_node)
    graph.add_node("generate_from_docs", generate_from_docs_node)
    graph.add_node("evaluate_coverage", evaluate_coverage_node)
    graph.add_node("offer_web_search", offer_web_search_node)
    graph.add_node("web_search", web_search_node)
    graph.add_node("generate_from_web", generate_from_web_node)
    graph.add_node("generate_fallback", generate_fallback_node)
    graph.add_node("suggest_followups", suggest_followups_node)

    graph.add_edge(START, "load_memory")
    graph.add_edge("load_memory", "retrieve_docs")
    graph.add_conditional_edges(
        "retrieve_docs",
        route_after_retrieve,
        {
            "web_search": "web_search",
            "has_docs": "generate_from_docs",
            "offer_web": "offer_web_search",
            "fallback": "generate_fallback",
        },
    )
    graph.add_edge("generate_from_docs", "evaluate_coverage")
    graph.add_conditional_edges(
        "evaluate_coverage",
        route_after_coverage,
        {
            "sufficient": "suggest_followups",
            "insufficient": "offer_web_search",
        },
    )
    graph.add_edge("web_search", "generate_from_web")
    graph.add_edge("generate_from_web", "suggest_followups")
    graph.add_edge("generate_fallback", "suggest_followups")
    graph.add_edge("offer_web_search", END)
    graph.add_edge("suggest_followups", END)
    return graph.compile(checkpointer=checkpointer)


async def process_notebook_chat(
    user_input: str,
    run_config: dict,
    checkpointer: MongoDBSaver,
) -> dict:
    agent = create_notebook_graph(checkpointer)
    metadata = run_config.get("metadata") or {}
    configurable = run_config.get("configurable") or {}
    user_id = metadata.get("user_id") or configurable.get("user_id")
    with propagate_run_attributes(
        session_id=metadata.get("session_id"),
        trace_name=metadata.get("langfuse_trace_name"),
        trace_id=metadata.get("trace_id"),
        environment=metadata.get("app_env"),
        domain=metadata.get("domain"),
        user_id=str(user_id) if user_id else None,
    ):
        final_state = await agent.ainvoke(
            {"messages": [HumanMessage(content=user_input)]},
            config=run_config,
        )
    return final_state


async def stream_notebook_chat(
    user_input: str,
    run_config: dict,
    checkpointer: MongoDBSaver,
) -> AsyncIterator[dict[str, Any]]:
    agent = create_notebook_graph(checkpointer)
    metadata = run_config.get("metadata") or {}
    configurable = run_config.get("configurable") or {}
    user_id = metadata.get("user_id") or configurable.get("user_id")

    with propagate_run_attributes(
        session_id=metadata.get("session_id"),
        trace_name=metadata.get("langfuse_trace_name"),
        trace_id=metadata.get("trace_id"),
        environment=metadata.get("app_env"),
        domain=metadata.get("domain"),
        user_id=str(user_id) if user_id else None,
    ):
        async for mode, chunk in agent.astream(
            {"messages": [HumanMessage(content=user_input)]},
            config=run_config,
            stream_mode=["updates", "custom"],
        ):
            if mode == "custom":
                yield chunk
            elif mode == "updates":
                for node_name in chunk:
                    yield {
                        "type": "node",
                        "node": node_name,
                        "message": NODE_STATUS.get(node_name, node_name),
                    }

        snapshot = await agent.aget_state(run_config)
        values = snapshot.values if snapshot else {}

    yield {
        "type": "done",
        "chat_response": values.get("final_response", ""),
        "results": values.get("results", []),
        "source": values.get("source", []),
        "suggested_questions": values.get("suggested_questions", []),
        "offer_web_search": bool(values.get("offer_web_search")),
        "pending_web_search_query": values.get("pending_web_search_query", ""),
    }
