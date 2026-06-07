import json
import logging
import re
from typing import Any, Dict, List

from langfuse import get_client
from common.langfuse_tracing import compile_langfuse_prompt_or_fallback, langfuse_configured
from typing_extensions import TypedDict
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

# ==========================================
# 1. 定義 Graph 的狀態 (State)
# ==========================================
class RouterState(TypedDict):
    # 輸入元資料
    raw_text: str
    file_name: str
    page_idx: int
    llm: Any
    
    # 內部路由標記 (供 Graph 判斷下一步)
    route_decision: str  
    
    # 最終輸出的單一陣列 (若無解法會保持空陣列)
    faca_data: List[Dict[str, Any]]
    has_solution: bool

# ==========================================
# 2. 定義 Nodes
# ==========================================

async def check_solution_node(state: RouterState) -> Dict[str, Any]:
    """節點 1：判斷文本是否含「可執行解決方案」"""
    lf_client = get_client() if langfuse_configured() else None
    system_prompt = compile_langfuse_prompt_or_fallback(
        lf_client,
        "etl_check_solution",
        fallback=(
            "你是一個 ETL 前置過濾器。請判斷文本是否至少包含一條可執行、可落地的問題解決方案。\n"
            "符合請回答 YES；不符合請回答 NO。不要輸出其他任何字。"
        ),
    )
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"文本：\n{state['raw_text']}")
    ]
    
    try:
        llm = state["llm"]
        response = await llm.ainvoke(messages)
        decision_text = response.content.strip().upper()
        # 若 LLM 回覆包含 YES，則標記為提取，否則結束
        decision = "extract" if "YES" in decision_text else "end"
    except Exception as e:
        print(f"[Graph Error] 判斷節點失敗: {e}")
        decision = "end"

    # 先初始化為空陣列，若判定為 end，Graph 結束時就會直接輸出 []
    return {"route_decision": decision, "faca_data": [], "has_solution": decision == "extract"}


async def extract_faca_node(state: RouterState) -> Dict[str, Any]:
    """節點 2：負責抓取明確的 FACA 內容並轉為 JSON 陣列"""
    lf_client = get_client() if langfuse_configured() else None
    system_prompt = compile_langfuse_prompt_or_fallback(
        lf_client,
        "etl_extract_faca",
        fallback=(
            "你是 FACA 分析專家。請輸出 JSON 陣列，每筆含 issue/reason/solution；"
            "若無可執行解決方案請輸出 []。"
        ),
    )
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"原始文本如下：\n\n{state['raw_text']}")
    ]
    
    extracted_list = []

    def _try_parse_json_array(raw: str) -> List[Dict[str, Any]]:
        if not raw:
            return []
        text = raw.strip()
        if not text:
            return []

        # 1) 直接解析
        try:
            data = json.loads(text)
            if isinstance(data, list):
                return data
        except Exception:
            pass

        # 2) 去除 markdown code fence 再解析
        text_no_fence = text.replace("```json", "").replace("```", "").strip()
        try:
            data = json.loads(text_no_fence)
            if isinstance(data, list):
                return data
        except Exception:
            pass

        # 3) 嘗試擷取第一段 JSON array 區塊
        match = re.search(r"\[[\s\S]*\]", text_no_fence)
        if match:
            try:
                data = json.loads(match.group(0))
                if isinstance(data, list):
                    return data
            except Exception:
                pass
        return []
    try:
        llm = state["llm"]
        response = await llm.ainvoke(messages)
        response_text = (response.content or "").strip()
        parsed_data = _try_parse_json_array(response_text)

        if parsed_data:
            # 檢查並注入來源 Metadata
            for item in parsed_data:
                if not isinstance(item, dict):
                    continue
                issue = str(item.get("issue") or "").strip()
                solution = str(item.get("solution") or "").strip()
                reason = str(item.get("reason") or "").strip()

                # 進一步防呆：排除空值與過度籠統的內容，降低無效頁面進入索引
                generic_tokens = {"null", "none", "n/a", "na", "無", "未知", "待確認"}
                if not issue or not solution:
                    continue
                if solution.lower() in generic_tokens:
                    continue
                if len(solution) < 6:
                    continue

                if reason.lower() in generic_tokens:
                    item["reason"] = "null"
                if item.get("issue") and item.get("solution"):
                    item["source_file"] = state.get("file_name", "unknown")
                    item["source_page"] = state.get("page_idx", -1)
                    extracted_list.append(item)
                    
        else:
            preview = response_text[:120].replace("\n", " ")
            logging.info(f"[Graph Skip] extract_faca_node 非 JSON 輸出，已略過。preview={preview}")
    except Exception as e:
        logging.warning(f"[Graph Error] extract_faca_node 失敗: {e}")
        
    return {"faca_data": extracted_list, "has_solution": bool(extracted_list)}


# ==========================================
# 3. 條件路由邏輯 (Conditional Edge)
# ==========================================
def route_condition(state: RouterState) -> str:
    """決定下一個要走哪個 Node"""
    return state["route_decision"]


# ==========================================
# 4. 組裝 Graph
# ==========================================
def create_repair_graph() -> CompiledStateGraph:
    """工廠函數：初始化並編譯 FACA 知識擷取的 LangGraph"""
    builder = StateGraph(RouterState)

    # 1. 註冊節點
    builder.add_node("check_solution", check_solution_node)
    builder.add_node("extract_faca", extract_faca_node)

    # 2. 設定流程
    builder.add_edge(START, "check_solution")
    
    # 3. 設定條件分支
    builder.add_conditional_edges(
        "check_solution",  # 判斷點在哪個 node 之後
        route_condition,   # 根據這個函數的回傳值來決定去向
        {
            "extract": "extract_faca", # 如果回傳 extract，走提取節點
            "end": END                 # 如果回傳 end，直接結束
        }
    )
    
    # 4. 提取完畢後結束
    builder.add_edge("extract_faca", END)

    return builder.compile()