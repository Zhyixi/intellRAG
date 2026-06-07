import datetime
import logging
import asyncio
import os
import re
from typing import Annotated, List, Literal
from typing_extensions import TypedDict
from langchain_core.messages import AnyMessage, AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.mongodb import MongoDBSaver
from common.langfuse_tracing import propagate_run_attributes, resolve_trace_id
from fast_api_service.api.ae.schemas import MessageSchema
from services.rag_service import RetriveService


# ==========================================
# 0. State & Helpers
# ==========================================

def safe_add_messages(left: list | AnyMessage | None, right: list | AnyMessage | None) -> list[AnyMessage]:
    """過濾掉因崩潰可能產生的 None 髒資料"""
    if isinstance(left, list):
        left = [m for m in left if m is not None]
    if isinstance(right, list):
        right = [m for m in right if m is not None]
    return add_messages(left, right)

class AgentState(TypedDict, total=False):
    messages: Annotated[List[AnyMessage], safe_add_messages]
    brand_en: str
    brand_zh: str
    errorcode: str
    model: str
    category: str
    intent: Literal["chat", "chat_node", "ae_faca", "ae_sop", "direct_analysis", ""]
    syslang: Literal["zh", "en", "vi", "pt", "es"]
    version: Literal["v1", "v2", "v3"]
    missing_info: str
    results: list
    sop_retrieve_flag: bool
    final_response: str
    source: list
    suggested_questions: list
    session_id: str
    input_lang: Literal["zh-cn", "zh", "en", "vi", "pt", "es"]
    refined_query: str
    query_extraction: dict

def build_sop_query(errorcode=None, brand=None, model=None):
    """組裝 SOP 數據庫查詢 SQL"""
    conditions = []
    params = {}
    if errorcode: 
        conditions.append("errorcode = :errorcode")
        params["errorcode"] = errorcode
    if model: 
        conditions.append("model = :model")
        params["model"] = model
    if brand: 
        conditions.append("(brand_zh = :brand OR brand_en = :brand)")
        params["brand"] = brand
    where_clause = " AND ".join(conditions) if conditions else "1=1"
    return f"SELECT * FROM iap_ae_errorcodelist WHERE {where_clause};", params


def tag_results(results: list, source_type: str) -> list:
    """保留既有 result 結構，同時標記資料來源供摘要分層使用。"""
    tagged = []
    for row in results:
        row = dict(row)
        row["source_type"] = source_type
        tagged.append(row)
    return tagged


MANUAL_QUERY_KEYWORDS = (
    "sop", "manual", "手冊", "说明书", "說明書", "维修", "維修",
    "故障碼", "故障码", "error code", "alarm", "报警", "警報",
    "參數", "参数", "設定", "设置", "排除", "處置", "处理",
    "操作", "步驟", "步骤", "按鍵", "按键", "key", "版本", "版號",
    "版本號", "系统信息", "系統信息", "示教盒", "解除條件", "解除条件",
    "畫面", "画面", "查詢", "查询", "顯示", "显示", "標示", "标示"
)


def is_manual_or_sop_query(query: str, intent: str | None) -> bool:
    if intent == "ae_sop":
        return True
    query_lower = (query or "").lower()
    return any(keyword.lower() in query_lower for keyword in MANUAL_QUERY_KEYWORDS)


def source_candidate_limits(query: str, intent: str | None) -> dict[str, int]:
    """依意圖控制各資料來源進入全域比較前的候選量。"""
    if is_manual_or_sop_query(query, intent):
        return {"FACA_CASE": 1, "DEVICE_MANUAL": 3, "SOP_FILE": 3}
    return {"FACA_CASE": 4, "DEVICE_MANUAL": 1, "SOP_FILE": 1}


def retrieval_result_limits(query: str, intent: str | None) -> dict[str, int]:
    """依查詢意圖控制各索引前段檢索量，避免固定 20 筆造成不必要成本。"""
    if is_manual_or_sop_query(query, intent):
        return {"FACA_CASE": 0, "DEVICE_MANUAL": 5, "SOP_FILE": 5}
    return {"FACA_CASE": 6, "DEVICE_MANUAL": 2, "SOP_FILE": 2}


def _row_score(row: dict) -> float:
    try:
        return float(row.get("Score") or 0)
    except (TypeError, ValueError):
        return 0.0


def _best_retrieval_rank(row: dict) -> int | None:
    debug = row.get("retrieval_debug") or {}
    ranks = []
    for key in ("semantic_rank", "lexical_rank"):
        try:
            rank = int(debug.get(key))
            if rank > 0:
                ranks.append(rank)
        except (TypeError, ValueError):
            continue
    return min(ranks) if ranks else None


def _looks_like_toc_or_noise(text: str) -> bool:
    lowered = text.lower()
    noisy_keywords = ("目录", "目錄", "table of contents", "一览表", "一覽表")
    if any(keyword in lowered for keyword in noisy_keywords):
        return True
    compact = "".join(text.split())
    if re.search(r"[a-z]?\d{2,}(?:[.\-/][a-z0-9]+)*", compact, re.IGNORECASE):
        return False
    return len(compact) < 12


def filter_source_results(rows: list[dict], source_type: str, query: str, intent: str | None, limit: int) -> list[dict]:
    """各來源先去除明顯無關/低品質內容，再交給跨來源排序。"""
    sorted_rows = sorted((dict(row) for row in rows), key=_row_score, reverse=True)
    if not sorted_rows or limit <= 0:
        return []

    max_score = max(_row_score(row) for row in sorted_rows) or 0.0
    manual_query = is_manual_or_sop_query(query, intent)
    filtered = []

    for row in sorted_rows:
        text = get_result_text(row)
        score = _row_score(row)
        best_rank = _best_retrieval_rank(row)
        issue_id = str(row.get("ISSUE_ID") or "")

        if _looks_like_toc_or_noise(text):
            continue

        if source_type == "FACA_CASE":
            if RetriveService.is_synthetic_issue_id(issue_id):
                continue
            filtered.append(row)
        else:
            relative_score_ok = max_score <= 0 or score >= max_score * (0.45 if manual_query else 0.65)
            rank_ok = best_rank is None or best_rank <= (35 if manual_query else 20)
            if relative_score_ok and rank_ok:
                filtered.append(row)

        if len(filtered) >= limit:
            break

    if filtered:
        return filtered
    if source_type == "FACA_CASE":
        fallback = [
            row for row in sorted_rows
            if _row_score(row) > 0 and not _looks_like_toc_or_noise(get_result_text(row))
        ]
        return fallback[:limit]
    if manual_query:
        return sorted_rows[:1]
    return []


def select_source_candidates(query: str,
                             intent: str | None,
                             tagged_issue: list[dict],
                             tagged_file: list[dict],
                             tagged_sop_file: list[dict]) -> list[dict]:
    limits = source_candidate_limits(query, intent)
    selected = (
        filter_source_results(tagged_issue, "FACA_CASE", query, intent, limits["FACA_CASE"])
        + filter_source_results(tagged_file, "DEVICE_MANUAL", query, intent, limits["DEVICE_MANUAL"])
        + filter_source_results(tagged_sop_file, "SOP_FILE", query, intent, limits["SOP_FILE"])
    )
    seen = {
        (str(row.get("ISSUE_ID") or ""), str(row.get("source_doc_id") or ""))
        for row in selected
    }
    if len(selected) < 5:
        fallback_pool = sorted(
            (dict(row) for row in tagged_issue + tagged_file + tagged_sop_file),
            key=_row_score,
            reverse=True,
        )
        for row in fallback_pool:
            key = (str(row.get("ISSUE_ID") or ""), str(row.get("source_doc_id") or ""))
            best_rank = _best_retrieval_rank(row)
            rank_ok = best_rank is None or best_rank <= (35 if is_manual_or_sop_query(query, intent) else 20)
            if key in seen or not rank_ok or _row_score(row) <= 0 or _looks_like_toc_or_noise(get_result_text(row)):
                continue
            selected.append(row)
            seen.add(key)
            if len(selected) >= 5:
                break
    return selected


def faca_relevant_filter() -> dict:
    # file_* 索引中 metadata.faca_relevant 使用 integer(0/1) 映射
    return {"term": {"metadata.faca_relevant": 1}}


def get_result_text(row: dict) -> str:
    return "\n".join(str(t).replace("passage: ", "") for t in row.get("Text", []))


def build_rerank_text(row: dict, max_chars: int = 1024) -> str:
    """保留錯誤碼、機型與原因/對策等關鍵片段，再限制送入 reranker 的長度。"""
    priority_parts = []
    for key in ("ISSUE_ID", "brand_zh", "brand_en", "model", "errorcode", "source_doc_id"):
        value = row.get(key)
        if value:
            priority_parts.append(f"{key}: {value}")

    text = get_result_text(row)
    keyword_lines = [
        line for line in text.splitlines()
        if any(keyword in line for keyword in ("功能描述", "發生原因", "发生原因", "解決方案", "解决方案", "處置", "处理", "error", "alarm"))
    ]
    merged = "\n".join(priority_parts + keyword_lines + [text])
    res = merged[:max_chars]
    return res

def sort_results_by_similarity(results: list,
                               query: str, retrice_service,
                               limit: int = 5) -> list:
    """合併不同來源後依檢索分數初排，並用 cross-encoder 重排最終候選。"""
    res = sorted(
        (dict(row) for row in results),
        key=lambda row: float(row.get("Score") or 0),
        reverse=True,
    )
    return res[:limit]


def build_source_key(row: dict) -> str:
    """為每筆檢索結果產生穩定的來源代號，供答案以 <ref>n</ref> 引用。

    優先用 ISSUE_ID；FACA 以外（手冊 / SOP）無 ISSUE_ID 時改用檔名(含頁碼)或 doc_id，
    確保所有來源都能被編號與引用。
    """
    issue_id = str(row.get("ISSUE_ID") or "").strip()
    if issue_id:
        return issue_id
    file_name = str(row.get("source_file_name") or "").strip()
    if file_name:
        page_label = str(row.get("source_page_label") or "").strip()
        return f"{file_name} p.{page_label}" if page_label else file_name
    return str(row.get("source_doc_id") or "").strip()


def format_result_for_summary(row: dict, max_content_chars: int = 1200) -> str:
    issue_id = row.get("ISSUE_ID", "")
    source_doc_id = row.get("source_doc_id", "")
    source_index = row.get("source_index")
    source_key = row.get("source_key") or issue_id or source_doc_id
    # [資料來源n] 編號與 precomputed_sources 同序，prompt 即據此用 <ref>n</ref> 標註
    label = f"[資料來源{source_index}]" if source_index else "[資料來源]"
    detail_str = (
        f"{label} : {source_key}\n"
        f"{build_rerank_text(row, max_chars=max_content_chars)}\n"
    )
    related_file = row.get("relatedFile", {})
    if isinstance(related_file, dict):
        for key, value in related_file.items():
            if isinstance(value, dict):
                description = value.get("DESCRIPTION", "")
                if description:
                    detail_str += f"{key} : {description[:240]}\n"
    return detail_str.strip()


# ==========================================
# 1. Graph Nodes
# ==========================================
async def preprocess_lang_node(state: AgentState, config: RunnableConfig):
    """判斷輸入語言並處理簡繁轉換"""
    logging.info("===preprocess_lang_node In===")
    retrice_service = config["configurable"]["retrice_service"]
    user_input = state["messages"][-1].content
    syslang = config["configurable"].get("syslang", "zh")
    if syslang == "zh":
        zh_type = await retrice_service.detect_zh_type(user_input)
        final_lang = "zh-cn" if zh_type == "zh-cn" else "zh"
        if final_lang == "zh-cn":
            user_input = retrice_service.t2s.convert(str(user_input))
        else:
            user_input = retrice_service.s2t.convert(str(user_input))
    else:
        final_lang = await retrice_service.detect_lang(user_input)
    logging.info(f"sys lang is {syslang}")
    logging.info(f"input lang is {final_lang}")
    logging.info("===preprocess_lang_node Out===")
    return {"refined_query": user_input, "input_lang": final_lang}


async def extract_intent_and_entities_node(state: AgentState, config: RunnableConfig):
    """判斷是否為閒聊，若非閒聊則區分 FACA 或 設備手冊(SOP) 並抽取實體"""
    logging.info("===extract_intent_and_entities_node In===")
    retrice_service = config["configurable"]["retrice_service"]
    user_input = state.get("refined_query", "")
    spec_intent = config["configurable"].get("intent", None)
    # A. 判斷是否為閒聊
    is_chitchat = await retrice_service.check_is_chitchat(user_input)
    if is_chitchat:
        logging.info("Intent is chat")
        logging.info("===extract_intent_and_entities_node Out===")
        return {"intent": "chat"}
    # B. 若設定中有指定 intent 則優先採用
    if spec_intent:
        logging.info(f"Specific intent {spec_intent}")
        logging.info("===extract_intent_and_entities_node Out===")
        return {"intent": spec_intent}    
    # C. 否則進行設備手冊(SOP)關鍵字與實體比對
    extracted, query_extraction = await retrice_service.sop_entity_extract(query=user_input)
    has_new_entity = any(extracted.get(key) for key in ["brand_en", "brand_zh", "errorcode", "model"])
    sop_keywords = ["維修", "故障", "代碼", "error", "code", "报警", "SOP", "手冊", "manual", "alarm"]
    has_sop_keyword = any(k.lower() in user_input.lower() for k in sop_keywords)
    if has_new_entity or has_sop_keyword:
        logging.info("判定為設備手冊(SOP)意圖")
        new_state = {
            "intent": "ae_sop",
            "query_extraction": query_extraction.model_dump(),
        }
        # 繼承並記憶既有參數
        if extracted.get("brand_zh") or extracted.get("brand_en"):
            for key in ["brand_en", "brand_zh", "errorcode", "model", "category"]:
                new_state[key] = extracted.get(key)
        else:
            for key in ["errorcode", "model", "category"]:
                new_state[key] = extracted.get(key)
    else:
        logging.info("判定為意圖")
        new_state = {
            "intent": "ae_faca",
            "brand_en": None, "brand_zh": None, "errorcode": None,
            "model": None, "category": None, "sop_retrieve_flag": False,
            "query_extraction": query_extraction.model_dump(),
        }
    logging.info(f"New states {new_state}")
    logging.info("===extract_intent_and_entities_node Out===")
    return new_state


async def check_completeness_node(state: AgentState, config: RunnableConfig):
    """針對設備手冊(SOP)檢查參數完整度，若不完整則生成提示字串"""
    tb_repo = config["configurable"]["tb_repo"]
    brand = state.get("brand_zh") or state.get("brand_en")
    model_name = state.get("model")
    errorcode = state.get("errorcode")
    category = state.get("category")
    logging.info("===check_completeness_node In===")
    
    if state.get("intent") != "ae_sop":
        return {"sop_retrieve_flag": False, "missing_info": None}
    sql, params = build_sop_query(errorcode=errorcode, brand=brand, model=model_name)
    errorcode_df = tb_repo.execute_raw_sql(sql, params=params)
    current_df = errorcode_df.copy()
    if brand and not current_df.empty:
        current_df = current_df[(current_df["brand_zh"] == brand) | (current_df["brand_en"] == brand)]
    if model_name and not current_df.empty:
        current_df = current_df[current_df["model"] == model_name]
    if errorcode and not current_df.empty:
        current_df = current_df[current_df["errorcode"] == errorcode]

    has_brand = bool(brand)
    has_model = bool(model_name)
    has_code  = bool(errorcode)
    sop_retrieve_flag = has_brand and has_model and has_code

    # 生成引導建議列表
    suggestions = []
    preview_df = current_df.drop_duplicates().head(5)
    for _, row in preview_df.iterrows():
        parts = []
        if not has_brand: parts.append(row['brand_zh'])
        if not has_model: parts.append(row['model'])
        if not has_code:  parts.append(row['errorcode'])
        if not parts: 
            parts = [row['brand_zh'], row['model'], row['errorcode']]
        suggestions.append("-".join(parts))
    suggestions_str = "\n, ".join(suggestions)

    # 根據缺少的欄位生成對應回覆
    if has_code:
        if has_brand and has_model:
            chat_response = f"您已提供 完整資訊: `{brand}`-`{model_name}`-`{errorcode}`\n"
        elif has_brand:
            chat_response = f"您已提供 廠牌與錯誤代碼: `{brand}`-`{errorcode}`\n請指定機型（參考）：\n{suggestions_str}"
        elif has_model:
            chat_response = f"您已提供 機型與錯誤代碼: `{model_name}`-`{errorcode}`\n"
        else:
            chat_response = f"您已提供 錯誤代碼: `{errorcode}`\n請補充廠牌與機型（參考）：\n{suggestions_str}"
    else:
        if has_brand and has_model:
            chat_response = f"您已提供 廠牌與機型: `{brand}`-`{model_name}`\n請補充錯誤代碼（參考）：\n{suggestions_str}"
        elif has_model:
            chat_response = f"您已提供 機型: `{model_name}`\n請補充廠牌與錯誤代碼（參考）：\n{suggestions_str}"
        elif has_brand:
            cat_str = f"與機型分類資訊:`{category}`" if category else ""
            chat_response = f"您已提供 廠牌: `{brand}`{cat_str}\n請指定機型與錯誤代碼（參考）：\n{suggestions_str}"
        else:
            sop_retrieve_flag = False
            prefix = f"您僅提供機型分類資訊`{category}`" if category else "您並未提供任何SOP檢索的必要資訊"
            chat_response = f"{prefix}\n請補充廠牌、機型與錯誤代碼（參考）：\n{suggestions_str}"
    logging.info("===check_completeness_node Out===")
    return {"missing_info": chat_response, "sop_retrieve_flag": sop_retrieve_flag}


async def chat_node(state: AgentState, config: RunnableConfig):
    """閒聊節點"""
    logging.info(f"=== chat_node In===")
    logging.info(f"=== chat_node Out===")
    return {
        "messages": [AIMessage(content="請詢問專業問題")], 
        "final_response": "請詢問專業問題", 
        "results": [] 
    }


async def ask_user_node(state: AgentState, config: RunnableConfig):
    """提示用戶補全 SOP 參數的節點"""
    logging.info(f"=== ask_user_node In===")
    missing = state["missing_info"]
    logging.info(f"AI 提示回應: {missing}")
    logging.info(f"=== ask_user_node Out===")
    return {
        "messages": [AIMessage(content=missing)], 
        "final_response": missing, 
        "results": []
    }


async def direct_analysis_node(state: AgentState, config: RunnableConfig):
    """不走 RAG，直接由 LLM 分析"""
    logging.info(f"=== direct_analysis_node In===")
    retrice_service = config["configurable"]["retrice_service"]
    user_input = state.get("refined_query", "")
    
    current_trace_id = resolve_trace_id(config)
    response = await retrice_service.llm_direct_analysis(
        query=user_input, 
        trace_id=current_trace_id
    )
    logging.info(f"=== direct_analysis_node Out===")
    return {
        "messages": [AIMessage(content=str(response))], 
        "final_response": str(response), 
        "results": [] ,
        "source": [] ,
        "suggested_questions": []
    }


async def execute_retrieve_node(state: AgentState, config: RunnableConfig):
    logging.info(f"=== execute_retrieve_node In===")
    """執行實際檢索，包含 SOP 手冊檢索與包含 v1/v2/v3 三種版本的 摘要邏輯"""
    retrice_service = config["configurable"]["retrice_service"]
    version = config["configurable"].get("version", "v3")
    syslang = config["configurable"].get("syslang", "zh")
    session_id = config["configurable"].get("session_id", "default")
    query = state.get("refined_query", "")
    results = []
    chat_response = ""
    source = []
    suggested_questions = []
    # ---- 狀況 A: SOP 資訊足夠時，保留精準 SOP 查詢路線 ----
    if state.get("intent") == "ae_sop" and state.get("sop_retrieve_flag"):
        brand = (state.get("brand_en") or "").strip() or (state.get("brand_zh") or "").strip()
        errorcode = (state.get("errorcode") or "").strip() or None
        model_name = (state.get("model") or "") or None
        results, chat_response_ = await retrice_service.sop_content_retrieve(
            query=query, errorcode=errorcode, model=model_name, brand=brand, lang_code=syslang
        )
        chat_response = f"\n以下為相關資訊:\n{chat_response_}"
    # ---- 狀況 B: 無 SOP 關鍵字時走 FACA，合併 FACA 與一般文件後依相似度排序 ----
    else:
        result_limits = retrieval_result_limits(query, state.get("intent"))
        query_extraction = state.get("query_extraction")

        async def _retrieve_sop_file():
            try:
                return await retrice_service.faca_retrieve(
                    prompt=query,
                    index_name=f"ae_sop_file_{syslang.lower()}",
                    result_limit=result_limits["SOP_FILE"],
                    metadata_filter=faca_relevant_filter(),
                    query_extraction=query_extraction,
                )
            except Exception as exc:
                logging.warning(f"SOP file fallback retrieval failed: {exc}")
                return [], None

        (results_issue, chat_response_issue), (result_file, _), (result_sop_file, _) = await asyncio.gather(
            retrice_service.faca_retrieve(
                prompt=query,
                index_name=f"iap_ae_issue_{syslang.lower()}",
                result_limit=result_limits["FACA_CASE"],
                query_extraction=query_extraction,
            ),
            retrice_service.faca_retrieve(
                prompt=query,
                index_name=f"file_{syslang.lower()}",
                result_limit=result_limits["DEVICE_MANUAL"],
                metadata_filter=faca_relevant_filter(),
                query_extraction=query_extraction,
            ),
            _retrieve_sop_file(),
        )

        tagged_issue = tag_results(results_issue, "FACA_CASE")
        tagged_file = tag_results(result_file, "DEVICE_MANUAL")
        tagged_sop_file = tag_results(result_sop_file, "SOP_FILE")
        source_candidates = select_source_candidates(
            query=query,
            intent=state.get("intent"),
            tagged_issue=tagged_issue,
            tagged_file=tagged_file,
            tagged_sop_file=tagged_sop_file,
        )
        results = sort_results_by_similarity(
            source_candidates,
            query,
            retrice_service
        )
        if version == "v1":
            chat_response = chat_response_issue
            
        elif version == "v2":
            for rank, row in enumerate(results, start=1):
                row["rank"] = rank
            detail_lines = [format_result_for_summary(row) for row in results]
            raw_str = "[詳情]\n\n" + "\n\n".join(detail_lines)
            chat_response_part2, chat_response_part1 = await asyncio.gather(
                retrice_service.llm_combination(query=query, raw_str=raw_str),
                retrice_service.llm_summary(query=query, raw_str=raw_str)
            )
            chat_response = f"{chat_response_part1}\n[詳情]\n{chat_response_part2}"
            
        elif version == "v3":
            precomputed_sources = []
            for rank, row in enumerate(results, start=1):
                row["rank"] = rank
                source_key = build_source_key(row)
                row["source_key"] = source_key
                if source_key:
                    if source_key not in precomputed_sources:
                        precomputed_sources.append(source_key)
                    row["source_index"] = precomputed_sources.index(source_key) + 1
                else:
                    row["source_index"] = None
            detail_lines = [format_result_for_summary(row) for row in results]
            raw_str = ("[DETAIL]\n"
                + "\n\n".join(detail_lines)
            )
            response = await retrice_service.llm_total_summary(
                query=query,
                raw_str=raw_str,
                syslang=syslang,
                session_id=session_id,
                precomputed_sources=precomputed_sources,
                trace_id=resolve_trace_id(config),
                parent_node_name="execute_retrieve_node",
                run_config=config,
            )
            source = response.source
            suggested_questions = response.suggested_questions
            chat_response = retrice_service.extract_deepseek_outputs(response.answer)
            
            if source:
                source_section = "\n\n參考依據：\n\n"
                for index, src in enumerate(source, start=1):
                    source_section += f"[{index}] {src}\n"
                chat_response += source_section
    logging.info(f"=== execute_retrieve_node Out===")
    return {
        "search_results": results, 
        "results": results,
        "final_response": chat_response, 
        "messages": [AIMessage(content=chat_response)],
        "source": source, 
        "suggested_questions": suggested_questions
    }


async def translate_node(state: AgentState, config: RunnableConfig):
    """翻譯與簡繁體轉換輸出節點"""
    logging.info(f"=== translate_node In ===")
    translator = config["configurable"].get("translator")
    syslang = config["configurable"].get("syslang", "zh")
    input_lang = state.get("input_lang", "zh")
    retrice_service = config["configurable"]["retrice_service"]
    chat_response = state.get("final_response", "請詢問相關專業問題")
    # Chat response
    chat_response_lang = await retrice_service.detect_lang(chat_response)
    if (syslang != 'zh') and translator and chat_response_lang != syslang:
        logging.info(f"Translation chat response")
        translated = await translator.translate(
            texts=[chat_response], source_lang="auto", target_lang=syslang.lower()
        )
        chat_response = translated[0]['translated']
    elif syslang == 'zh':
        logging.info(f"簡體繁體轉換")
        if input_lang == 'zh':
            chat_response = retrice_service.s2t.convert(str(chat_response))
        elif input_lang == 'zh-cn':
            chat_response = retrice_service.t2s.convert(str(chat_response))
    
    # suggested_questions
    suggestions = list(state.get("suggested_questions", []))
    new_sug = suggestions
    if suggestions and (syslang != 'zh') and translator:
        logging.info("Batch translation suggested_questions")
        sug_langs = await asyncio.gather(*(retrice_service.detect_lang(sug) for sug in suggestions))
        translate_positions = [idx for idx, lang in enumerate(sug_langs) if lang != syslang]
        if translate_positions:
            translated = await translator.translate(
                texts=[suggestions[idx] for idx in translate_positions],
                source_lang="auto",
                target_lang=syslang.lower()
            )
            new_sug = list(suggestions)
            for idx, item in zip(translate_positions, translated):
                new_sug[idx] = item['translated']
    elif suggestions and syslang == 'zh':
        logging.info("Batch 簡體繁體轉換 suggested_questions")
        if input_lang == 'zh':
            new_sug = [retrice_service.s2t.convert(str(sug)) for sug in suggestions]
        elif input_lang == 'zh-cn':
            new_sug = [retrice_service.t2s.convert(str(sug)) for sug in suggestions]
    
    logging.info(f"=== translate_node Out ===")
    return {'final_response': chat_response,
            "messages": [AIMessage(content=chat_response)],
            "suggested_questions":new_sug}


# ==========================================
# 2. Routing Logic
# ==========================================

def route_after_completeness(state: AgentState, config: RunnableConfig):
    """根據意圖與參數完整度決定下一步"""
    intent = state.get("intent")
    
    if intent in ("chat", "chat_node"):
        return "chat_node"
        
    # 如果指定走直接分析
    if intent == "direct_analysis":
        return "direct_analysis_node"
        
    # 如果是設備手冊(SOP)，資訊不足時也進入混合檢索，不直接中斷詢問補充
    if intent == "ae_sop":
        return "execute_retrieve_node"
            
    # 預設走 檢索
    return "execute_retrieve_node"


# ==========================================
# 3. Graph Builder
# ==========================================
_GRAPH_CACHE: dict[tuple[str, int], object] = {}


def _graph_cache_key(checkpointer: MongoDBSaver) -> tuple[str, int]:
    client = (
        getattr(checkpointer, "client", None)
        or getattr(checkpointer, "_client", None)
        or getattr(checkpointer, "mongo_client", None)
        or checkpointer
    )
    return (type(checkpointer).__name__, id(client))


def create_graph(checkpointer: MongoDBSaver, write_debug_graph: bool | None = None):
    cache_key = _graph_cache_key(checkpointer)
    if cache_key in _GRAPH_CACHE:
        return _GRAPH_CACHE[cache_key]

    workflow = StateGraph(AgentState)
    # 註冊所有標準節點
    workflow.add_node("preprocess_lang_node", preprocess_lang_node)
    workflow.add_node("extract_intent_and_entities_node", extract_intent_and_entities_node)
    workflow.add_node("check_completeness_node", check_completeness_node)
    workflow.add_node("chat_node", chat_node)
    workflow.add_node("ask_user_node", ask_user_node)
    workflow.add_node("direct_analysis_node", direct_analysis_node)
    workflow.add_node("execute_retrieve_node", execute_retrieve_node)
    workflow.add_node("translate_node", translate_node)
    
    # 建立主要連線
    workflow.add_edge(START, "preprocess_lang_node")
    workflow.add_edge("preprocess_lang_node", "extract_intent_and_entities_node")
    workflow.add_edge("extract_intent_and_entities_node", "check_completeness_node")
    
    # 動態路由分流
    workflow.add_conditional_edges(
        "check_completeness_node",
        route_after_completeness,
        {
            "chat_node": "chat_node",
            "ask_user_node": "ask_user_node",
            "direct_analysis_node": "direct_analysis_node",
            "execute_retrieve_node": "execute_retrieve_node"
        }
    )
    # 所有輸出流向翻譯節點後結束
    workflow.add_edge("chat_node", "translate_node")
    workflow.add_edge("ask_user_node", "translate_node")
    workflow.add_edge("direct_analysis_node", "translate_node")
    workflow.add_edge("execute_retrieve_node", "translate_node")
    workflow.add_edge("translate_node", END)
    app = workflow.compile(checkpointer=checkpointer)
    should_write_debug_graph = (
        write_debug_graph
        if write_debug_graph is not None
        else os.getenv("AE_GRAPH_DEBUG", "").lower() in {"1", "true", "yes"}
    )
    if should_write_debug_graph:
        try:
            with open("agent_graph.png", "wb") as f:
                f.write(app.get_graph().draw_mermaid_png())
            logging.info("結構圖已成功儲存為 agent_graph.png")
        except Exception as e:
            logging.info(f"儲存圖片失敗: {e}")
    _GRAPH_CACHE[cache_key] = app
    return app


async def process_agent_retrieve(user_input: str, run_config: dict, checkpointer: MongoDBSaver) -> dict:
    session_id = run_config["configurable"]["thread_id"]
    
    if "clear" in user_input or "delete" in user_input:
        checkpointer.delete_thread(session_id)
        
    agent = create_graph(checkpointer)
    metadata = run_config.get("metadata") or {}
    with propagate_run_attributes(
        session_id=metadata.get("session_id"),
        trace_name=metadata.get("langfuse_trace_name"),
        trace_id=metadata.get("trace_id"),
        environment=metadata.get("app_env"),
        domain=metadata.get("domain"),
    ):
        final_state = await agent.ainvoke(
            {"messages": [HumanMessage(content=user_input)]},
            config=run_config,
        )
    return final_state