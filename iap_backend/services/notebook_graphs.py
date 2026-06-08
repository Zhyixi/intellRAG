"""Notebook LangGraph: memory → intent classify ∥ retrieve → route → generate / web search."""
from __future__ import annotations

import json
import logging
import re
from contextlib import nullcontext
from typing import Annotated, Any, AsyncIterator, List, Literal

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.mongodb import MongoDBSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from common.langfuse_tracing import (
    compile_langfuse_prompt_or_fallback,
    langfuse_configured,
    observe_tool_span,
    propagate_run_attributes,
)
from common.web_search import mcp_search_web
from services.llm_factory import user_index_name

logger = logging.getLogger(__name__)

NODE_STATUS: dict[str, str] = {
    "load_memory": "載入長期記憶…",
    "check_memory_answer": "檢查記憶是否可回答…",
    "answer_from_memory": "從記憶直接回答…",
    "classify_intent": "分析使用者意圖…",
    "retrieve_docs": "檢索個人文件…",
    "clarify_query": "釐清問題…",
    "generate_chitchat": "回覆閒聊…",
    "generate_from_docs": "根據文件生成回答…",
    "evaluate_coverage": "評估文件是否足夠回答…",
    "offer_web_search": "準備詢問是否上網搜尋…",
    "web_search": "上網搜尋中…",
    "generate_from_web": "根據搜尋結果生成回答…",
    "generate_fallback": "生成一般回答…",
    "suggest_followups": "生成建議追問…",
}

INTENT_LABELS: dict[str, str] = {
    "chitchat": "閒聊",
    "general_knowledge": "通用知識（需上網）",
    "doc_query": "文件問答",
    "unclear": "問題不明確",
}

_HIGH_RETRIEVAL_SCORE = 0.65
_MEMORY_ANSWER_CONFIDENCE = 0.75

_CHITCHAT_RE = re.compile(
    r"^(你好|您好|嗨|哈囉|哈罗|早安|午安|晚安|謝謝|感谢|感謝|再見|拜拜|"
    r"好的|ok|okay|yes|謝了|多謝|辛苦了|"
    r"你是誰|你是什麼|你能做什麼|你能幫我什麼|"
    r"hi|hello|hey|thanks|thank you|bye|goodbye|good morning|good night)\s*[!！?？。.~]*$",
    re.I,
)

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

_FALLBACK_CLASSIFY = (
    "判斷使用者輸入是 chitchat、general_knowledge、doc_query 或 unclear。"
    "若問題過於模糊、缺少主語或無法判斷意圖，設 needs_clarification=true 並給出反問。"
    "輸出 JSON：intent/reason/needs_clarification/clarification_question/clarification_options"
)
_FALLBACK_MEMORY_CHECK = (
    "判斷長期記憶是否足以直接回答使用者問題。"
    "若可回答，給出簡潔繁體中文答案與 confidence（0-1）。"
    "輸出 JSON：can_answer/answer/confidence/reason"
)
_FALLBACK_CHITCHAT = "你是 IntelliAgnet 助手，以繁體中文簡短友善回應閒聊。"
_FALLBACK_FROM_DOCS = "你是 IntelliAgnet 筆記本助手，僅根據文件片段以繁體中文回答。"
_FALLBACK_FROM_WEB = "你是 IntelliAgnet 筆記本助手，根據搜尋結果以繁體中文回答。"
_FALLBACK_GENERAL = "你是 IntelliAgnet 助手，以繁體中文回答並引導使用者上傳文件。"
_FALLBACK_COVERAGE = "判斷文件是否足以回答，輸出 JSON sufficient/reason。"
_FALLBACK_FOLLOWUPS = "產生 3 個繁體中文後續追問，每行一個。"


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
    query_intent: str
    memory_context: str
    memory_can_answer: bool
    answered_from_memory: bool
    needs_clarification: bool
    clarification_question: str
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


def _emit_step(node: str, *, detail: str = "", completed: bool = True) -> None:
    try:
        get_stream_writer()(
            {
                "type": "step",
                "node": node,
                "message": NODE_STATUS.get(node, node),
                "completed": completed,
                "detail": detail,
            }
        )
    except Exception:
        pass


def _emit_token(content: str) -> None:
    if not content:
        return
    try:
        get_stream_writer()({"type": "token", "content": content})
    except Exception:
        pass


def _prompt_label(config: RunnableConfig) -> str:
    return (config.get("configurable") or {}).get("langfuse_prompt_label") or "production"


def _compile_system_prompt(
    config: RunnableConfig,
    name: str,
    *,
    fallback: str,
    **compile_kwargs: Any,
) -> str:
    client = (config.get("configurable") or {}).get("langfuse_client")
    return compile_langfuse_prompt_or_fallback(
        client,
        name,
        fallback=fallback,
        label=_prompt_label(config),
        **compile_kwargs,
    )


def _last_user_message(state: NotebookState) -> str:
    history = state.get("messages") or []
    for msg in reversed(history):
        if isinstance(msg, HumanMessage):
            return str(msg.content or "").strip()
    return str(state.get("user_query") or "").strip()


def _is_web_search_confirm(text: str) -> bool:
    return bool(_CONFIRM_RE.match((text or "").strip()))


def _is_obvious_chitchat(text: str) -> bool:
    return bool(_CHITCHAT_RE.match((text or "").strip()))


def _retrieval_scores_high(results: list) -> bool:
    if not results:
        return False
    scores = [float(r.get("Score") or 0) for r in results]
    return bool(scores) and min(scores) >= _HIGH_RETRIEVAL_SCORE


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
    config: RunnableConfig,
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

    system = _compile_system_prompt(
        config, "notebook_suggest_followups", fallback=_FALLBACK_FOLLOWUPS
    )
    try:
        structured = llm.with_structured_output(Followups)
        parts = [f"用户问题：{user_question}", f"助手回答：{answer[:2000]}"]
        if context_snippet:
            parts.append(f"相关文档摘录：{context_snippet[:1000]}")
        parts.append("请输出 3 个具体、可点击的后续追问，不要重复原问题。")
        result = await structured.ainvoke(
            [
                SystemMessage(content=system),
                HumanMessage(content="\n\n".join(parts)),
            ]
        )
        return [q.strip() for q in result.questions if q and q.strip()][:3]
    except Exception as exc:
        logger.warning("notebook suggested questions failed: %s", exc)
        return []


async def load_memory_node(state: NotebookState, config: RunnableConfig) -> dict:
    _emit_status("load_memory")
    _emit_step("load_memory")
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


def route_after_load(state: NotebookState, config: RunnableConfig) -> str:
    configurable = config.get("configurable") or {}
    user_msg = _last_user_message(state)
    pending = state.get("pending_web_search_query")
    if configurable.get("confirm_web_search") or (
        pending and _is_web_search_confirm(user_msg)
    ):
        return "web_search"
    if _is_obvious_chitchat(user_msg):
        return "chitchat_fast"
    if (state.get("memory_context") or "").strip():
        return "memory_check"
    return "parallel"


def route_after_memory_check(state: NotebookState, config: RunnableConfig) -> str:
    if state.get("memory_can_answer") and (state.get("final_response") or "").strip():
        return "memory_hit"
    return "parallel"


async def start_parallel_node(state: NotebookState, config: RunnableConfig) -> dict:
    return {}


async def check_memory_answer_node(state: NotebookState, config: RunnableConfig) -> dict:
    _emit_status("check_memory_answer", tool="llm")
    memory_context = (state.get("memory_context") or "").strip()
    if not memory_context:
        _emit_step("check_memory_answer", detail="無長期記憶")
        return {"memory_can_answer": False}

    llm = config["configurable"]["llm"]
    user_input = state.get("user_query") or _last_user_message(state)

    from pydantic import BaseModel, Field

    class MemoryAnswerCheck(BaseModel):
        can_answer: bool = Field(description="長期記憶是否足以直接回答")
        answer: str = Field(default="", description="若可回答，簡潔繁體中文答案")
        confidence: float = Field(default=0.0, description="0-1 置信度")
        reason: str = Field(default="", description="簡短理由")

    system = _compile_system_prompt(
        config, "notebook_check_memory", fallback=_FALLBACK_MEMORY_CHECK
    )
    try:
        structured = llm.with_structured_output(MemoryAnswerCheck)
        result = await structured.ainvoke(
            [
                SystemMessage(content=system),
                HumanMessage(
                    content=f"長期記憶：\n{memory_context}\n\n使用者問題：{user_input}"
                ),
            ]
        )
        detail = f"{result.reason}（置信度 {result.confidence:.0%}）"
        _emit_status("check_memory_answer", tool="llm", detail=detail)
        _emit_step("check_memory_answer", detail=detail)
        if (
            result.can_answer
            and result.confidence >= _MEMORY_ANSWER_CONFIDENCE
            and (result.answer or "").strip()
        ):
            return {
                "memory_can_answer": True,
                "final_response": result.answer.strip(),
                "answered_from_memory": True,
            }
    except Exception as exc:
        logger.warning("memory answer check failed: %s", exc)

    _emit_step("check_memory_answer", detail="記憶不足以直接回答")
    return {"memory_can_answer": False}


async def answer_from_memory_node(state: NotebookState, config: RunnableConfig) -> dict:
    _emit_status("answer_from_memory")
    answer = (state.get("final_response") or "").strip()
    _emit_token(answer)
    _emit_step("answer_from_memory", detail="已從長期記憶回答")
    return {
        "final_response": answer,
        "results": [],
        "source": ["長期記憶"],
        "answered_from_memory": True,
        "suggested_questions": [],
        "messages": [AIMessage(content=answer)],
    }


async def classify_intent_node(state: NotebookState, config: RunnableConfig) -> dict:
    _emit_status("classify_intent", tool="llm")
    llm = config["configurable"]["llm"]
    user_input = state.get("user_query") or _last_user_message(state)

    from pydantic import BaseModel, Field

    class IntentResult(BaseModel):
        intent: Literal["chitchat", "general_knowledge", "doc_query", "unclear"] = Field(
            description="使用者意圖類別"
        )
        needs_clarification: bool = Field(
            default=False, description="問題是否過於模糊需要反問"
        )
        clarification_question: str = Field(
            default="", description="若需反問，給使用者的繁體中文追問"
        )
        clarification_options: list[str] = Field(
            default_factory=list,
            description="2-3 個可點選的澄清選項（繁體中文）",
            max_length=4,
        )
        reason: str = Field(description="簡短分類理由")

    history = state.get("messages") or []
    history_snippet = ""
    if len(history) > 1:
        recent: list[str] = []
        for msg in history[-4:]:
            if isinstance(msg, HumanMessage):
                recent.append(f"使用者：{msg.content}")
            elif isinstance(msg, AIMessage):
                recent.append(f"助手：{str(msg.content or '')[:200]}")
        history_snippet = "\n".join(recent)

    system = _compile_system_prompt(
        config, "notebook_classify_intent", fallback=_FALLBACK_CLASSIFY
    )
    intent: str = "doc_query"
    reason = "default"
    needs_clarification = False
    clarification_question = ""
    clarification_options: list[str] = []
    try:
        structured = llm.with_structured_output(IntentResult)
        prompt_parts = [f"使用者輸入：{user_input}"]
        if history_snippet:
            prompt_parts.append(f"近期對話：\n{history_snippet}")
        result = await structured.ainvoke(
            [
                SystemMessage(content=system),
                HumanMessage(content="\n\n".join(prompt_parts)),
            ]
        )
        intent = result.intent
        reason = result.reason
        needs_clarification = bool(result.needs_clarification) or intent == "unclear"
        clarification_question = (result.clarification_question or "").strip()
        clarification_options = [
            o.strip() for o in (result.clarification_options or []) if o and o.strip()
        ][:3]
    except Exception as exc:
        logger.warning("intent classification failed: %s", exc)

    label = INTENT_LABELS.get(intent, intent)
    detail = f"{label} — {reason}"
    if needs_clarification:
        detail += "（需釐清）"
    _emit_status("classify_intent", tool="llm", detail=detail)
    _emit_step("classify_intent", detail=detail)
    return {
        "query_intent": intent,
        "needs_clarification": needs_clarification,
        "clarification_question": clarification_question,
        "suggested_questions": clarification_options,
    }


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
            retrieve_ctx = (
                observe_tool_span(
                    name="retrieve_docs",
                    input={"query": user_input, "index": index_name},
                    metadata={"user_id": str(user_id)},
                )
                if langfuse_configured()
                else nullcontext()
            )
            with retrieve_ctx:
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
    _emit_step("retrieve_docs", detail=detail)
    source, raw_context = _extract_sources(results) if results else ([], "")
    return {
        "retrieval_results": results,
        "results": _format_notebook_results(results),
        "source": source,
        "raw_context": raw_context,
        "offer_web_search": False,
    }


async def route_intent_node(state: NotebookState, config: RunnableConfig) -> dict:
    return {}


def route_after_intent(state: NotebookState, config: RunnableConfig) -> str:
    if state.get("needs_clarification"):
        return "clarify"

    intent = state.get("query_intent") or "doc_query"
    if intent == "chitchat":
        return "chitchat"
    if intent == "general_knowledge":
        return "general_web"

    if state.get("retrieval_results"):
        return "has_docs"

    user_msg = _last_user_message(state)
    if user_msg and not _is_web_search_confirm(user_msg):
        return "offer_web"
    return "fallback"


async def clarify_query_node(state: NotebookState, config: RunnableConfig) -> dict:
    _emit_status("clarify_query")
    options = state.get("suggested_questions") or []
    question = (state.get("clarification_question") or "").strip()
    if not question:
        question = "您的問題有點籠統，能否請您補充更多細節？"
        if not options:
            options = [
                "我想查詢已上傳的文件內容",
                "我想了解某個技術概念",
                "我想搜尋最新公開資訊",
            ]

    message = question
    if options:
        message += "\n\n您可以選擇以下方向，或直接描述您的需求：\n"
        message += "\n".join(f"• {opt}" for opt in options)

    _emit_token(message)
    _emit_step("clarify_query", detail=question[:80])
    return {
        "final_response": message,
        "needs_clarification": True,
        "clarification_question": question,
        "suggested_questions": options,
        "results": [],
        "source": [],
        "messages": [AIMessage(content=message)],
    }


async def generate_chitchat_node(state: NotebookState, config: RunnableConfig) -> dict:
    _emit_status("generate_chitchat", tool="llm")
    llm = config["configurable"]["llm"]
    memory_context = state.get("memory_context") or ""
    history = state.get("messages") or []

    system = _compile_system_prompt(
        config, "notebook_llm_chat", fallback=_FALLBACK_CHITCHAT
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
    _emit_step("generate_chitchat")
    return {
        "final_response": chat_response,
        "results": [],
        "source": [],
        "messages": [AIMessage(content=chat_response)],
    }


async def generate_from_docs_node(state: NotebookState, config: RunnableConfig) -> dict:
    _emit_status("generate_from_docs", tool="llm")
    llm = config["configurable"]["llm"]
    retrice_service = config["configurable"].get("retrice_service")
    memory_context = state.get("memory_context") or ""
    user_input = state.get("user_query") or ""
    raw_str = state.get("raw_context") or ""
    source = state.get("source") or []

    system = _compile_system_prompt(
        config, "notebook_generate_from_docs", fallback=_FALLBACK_FROM_DOCS
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

    _emit_step("generate_from_docs")
    return {"final_response": chat_response, "doc_sufficient": True}


async def evaluate_coverage_node(state: NotebookState, config: RunnableConfig) -> dict:
    _emit_status("evaluate_coverage", tool="llm")
    retrieval_results = state.get("retrieval_results") or []
    if _retrieval_scores_high(retrieval_results):
        _emit_step("evaluate_coverage", detail="高相關度檢索，跳過 LLM 評估")
        return {"doc_sufficient": True}

    llm = config["configurable"]["llm"]
    user_input = state.get("user_query") or ""
    answer = state.get("final_response") or ""
    raw_str = state.get("raw_context") or ""

    from pydantic import BaseModel, Field

    class Coverage(BaseModel):
        sufficient: bool = Field(description="文件片段是否足以回答用户问题")
        reason: str = Field(description="简短理由")

    system = _compile_system_prompt(
        config, "notebook_evaluate_coverage", fallback=_FALLBACK_COVERAGE
    )
    try:
        structured = llm.with_structured_output(Coverage)
        prompt = (
            f"用户问题：{user_input}\n\n"
            f"文件摘录：{raw_str[:1500]}\n\n"
            f"助手回答：{answer[:1500]}"
        )
        result = await structured.ainvoke(
            [SystemMessage(content=system), HumanMessage(content=prompt)]
        )
        _emit_step("evaluate_coverage", detail=result.reason)
        return {"doc_sufficient": bool(result.sufficient)}
    except Exception as exc:
        logger.warning("coverage evaluation failed: %s", exc)
        insufficient_markers = ("不足以", "無法從文件", "没有找到", "缺少", "尚無", "未找到")
        sufficient = not any(m in answer for m in insufficient_markers)
        _emit_step("evaluate_coverage")
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
    _emit_step("offer_web_search")
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
        results = mcp_search_web(query, max_results=8)
    except Exception as exc:
        logger.warning("web_search failed: %s", exc)
        results = []

    detail = f"找到 {len(results)} 筆搜尋結果" if results else "搜尋無結果"
    _emit_status("web_search", tool="search_web", detail=detail)
    _emit_step("web_search", detail=detail)
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
        _emit_step("generate_from_web", detail="無搜尋結果")
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

    system = _compile_system_prompt(
        config, "notebook_generate_from_web", fallback=_FALLBACK_FROM_WEB
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
    _emit_step("generate_from_web")
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

    system = _compile_system_prompt(
        config, "notebook_generate_fallback", fallback=_FALLBACK_GENERAL
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
    _emit_step("generate_fallback")
    return {
        "final_response": chat_response,
        "results": [],
        "source": [],
        "messages": [AIMessage(content=chat_response)],
    }


async def suggest_followups_node(state: NotebookState, config: RunnableConfig) -> dict:
    _emit_status("suggest_followups", tool="llm")
    llm = config["configurable"]["llm"]
    user_input = state.get("user_query") or ""
    answer = state.get("final_response") or ""
    raw_str = state.get("raw_context") or ""
    if state.get("web_search_used"):
        raw_str = json.dumps(state.get("web_search_results") or [])[:1000]

    suggested = await _generate_suggested_questions(
        llm,
        config,
        user_question=user_input,
        answer=answer,
        context_snippet=raw_str,
    )
    _emit_step("suggest_followups")
    return {
        "suggested_questions": suggested,
        "messages": [AIMessage(content=answer)],
        "offer_web_search": False,
        "pending_web_search_query": "",
    }


def create_notebook_graph(checkpointer: MongoDBSaver):
    graph = StateGraph(NotebookState)
    graph.add_node("load_memory", load_memory_node)
    graph.add_node("check_memory_answer", check_memory_answer_node)
    graph.add_node("answer_from_memory", answer_from_memory_node)
    graph.add_node("start_parallel", start_parallel_node)
    graph.add_node("classify_intent", classify_intent_node)
    graph.add_node("retrieve_docs", retrieve_docs_node)
    graph.add_node("route_intent", route_intent_node)
    graph.add_node("clarify_query", clarify_query_node)
    graph.add_node("generate_chitchat", generate_chitchat_node)
    graph.add_node("generate_from_docs", generate_from_docs_node)
    graph.add_node("evaluate_coverage", evaluate_coverage_node)
    graph.add_node("offer_web_search", offer_web_search_node)
    graph.add_node("web_search", web_search_node)
    graph.add_node("generate_from_web", generate_from_web_node)
    graph.add_node("generate_fallback", generate_fallback_node)
    graph.add_node("suggest_followups", suggest_followups_node)

    graph.add_edge(START, "load_memory")
    graph.add_conditional_edges(
        "load_memory",
        route_after_load,
        {
            "web_search": "web_search",
            "chitchat_fast": "generate_chitchat",
            "memory_check": "check_memory_answer",
            "parallel": "start_parallel",
        },
    )
    graph.add_conditional_edges(
        "check_memory_answer",
        route_after_memory_check,
        {"memory_hit": "answer_from_memory", "parallel": "start_parallel"},
    )
    graph.add_edge("answer_from_memory", END)
    graph.add_edge("start_parallel", "classify_intent")
    graph.add_edge("start_parallel", "retrieve_docs")
    graph.add_edge("classify_intent", "route_intent")
    graph.add_edge("retrieve_docs", "route_intent")
    graph.add_conditional_edges(
        "route_intent",
        route_after_intent,
        {
            "clarify": "clarify_query",
            "chitchat": "generate_chitchat",
            "general_web": "web_search",
            "has_docs": "generate_from_docs",
            "offer_web": "offer_web_search",
            "fallback": "generate_fallback",
        },
    )
    graph.add_edge("clarify_query", END)
    graph.add_edge("generate_chitchat", END)
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
    graph.add_edge("generate_fallback", END)
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
    completed_steps: list[dict[str, Any]] = []

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
                if chunk.get("type") == "step" and chunk.get("completed"):
                    completed_steps.append(
                        {
                            "node": chunk.get("node"),
                            "message": chunk.get("message"),
                            "detail": chunk.get("detail", ""),
                        }
                    )
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
        "query_intent": values.get("query_intent", ""),
        "needs_clarification": bool(values.get("needs_clarification")),
        "answered_from_memory": bool(values.get("answered_from_memory")),
        "steps": completed_steps,
    }
