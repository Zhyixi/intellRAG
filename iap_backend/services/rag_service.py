import hashlib
import os, sys, re
import time

from services.file_etl_graph import create_repair_graph
sys.path.extend(['.', '..'])
import uuid
from common.utils import generate_chunk_id
from services.services import TranslatorService
from langchain_core.prompts import ChatPromptTemplate
import jieba
from pathlib import Path
import pandas as pd
import re, copy
from LLM.RagEngine.build_image import build_pdf_image
from LLM.RagEngine.rag_utils import file2pdf
from configs.config import ENV, rag_index_dir, rag_chunk_size, INDEX_NAME, rag_dim, rag_index_dir, HTTP_PROXY, HTTPS_PROXY
from common.langfuse_tracing import compile_langfuse_prompt_or_fallback
from typing import Optional
import logging
from transformers import logging as transformers_logging
import shutil
import asyncio
from repositories.repositories import ElasticsearchManagerRepository, TableManagerRepository
from pathlib import Path
import re
from opencc import OpenCC
import pandas as pd
from collections import defaultdict
import copy, tqdm
import logging
import sys, os
import numpy as np
import pandas as pd
import json
import ast, datetime
import ast
import re
import re, json, datetime, logging
from difflib import get_close_matches
import re, unicodedata, datetime, logging
import jieba
from difflib import SequenceMatcher
from numpy import dot
from numpy.linalg import norm
from langchain_core.documents import Document
# Splitters
from langchain_text_splitters import RecursiveCharacterTextSplitter
# Loaders
from langchain_community.document_loaders import (
    DirectoryLoader,
    TextLoader,
    PyPDFLoader,
    CSVLoader,
    UnstructuredWordDocumentLoader,
    UnstructuredPowerPointLoader,
    UnstructuredExcelLoader,
    UnstructuredFileLoader
)
from typing import List
from langchain_elasticsearch import ElasticsearchStore
from langchain_core.messages import SystemMessage, HumanMessage
import asyncio
from elasticsearch.helpers import BulkIndexError
# 設置 transformers 日誌等級
transformers_logging.set_verbosity_error()
logging.getLogger("transformers").setLevel(logging.ERROR)
logging.getLogger("elasticsearch").setLevel(logging.WARNING)
logging.getLogger("elastic_transport.transport").setLevel(logging.WARNING)
pd.set_option('display.max_rows', None)
pd.set_option('display.max_columns', None)
from langchain_community.vectorstores import FAISS
from langchain_text_splitters import RecursiveCharacterTextSplitter
# 假設你原本有用 OpenAIEmbeddings 或其他 Embedding
from langchain_openai import OpenAIEmbeddings 
from typing import Any, Optional, Literal, Sequence
from pydantic import BaseModel, Field, model_validator
from elasticsearch import Elasticsearch
from typing import Dict, Any
import asyncio
from typing import TypedDict, List
from langgraph.graph import StateGraph, START, END
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field
from typing import List, Optional
from pydantic import BaseModel, Field
from typing import List, Optional
from langchain_core.messages import SystemMessage, HumanMessage
import datetime
import logging
from fastapi import FastAPI, HTTPException
import os


class ErrorTableAnalysis(BaseModel):
    error_code: str = Field(..., description="錯誤代碼")
    cause: str = Field(..., description="表格中的'原因'或'產生機理'欄位, 若沒有可空白")
    check_method: str = Field(..., description="表格中的'確認方法'欄位, 若沒有可空白")
    solution: str = Field(..., description="表格中的'處理措施'欄位, 若沒有可空白")

class RetrievalKeywordExtraction(BaseModel):
    error_codes: List[str] = Field(
        default_factory=list,
        description="錯誤代碼、告警編號、故障代號等，保留原文，形式不限",
    )
    symptoms: List[str] = Field(
        default_factory=list,
        description="失效模式、故障現象、異常描述、告警名稱等",
    )
    entities: List[str] = Field(
        default_factory=list,
        description="設備名稱、零件、型號、廠牌、工站等檢索實體",
    )

class QueryExtraction(RetrievalKeywordExtraction):
    brand_en: str = Field(default="", description="英文廠牌，無則留空")
    brand_zh: str = Field(default="", description="中文廠牌，無則留空")
    model: str = Field(default="", description="設備型號，無則留空")
    errorcode: str = Field(default="", description="最可能的錯誤代碼，無則留空")
    category: str = Field(default="", description="設備類別，無則留空")

class LLMSummary(BaseModel):
    """用於生成主要回答與後續建議問題的結構"""
    answer: str = Field(
        description="純 Markdown 格式的回應。⚠️ 極度重要：內文的引用標記只能使用純數字序號（如 <ref>1</ref>, <ref>2</ref>），絕對禁止填入文件名稱或 Issue ID。若查無資料，請填寫「根據目前的內部知識庫，查無相關維修指南。」"
    )
    source: List[str] = Field(
        description="參考資料陣列。填入實際的文件名稱或 Issue ID。只保留不重複的來源，且陣列順序必須對應 answer 中的 <ref>1</ref>, <ref>2</ref> 序號。若查無資料，請回傳空陣列 []。"
    )
    suggested_questions: List[str] = Field(
        description="預測使用者下一步最可能想追問的 3 個相關問題。問題需簡短、具體且具備技術引導性。",
        max_length=3,
        min_length=1
    )

# 1. 定義併發狀態
class SummaryState(TypedDict):
    query: str
    raw_str: str
    syslang: str
    trace_id: str
    precomputed_sources: List[str]
    # 併發產出的三個結果
    answer: str
    source: List[str]
    suggested_questions: List[str]

# --- 🌟 節點 A: 產生核心回答 (Answer Node) ---
async def gen_answer_node(state: SummaryState, config: RunnableConfig):
    service = config["configurable"]["retrive_service"]
    # 專用 Prompt：只專注在回答問題（引用規則寫在 langfuse 的 generate_answer prompt 內，
    # 這裡僅負責把帶有 [來源 n] 編號的檢索內容餵進去，<ref>n</ref> 的編號即由輸入決定）
    system_prompt = compile_langfuse_prompt_or_fallback(
        service.langfuse_client,
        "generate_answer",
        fallback=(
            "你是一位維修專家。請根據提供資料回答問題。語言：{syslang}\n"
            "規則：\n"
            "1. 僅能使用待解析資料中的來源與內容，不得自行補充未被資料支持的原因或處理方式。\n"
            "2. 優先採用 rank 較前、score 較高且與問題直接相關的來源。\n"
            "3. 若多個來源的原因或對策不一致，請分別列出「可能原因」與「對應處置」，不要融合成單一結論。\n"
            "4. 若資料不足，請明確說明目前資料不足，並建議補充品牌、機型、錯誤碼或現象細節。"
        ),
        syslang=state["syslang"],
    )

    user_input = f"待解析資料：{state['raw_str']}\n問題：{state['query']} /no_think"
    try:
        # 調用你調優過的 
        response = await service._safe_llm_call(
            [SystemMessage(content=system_prompt), HumanMessage(content=user_input)],
            gen_name="parallel_gen_answer",
            trace_id=state['trace_id']
        )
        res = service.extract_deepseek_outputs(response.content)
    except Exception as e:
        logging.error(f"""gen_answer_node 發生錯誤:{e}
                      system_prompt:
                      {system_prompt}
                      user_input:
                      {user_input}""")
        res = await service.llm_direct_analysis(f"請分析用戶問題但不要過度思考：{state['query']}", state['trace_id'])
    return {"answer": res}

# --- 🌟 節點 B: 提取來源 (Source Node) ---
async def gen_source_node(state: SummaryState, config: RunnableConfig):
    service = config["configurable"]["retrive_service"]
    precomputed_sources = [
        str(source_id).strip()
        for source_id in (state.get("precomputed_sources") or [])
        if str(source_id).strip()
    ]
    if precomputed_sources:
        logging.info(f"結構化提取資料來源:{precomputed_sources}")
        return {"source": precomputed_sources}
    parsed_sources = []
    for match in re.finditer(r"issue_id:\s*(\S+)", str(state.get("raw_str") or "")):
        source_id = match.group(1).strip()
        if source_id and source_id not in parsed_sources:
            parsed_sources.append(source_id)
    if parsed_sources:
        logging.info(f"結構化提取資料來源:{parsed_sources}")
        return {"source": parsed_sources}
    system_prompt = compile_langfuse_prompt_or_fallback(
        service.langfuse_client,
        "gen_source",
        fallback="請從資料中提取所有相關的 ISSUE_ID 或文件名稱，僅輸出 陣列",
    )
    user_input = f"資料：{state['raw_str']}\n問題：{state['query']} /no_think"
    try:
        response = await service._safe_llm_call(
            [SystemMessage(content=system_prompt), HumanMessage(content=user_input)],
            gen_name="parallel_gen_source",
            trace_id=state['trace_id']
        )
        # 簡單清理並轉為 List
        sources = service.extract_deepseek_outputs(response.content)
        sources_lst = [s.strip().replace('[', '').replace(']', '').replace('"', '') for s in sources.split(',') if s.strip()]
        logging.info(f"生成資料來源:{sources_lst}")
        return {"source": sources_lst}
    except Exception as e:
        logging.error(f"gen_source_node 發生錯誤:{e}")
        return {"source": []}
        
# --- 🌟 節點 C: 產生建議問題 (Questions Node) ---
async def gen_questions_node(state: SummaryState, config: RunnableConfig):
    service = config["configurable"]["retrive_service"]
    system_prompt = compile_langfuse_prompt_or_fallback(
        service.langfuse_client,
        "gen_questions",
        fallback="請根據對話內容，產生 3 個簡短的後續追問問題。",
    )
    
    try:
        response = await service._safe_llm_call(
        [SystemMessage(content=system_prompt), HumanMessage(content=state['query'] + " /no_think")],
        gen_name="parallel_gen_questions",
        trace_id=state['trace_id']
    )
    
    
        questions = service.extract_deepseek_outputs(response.content).split('\n')
        return {"suggested_questions": [q.strip() for q in questions if q.strip()][:3]}
    except Exception as e:
        logging.error(f"gen_questions_node 發生錯誤:{e}")
        return {"suggested_questions": []}

# --- 2. 建立併發圖 (Parallel Graph) ---
def create_summary_parallel_graph():
    workflow = StateGraph(SummaryState)
    # 加入節點
    workflow.add_node("gen_answer", gen_answer_node)
    workflow.add_node("gen_source", gen_source_node)
    workflow.add_node("gen_questions", gen_questions_node)
    
    # 🌟 核心：從 START 同時指向三個節點實現「併發」
    workflow.add_edge(START, "gen_answer")
    workflow.add_edge(START, "gen_source")
    workflow.add_edge(START, "gen_questions")
    
    # 匯合至結束
    workflow.add_edge("gen_answer", END)
    workflow.add_edge("gen_source", END)
    workflow.add_edge("gen_questions", END)
    
    return workflow.compile()

# 預編譯實例
summary_app = create_summary_parallel_graph()



class RAGService:
    _LLM_POLICY = {
        "default": {
            "primary_failures_before_open": 5,
            "circuit_open_seconds": 90,
            "primary_timeout_s": 20,
        },
        "query_extraction": {
            "primary_failures_before_open": 3,
            "circuit_open_seconds": 60,
            "primary_timeout_s": 6,
        },
        "summary": {
            "primary_failures_before_open": 4,
            "circuit_open_seconds": 90,
            "primary_timeout_s": 30,
        },
    }

    def __init__(self, **deps) -> None:
        self._es_repo = deps['es_repo']
        self._tb_repo = deps['tb_repo']
        self.ollama_general_llm=deps['ollama_general_llm']
        self.vllm_general_llm=deps['vllm_general_llm']
        self.embedding_model=deps['embedding']
        self.translator = deps['translator']
        self.langfuse_handler  = deps['langfuse_handler']
        self.langfuse_client  = deps['langfuse_client']
        
        self.reranker_model_path = deps.get('reranker_model_path')
        self.reranker = deps.get('reranker', None)
        self.rag_chunk_size = 1024 
        self.rag_chunk_overlap = 0 # LangChain 習慣設定重疊區間
        self._vector_stores = {}
        self._llm_primary_state: dict[str, dict[str, float | int]] = {}
        self.s2t = OpenCC('s2t')
        self.t2s = OpenCC('t2s')

    def _llm_policy(self, task_type: str) -> dict:
        base = dict(self._LLM_POLICY["default"])
        base.update(self._LLM_POLICY.get(task_type, {}))
        return base

    def _is_retryable_llm_error(self, exc: Exception) -> bool:
        retryable_types = (TimeoutError, ConnectionError, OSError)
        if isinstance(exc, retryable_types):
            return True
        msg = str(exc).lower()
        retryable_tokens = (
            "connection",
            "timed out",
            "timeout",
            "all connection attempts failed",
            "service unavailable",
            "temporarily unavailable",
            "length limit was reached",
            "max token",
            "rate limit",
            "429",
            "502",
            "503",
            "504",
        )
        return any(token in msg for token in retryable_tokens)

    def _is_primary_circuit_open(self, task_type: str) -> bool:
        llm_state = getattr(self, "_llm_primary_state", {})
        state = llm_state.get(task_type)
        if not state:
            return False
        open_until = float(state.get("open_until", 0))
        return time.monotonic() < open_until

    def _mark_primary_failure(self, task_type: str) -> None:
        policy = self._llm_policy(task_type)
        if not hasattr(self, "_llm_primary_state"):
            self._llm_primary_state = {}
        state = self._llm_primary_state.setdefault(task_type, {"failures": 0, "open_until": 0.0})
        failures = int(state.get("failures", 0)) + 1
        state["failures"] = failures
        if failures >= int(policy["primary_failures_before_open"]):
            state["open_until"] = time.monotonic() + float(policy["circuit_open_seconds"])
            logging.warning(
                "primary llm circuit opened task=%s failures=%s",
                task_type,
                failures,
            )

    def _mark_primary_success(self, task_type: str) -> None:
        if not hasattr(self, "_llm_primary_state"):
            self._llm_primary_state = {}
        state = self._llm_primary_state.setdefault(task_type, {"failures": 0, "open_until": 0.0})
        state["failures"] = 0
        state["open_until"] = 0.0

    def _llm_label(self, llm: Any) -> str:
        return getattr(llm, "model_name", None) or getattr(llm, "model", None) or type(llm).__name__

    def _bind_llm(
        self,
        llm: Any,
        *,
        task_type: str,
        trace_id: str | None = None,
        gen_name: str | None = None,
        max_tokens: int | None = None,
        fallback_used: bool = False,
        selected_model: str | None = None,
    ) -> Any:
        policy = self._llm_policy(task_type)
        bind_kwargs: dict[str, Any] = {
            "temperature": 0,
            "timeout": float(policy["primary_timeout_s"]),
        }
        if max_tokens is not None:
            bind_kwargs["max_tokens"] = int(max_tokens)
        metadata = {}
        if trace_id:
            metadata["trace_id"] = str(trace_id)
        if gen_name:
            metadata["generation_name"] = gen_name
        metadata["task_type"] = task_type
        metadata["fallback_used"] = bool(fallback_used)
        if selected_model:
            metadata["selected_model"] = selected_model
        if metadata:
            bind_kwargs["extra_body"] = {"metadata": metadata}
        try:
            return llm.bind(**bind_kwargs)
        except Exception:
            # 某些 provider（如 ChatOllama）不支援 extra_body/timeout，降級最小綁定參數。
            minimal_kwargs = {"temperature": 0}
            if max_tokens is not None:
                minimal_kwargs["max_tokens"] = int(max_tokens)
            return llm.bind(**minimal_kwargs)

    async def _invoke_with_fallback(
        self,
        *,
        messages: list,
        task_type: str = "default",
        trace_id: str | None = None,
        gen_name: str | None = None,
        max_tokens: int | None = None,
    ):
        primary = self.vllm_general_llm
        secondary = self.ollama_general_llm
        use_secondary_first = self._is_primary_circuit_open(task_type)
        candidates = ([secondary, primary] if use_secondary_first else [primary, secondary])

        last_exc: Exception | None = None
        for idx, llm in enumerate(candidates):
            if llm is None:
                continue
            llm_name = self._llm_label(llm)
            try:
                bound_llm = self._bind_llm(
                    llm,
                    task_type=task_type,
                    trace_id=trace_id,
                    gen_name=gen_name,
                    max_tokens=max_tokens,
                    fallback_used=(idx > 0),
                    selected_model=llm_name,
                )
                response = await bound_llm.ainvoke(messages)
                if idx == 0 and llm is primary:
                    self._mark_primary_success(task_type)
                if idx > 0:
                    logging.warning("llm fallback used task=%s model=%s", task_type, llm_name)
                return response
            except Exception as exc:
                last_exc = exc
                is_primary = llm is primary
                retryable = self._is_retryable_llm_error(exc)
                if is_primary and retryable:
                    self._mark_primary_failure(task_type)
                logging.warning(
                    "llm invoke failed task=%s model=%s retryable=%s error=%s",
                    task_type,
                    llm_name,
                    retryable,
                    exc,
                )
                # 非可重試錯誤不做跨模型 fallback，避免掩蓋 prompt/schema 問題
                if not retryable:
                    raise
                continue

        if last_exc:
            raise last_exc
        raise RuntimeError("No available LLM candidates")

    def _normalize_ref_tags(self, answer: str, sources: List[str]) -> str:
        """統一引用標記為標準 <ref>n</ref>。

        只負責收斂模型輸出的各種寫法（[1]、【1】、殘缺標籤等），
        不自行補造引用；文末的「參考依據 [n]」清單由呼叫端自行組裝。
        """
        text = str(answer or "")
        # 各種形式統一為標準 <ref>n</ref>
        text = re.sub(r"\[(\d+)\]", r"<ref>\1</ref>", text)
        text = re.sub(r"【(\d+)】", r"<ref>\1</ref>", text)
        # 殘缺標籤（如 <ref>1<ref>）補回正確結尾
        text = re.sub(r"<ref>\s*(\d+)\s*<ref>", r"<ref>\1</ref>", text)
        # 收斂多餘空白
        text = re.sub(r"<ref>\s*(\d+)\s*</ref>", r"<ref>\1</ref>", text)
        # 移除非數字引用，避免把 ISSUE_ID / 檔名塞進 <ref>
        text = re.sub(r"<ref>\s*([^<>\d][^<>]*?)\s*</ref>", "", text)
        return text
    
    def get_reranker(self):
        if self.reranker is not None:
            return self.reranker
        if not self.reranker_model_path:
            return None
        try:
            from langchain_community.cross_encoders import HuggingFaceCrossEncoder
            self.reranker = HuggingFaceCrossEncoder(
                model_name=self.reranker_model_path,
                model_kwargs={"device": "cpu"}
            )
        except Exception as exc:
            logging.warning(f"reranker lazy loading failed: {exc}")
            self.reranker = None
        return self.reranker
    
    
    
    
    async def _safe_llm_call(self, messages, gen_name, trace_id):
        """統一 LLM 呼叫：自動注入影子計費與 vLLM/Ollama 備援"""
        return await self._invoke_with_fallback(
            messages=messages,
            task_type="default",
            trace_id=str(trace_id) if trace_id else None,
            gen_name=gen_name,
        )

    def _sanitize_mineru_markdown(self, content: str) -> str:
        """清理 MinerU markdown 噪音，保留對解法判斷有用的文字。"""
        if not content:
            return ""
        text = content
        # 去除 markdown 圖片連結
        text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "\n", text)
        # 保留 details 內容但移除標籤
        text = re.sub(r"</?details>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"</?summary>", "\n", text, flags=re.IGNORECASE)
        # 移除常見 HTML 標籤（table/tr/td/p...）
        text = re.sub(r"<[^>]+>", " ", text)
        # 合併空白與多餘換行
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _is_low_signal_text(self, text: str) -> bool:
        """判斷文本是否低訊號（多為封面/簽核/版面噪音）。"""
        if not text:
            return True
        stripped = text.strip()
        if len(stripped) < 120:
            return True
        lines = [ln.strip() for ln in stripped.splitlines() if ln.strip()]
        if not lines:
            return True
        short_line_ratio = sum(1 for ln in lines if len(ln) <= 4) / max(len(lines), 1)
        low_signal_tokens = [
            "文件名稱", "制訂", "修改記錄", "版別", "核准", "流程圖", "適用範圍",
            "Tên văn kiện", "Ghi chép", "Phê duyệt", "Bộ phận"
        ]
        token_hits = sum(1 for tk in low_signal_tokens if tk in stripped)
        return short_line_ratio >= 0.6 or token_hits >= 4

    def _extract_page_texts_from_mineru_content_json(self, md_path: Path) -> List[Dict[str, Any]]:
        """
        從 MinerU content_list JSON 逐頁提取文字。
        回傳格式: [{"page_idx": 1-based int, "text": str, "page_snapshot": str}, ...]
        """
        candidates = list(md_path.parent.glob("*_content_list_v2.json")) + list(md_path.parent.glob("*_content_list.json"))
        if not candidates:
            return []
        for json_path in candidates:
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
                page_texts: List[Dict[str, Any]] = []
                for page_idx, page in enumerate(data if isinstance(data, list) else [], start=1):
                    if not isinstance(page, list):
                        continue
                    texts = []
                    page_snapshot = ""
                    for block in page:
                        if not isinstance(block, dict):
                            continue
                        content = block.get("content")
                        if isinstance(content, dict):
                            # 優先抓該頁第一張圖片做 snapshot
                            if not page_snapshot:
                                image_source = content.get("image_source")
                                if isinstance(image_source, dict):
                                    rel_img_path = str(image_source.get("path") or "").strip()
                                    if rel_img_path:
                                        page_snapshot = str((md_path.parent / rel_img_path).resolve())
                            # 常見鍵值統一提取
                            for key in ["content", "text", "title_content", "paragraph_content", "image_caption", "image_footnote"]:
                                value = content.get(key)
                                if isinstance(value, str) and value.strip():
                                    texts.append(value.strip())
                                elif isinstance(value, list):
                                    for item in value:
                                        if isinstance(item, dict):
                                            v = item.get("content")
                                            if isinstance(v, str) and v.strip():
                                                texts.append(v.strip())
                    page_text = self._sanitize_mineru_markdown("\n".join(texts))
                    if page_text:
                        page_texts.append({"page_idx": page_idx, "text": page_text, "page_snapshot": page_snapshot})
                if page_texts:
                    return page_texts
            except Exception as exc:
                logging.warning(f"讀取 MinerU content json 失敗: {json_path}, error={exc}")
                continue
        return []
    
    async def file_process_md(self, md_path):
        md_file = Path(md_path)
        source_file_name = md_file.parent.name
        raw_content = md_file.read_text(encoding="utf-8")
        content = self._sanitize_mineru_markdown(raw_content)
        file_etl_agent = create_repair_graph()
        
        # 優先用 MinerU content_list 逐頁抽取，保留來源頁碼
        page_payloads = self._extract_page_texts_from_mineru_content_json(md_file)
        if page_payloads:
            all_items: List[Dict[str, Any]] = []
            for payload in page_payloads:
                page_text = payload.get("text", "")
                page_idx = payload.get("page_idx", -1)
                if not page_text or self._is_low_signal_text(page_text):
                    continue
                state = {
                    "raw_text": page_text,
                    "file_name": source_file_name,
                    "page_idx": page_idx,
                    "llm": self.vllm_general_llm
                }
                page_result = await file_etl_agent.ainvoke(state)
                items = page_result.get("faca_data") or []
                if items:
                    page_snapshot = payload.get("page_snapshot", "")
                    for item in items:
                        if isinstance(item, dict) and page_snapshot:
                            item["source_snapshot"] = page_snapshot
                    all_items.extend(items)
            if all_items:
                return {
                    "faca_data": all_items,
                    "has_solution": True,
                    "parse_strategy": "content_json_pages",
                    "fallback_attempted": False,
                    "source_file_name": source_file_name,
                }

        # content_list 不可用或未抽出結果時，才退回 markdown 全文模式
        initial_state = {
            "raw_text": content,
            "file_name": source_file_name,
            "page_idx": -1,
            "llm": self.vllm_general_llm
        }
        final_state = await file_etl_agent.ainvoke(initial_state)
        final_state["parse_strategy"] = "markdown"
        final_state["fallback_attempted"] = bool(page_payloads)
        final_state["source_file_name"] = source_file_name
        return final_state
    
    
    def _get_vector_store(self, index_name: str):
        """根據 index_name 獲取或建立對應的 Store"""
        if index_name not in self._vector_stores:
            # 只有在該 index 第一次被呼叫時才建立實例
            self._vector_stores[index_name] = ElasticsearchStore(
                index_name=index_name,
                client=self._es_repo.es,
                embedding=self.embedding_model
            )
        return self._vector_stores[index_name]
    
    
    def _get_loader_for_file(self, file_path: str):
        """根據副檔名決定要使用哪個 Loader"""
        ext = os.path.splitext(file_path)[1].lower()
        # 定義副檔名與 Loader 的映射
        # 注意: DOCX, PPTX, XLSX 需要安裝: pip install unstructured openpyxl python-docx python-pptx
        loaders_map = {
            ".txt": TextLoader,
            ".md": TextLoader,
            ".py": TextLoader,
            ".csv": CSVLoader,
            ".pdf": PyPDFLoader,
            ".docx": UnstructuredWordDocumentLoader,
            ".doc": UnstructuredWordDocumentLoader,
            ".pptx": UnstructuredPowerPointLoader,
            ".ppt": UnstructuredPowerPointLoader,
            ".xlsx": UnstructuredExcelLoader,
            ".xls": UnstructuredExcelLoader,
        }
        
        loader_cls = loaders_map.get(ext)
        if loader_cls:
            # 大部分 Loader 建構子只需要 file_path
            # CSVLoader 可能需要 encoding='utf-8'，這裡做個簡單處理
            if loader_cls == CSVLoader:
                return loader_cls(file_path, encoding='utf-8')
            return loader_cls(file_path)
        # 沒在清單內的，嘗試用 Unstructured 通用讀取器 (如果系統有安裝依賴)
        logging.warning(f"No specific loader found for {ext}, trying UnstructuredFileLoader.")
        return UnstructuredFileLoader(file_path)
    
    async def get_nodes_from_es_by_id(self, index_name: str, chunk_id=None):
        if chunk_id is None:
            query = {"query": {"match_all": {}}}
        else:
            query = {
                "query": {
                    "match": {
                        "_id": chunk_id
                    }
                }
            }
        response = self._es_repo.es.search(index=index_name, body=query)
        try:
            hits = response.get("hits", {}).get("hits", [])
        except Exception as e:
            hits = response['hits']['hits']
        return hits
    
    
    def get_embedding(self, texts: list[str] | str):
        try:
            # 1. 處理單一字串 (Query 模式)
            if isinstance(texts, str):
                # 去除換行符號以確保向量品質
                clean_text = texts.replace("\n", " ")
                # 使用 LangChain 標準的 embed_query
                return self.embedding_model.embed_query(clean_text)
            
            # 2. 處理字串列表 (Batch 模式)
            elif isinstance(texts, list):
                # 批量替換換行符號
                clean_texts = [t.replace("\n", " ") for t in texts]
                # 使用 LangChain 標準的 embed_documents (取代 get_text_embedding_batch)
                return self.embedding_model.embed_documents(clean_texts)
            
            else:
                raise TypeError(f"不支援的輸入型態: {type(texts)}，僅支援 str 或 list[str]")

        except Exception as e:
            # 使用更具體的錯誤訊息
            raise AssertionError(f"獲取向量時發生錯誤: {str(e)}")
    
    async def llm_summary(self, query:str, raw_str:str):
        
        system_prompt = compile_langfuse_prompt_or_fallback(
            self.langfuse_client,
            "llm_summary",
            fallback=(
                "你是一位專業的維修總結助理，請根據使用者問題：「{query}」，從以下維修資料中萃取相關內容。\n\n"
                "請產出一份「摘要」，以一段不超過 100 字的摘要詳細但不冗長的回應問題, 且不可用...忽略, 需要完整收尾。\n\n"
                "格式範例如下（請勿照抄內容）：\n"
                "【摘要】\n"
                "螺絲浮鎖主要因底孔尺寸及定位偏差，造成鎖附不良"
            ),
            query=query,
        )
        
        user_input = f"維修紀錄內容：{raw_str} /no_think"
        
        # 準備對話內容
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_input)
        ]
        t0 = datetime.datetime.now()
        response = await self._invoke_with_fallback(
            messages=messages,
            task_type="summary",
            gen_name="llm_summary",
        )
        # 取出回覆內容
        new_query = self.extract_deepseek_outputs(response.content)
        t1 = datetime.datetime.now()
        logging.info(f"llm_summary 花費: {round((t1-t0).total_seconds(), 2)} seconds")
        return new_query
    
    
    async def detect_lang(self, text):
        system_prompt = compile_langfuse_prompt_or_fallback(
            self.langfuse_client,
            "detect_lang",
            fallback=(
                "你是一位語言專家, 請判斷用戶輸入的語言是以下哪一種的可能性最高, 請參考下方語系對應表, 只輸出語系代碼\n"
                "以下是 \"代碼:語言\"\n"
                "en:英文\nzh:繁體中文\ncn:簡體中文\nvi:越南文\npt:葡萄牙文\nes:西班牙文\n"
                "other:無法判斷或不是以上語言"
            ),
        )
        
        user_input = f"輸入的語言：\"{text}\" /no_think"
        
        # 準備對話內容
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_input)
        ]
        t0 = datetime.datetime.now()
        response = await self._invoke_with_fallback(
            messages=messages,
            task_type="default",
            gen_name="detect_lang",
            max_tokens=64,
        )
        # 取出回覆內容
        lang_code = self.extract_deepseek_outputs(response.content)
        t1 = datetime.datetime.now()
        logging.info(f"detect_lang 偵測語言:{lang_code} 花費: {round((t1-t0).total_seconds(), 2)} seconds")
        return lang_code
    
    async def detect_zh_type(self, text: str):
        SIMP_CHARS = set("汉机龙后发台体里云万与电气车书广东门风")
        TRAD_CHARS = set("漢機龍後發臺體裡雲萬與電氣車書廣東門風")
        simp = sum(1 for ch in text if ch in SIMP_CHARS)
        trad = sum(1 for ch in text if ch in TRAD_CHARS)

        if simp > trad:
            return "zh-cn"
        elif trad > simp:
            return "zh-tw"

        # fallback
        if self.s2t.convert(text) != text:
            return "zh-cn"
        elif self.t2s.convert(text) != text:
            return "zh-tw"

        return "unknown"
    
    async def llm_total_summary(
        self,
        query: str,
        raw_str: str,
        syslang: str,
        session_id: str = "test_session_id",
        precomputed_sources: list[str] | None = None,
        trace_id: str | None = None,
        parent_span_id: str | None = None,
        parent_node_name: str | None = "execute_retrieve_node",
        run_config: dict | None = None,
    ):
        syslang = syslang.lower()
        # ⭐ 新增：判斷簡繁
        if syslang == "zh":
            zh_type = await self.detect_zh_type(query)

            if zh_type == "zh-cn":
                final_lang = "簡體中文"
            elif zh_type == "zh-tw":
                final_lang = "繁體中文(台灣地區)"
            else:
                final_lang = "繁體中文(台灣地區)"  # 預設
        else:
            lang_mapping = {
                "vi": "越南文",
                "en": "英文",
                "pt": "葡萄牙文",
                "es": "西班牙文"
            }
            final_lang = lang_mapping.get(syslang, "英文")
        logging.info(f"summary in {final_lang}")
        
        user_input = f"用戶問題：{query} /no_think"
        t0 = datetime.datetime.now()
        from common.langfuse_tracing import (
            build_langgraph_invoke_config,
            get_active_trace_context,
            langgraph_chain_observation,
            new_trace_id,
            resolve_chain_observation_id,
        )

        active_trace_id, active_parent_span_id = get_active_trace_context()
        resolved_trace_id = str(trace_id or active_trace_id or new_trace_id())
        resolved_parent_span_id = parent_span_id
        if not resolved_parent_span_id and parent_node_name and run_config:
            resolved_parent_span_id = resolve_chain_observation_id(
                run_config,
                node_name=parent_node_name,
            )
        if not resolved_parent_span_id:
            resolved_parent_span_id = active_parent_span_id
        inputs = {
            "query": user_input,
            "raw_str": raw_str,
            "syslang": syslang,
            "trace_id": resolved_trace_id,
            "precomputed_sources": list(precomputed_sources or []),
        }
        with langgraph_chain_observation(
            name="summary_parallel_graph",
            trace_id=resolved_trace_id,
            parent_span_id=resolved_parent_span_id,
            input={"query": query, "syslang": syslang},
            metadata={"session_id": session_id},
        ) as obs_ctx:
            config = build_langgraph_invoke_config(
                trace_id=str(obs_ctx["trace_id"]),
                parent_span_id=obs_ctx["parent_span_id"],
                session_id=session_id,
                trace_name="summary_parallel_graph",
                configurable={"retrive_service": self},
            )
            final_state = await summary_app.ainvoke(inputs, config=config)
        if syslang == "zh":
            if "簡體" in final_lang:
                final_state['answer'] = self.t2s.convert(str(final_state['answer']))
            else:
                final_state['answer'] = self.s2t.convert(str(final_state['answer']))
        final_state['answer'] = self._normalize_ref_tags(
            final_state.get('answer', ''),
            list(final_state.get('source') or [])
        )
        t1 = datetime.datetime.now()
        logging.info(f"llm_total_summary 花費: {round((t1-t0).total_seconds(), 2)} seconds")
        suggested = list(final_state.get("suggested_questions") or [])
        if not suggested:
            suggested = ["能否提供更多細節？", "還有哪些相關資料？", "下一步建議如何排查？"]
        response = LLMSummary(
            answer=final_state["answer"],
            source=final_state.get("source") or [],
            suggested_questions=suggested,
        )
        return response
    
    async def llm_combination(self, query:str, raw_str:str):
        
        t0 = datetime.datetime.now()
        
        
        system_prompt = compile_langfuse_prompt_or_fallback(
            self.langfuse_client,
            "llm_combination",
            fallback=(
                "你是一位專業的維修總結助理，以下是從維修資料中擷取的多筆摘要條目，每筆皆包含一段描述與對應的 ISSUE_ID。\n"
                "請根據【使用者問題】，協助檢查並合併描述相同或相似內容的條目，並依照【摘要合併規則】輸出條列式摘要。\n"
                "僅輸出合併後的條列式摘要，不要多餘解釋。"
            ),
        )
        user_input = f"""### 使用者問題\n{query} ### 原始維修條目\n{raw_str}\n---\n請依照上述規則進行整理： /no_think"""
        # 準備對話內容
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_input)
        ]
        response = await self._invoke_with_fallback(
            messages=messages,
            task_type="summary",
            gen_name="llm_combination",
        )
        # 取出回覆內容
        issue_summary_response = self.extract_deepseek_outputs(response.content)
        t1 = datetime.datetime.now()
        logging.info(f"llm_combination 花費: {round((t1-t0).total_seconds(), 2)} seconds")
        return issue_summary_response
    
    
    async def check_is_chitchat(self, query: str) -> bool:
        """
        判斷使用者輸入是否為閒聊。
        回傳 True 代表是閒聊（Chitchat），回傳 False 代表是專業技術問題。
        """
        t0 = datetime.datetime.now()
        
        
        system_prompt = compile_langfuse_prompt_or_fallback(
            self.langfuse_client,
            "check_is_chitchat",
            fallback=(
                "你是一位分類助手。請判斷使用者的輸入是「一般閒聊/禮貌性對話」還是「專業維修/技術諮詢」。\n"
                "如果是閒聊，請僅輸出：True；如果是專業技術諮詢，請僅輸出：False。嚴禁輸出任何解釋。"
            ),
        )

        user_input = f"使用者輸入：{query}\n---\n請依照規則回傳 True 或 False： /no_think"
        
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_input)
        ]

        try:
            response = await self._invoke_with_fallback(
                messages=messages,
                task_type="default",
                gen_name="check_is_chitchat",
                max_tokens=16,
            )
            raw_content = self.extract_deepseek_outputs(response.content).strip().lower()
            is_chitchat = "true" in raw_content
            t1 = datetime.datetime.now()
            logging.info(f"check_is_chitchat 判定結果: {is_chitchat}, 耗時: {round((t1-t0).total_seconds(), 2)} seconds")
            return is_chitchat
        except Exception:
            # 發生錯誤時預設為 False，進入專業分析流程較保險
            return False


    async def llm_chat(self, query: str):
        """
        當判定為閒聊時，呼叫此函數進行親切的回覆。
        """
        
        system_prompt = compile_langfuse_prompt_or_fallback(
            self.langfuse_client,
            "llm_chat",
            fallback="你是一位專業且親切的維修服務助手。",
        )
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"{query}\n/no_think")
        ]
        response = await self._invoke_with_fallback(
            messages=messages,
            task_type="default",
            gen_name="llm_chat",
        )
        return self.extract_deepseek_outputs(response.content)
   
    
    # 🌟 1. 幫函數增加 trace_id 參數，預設給 None 避免破壞其他舊有呼叫 🌟
    async def llm_direct_analysis(self, query: str, trace_id: str = None):
        t0 = datetime.datetime.now()
        result_str = ""
        
        # 1. 準備 Prompt (保持原樣不變)
        system_prompt = compile_langfuse_prompt_or_fallback(
            self.langfuse_client,
            "llm_direct_analysis",
            fallback=(
                "你是一位專業的機台維修與失效分析專家。請根據【使用者問題】及你的【專業領域知識】，直接給出原因分析。\n\n"
                "**[根據問題生成的大標題]**\n\n**一、核心原因**\n\n**1.[大類別名稱]**\n\n"
                "• [具體原因細節1]\n• [具體原因細節2]\n"
            ),
        )

        user_input = f"【使用者問題】：{query}\n /no_think"
        messages =[
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_input)
        ]

        try:
            response = await self._invoke_with_fallback(
                messages=messages,
                task_type="summary",
                trace_id=trace_id,
                gen_name="llm_direct_analysis",
            )
            result_str = self.extract_deepseek_outputs(response.content).strip()
        except Exception as exc:
            logging.critical(f"所有 LLM 服務均不可用: {exc}")
            result_str = "資料生成發生錯誤，請檢查推理伺服器狀態。"

        t1 = datetime.datetime.now()
        logging.info(f"llm_direct_analysis 完成，耗時: {round((t1-t0).total_seconds(), 2)} 秒")
        
        return result_str
    
    
    async def ae_sop_llm_systhsis(self, target_nodes, errorcode: str, max_concurrency: int = 5):
        t0 = datetime.datetime.now()
        
        # 1. 預先初始化工具 (只需要做一次)
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=300,
            chunk_overlap=50,
            separators=["\n\n", "\n", "。", ""]
        )
        
        ae_sop_system_prompt = compile_langfuse_prompt_or_fallback(
            self.langfuse_client,
            "ae_sop_synthesis",
            fallback=(
                "你是一位精確的技術文件數位化專家。目標錯誤代碼：{target_errorcode}\n"
                "請將輸入的技術表格內容轉換為結構化數據，不可添加文本未出現的資訊。"
            ),
            target_errorcode=errorcode,
        )
        prompt_template = ChatPromptTemplate.from_messages([
            ("system", ae_sop_system_prompt),
            ("human", "參考文件內容：\n{content} /not_think")
        ])

        # 2. 文本切分與初步過濾 (減少傳給 LLM 的無效片段)
        # 僅針對含有 errorcode 的 node 進行切分，或切分後過濾
        all_splits = text_splitter.split_documents(target_nodes)
        filtered_contents = [n.page_content for n in all_splits if errorcode in n.page_content]

        if not filtered_contents:
            return f"在文件中未找到關於 {errorcode} 的具體維修資訊。"

        # 3. 定義備援 LLM 清單
        llm_candidates = [self.vllm_general_llm, self.ollama_general_llm]
        
        final_causes = set()
        final_solutions = set()
        
        # 4. 嘗試執行 (備援機制)
        for idx, llm_instance in enumerate(llm_candidates):
            try:
                logging.info(f"嘗試使用第 {idx+1} 個 LLM 進行分析...")
                
                structured_llm = llm_instance.with_structured_output(ErrorTableAnalysis)
                chain = prompt_template | structured_llm
                semaphore = asyncio.Semaphore(max_concurrency)

                async def sem_task(content, current_chain):
                    async with semaphore:
                        return await current_chain.ainvoke({
                            "target_errorcode": errorcode,
                            "content": content
                        })

                # 併發執行任務
                tasks = [sem_task(content, chain) for content in filtered_contents]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # 解析結果
                for res in results:
                    if isinstance(res, ErrorTableAnalysis) and res.error_code == errorcode:
                        if res.cause: final_causes.add(res.cause)
                        if res.solution: final_solutions.add(res.solution)
                    elif isinstance(res, Exception):
                        logging.error(f"單一 Task 失敗: {res}")
                # 如果這次嘗試已經抓到數據，就跳出備援迴圈
                if final_causes:
                    break
            except Exception as e:
                logging.error(f"LLM 實例 {idx+1} 發生嚴重錯誤: {e}")
                if idx == len(llm_candidates) - 1: # 最後一個也掛了
                    return f"服務繁忙，無法處理 {errorcode} 的分析請求。"

        # 5. 生成最終回應
        if not final_causes:
            return f"未能從文中提取出 {errorcode} 的有效結構化資訊。"

        response_str = f"## 錯誤代碼：{errorcode} 分析報告\n\n"
        response_str += "### 可能原因\n" + "\n".join([f"{i}. {c}" for i, c in enumerate(sorted(final_causes), 1)])
        response_str += "\n\n### 處理措施\n" + "\n".join([f"{i}. {s}" for i, s in enumerate(sorted(final_solutions), 1)])
        t1 = datetime.datetime.now()
        logging.info(f"ae sop llm 併發分析總耗時: {round((t1-t0).total_seconds(), 2)} 秒")
        return response_str
    
    # 建立doc_node from path
    async def _get_docs_nodes(self, input_path: str) -> List[Document]:
        docs = []
        if os.path.isfile(input_path):
            loader = self._get_loader_for_file(input_path)
            docs.extend(loader.load())
        elif os.path.isdir(input_path):
            raise AssertionError("Not support DirectoryLoader")
        if not docs:
            logging.warning(f"No documents loaded from {input_path}")
            return []
        # 2. Splitting (切割階段)
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.rag_chunk_size,
            chunk_overlap=self.rag_chunk_overlap,
            separators=["\n\n", "\n", " ", ""] # 優先順序
        )
        # split_documents 會保留原本的 metadata (如 source file path)
        split_docs = text_splitter.split_documents(docs)
        # 過濾空內容
        docs_nodes = [doc for doc in split_docs if doc.page_content.strip()]
        logging.info(f"get docs and nodes complete, have {len(docs_nodes)} chunks")
        return docs_nodes

    def extract_deepseek_outputs(self, text: str):
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        return text
    
    def clean_text(self, text):
        # 移除非法 surrogate 字元
        return text.encode("utf-8", "ignore").decode("utf-8", "ignore")
    
    async def get_field_unique(self, index_name: str, field_str:str="metadata.basic_information.UPLOAD_DATE") -> list:
        """
        查詢數據中時間欄位的集合, 並且排序
        """
        if not self._es_repo.es.indices.exists(index=index_name):
            return []
        # aggs: 執行 terms 聚合，自動去重並排序
        query_body = {
            "size": 0, 
            "aggs": {
                "unique_dates": {
                    "terms": {
                        "field": field_str, 
                        "order": {"_key": "asc"}   # desc: 新到舊 / asc: 舊到新
                    }
                }
            }
        }
        
        # 若你的 es 是異步客戶端(AsyncElasticsearch)，請加上 await
        response = self._es_repo.es.search(
            index=index_name,
            body=query_body
        )
        # 提取 ES 整理好的去重與排序結果
        buckets = response.get('aggregations', {}).get('unique_dates', {}).get('buckets', [])
        existing_dates = [b['key'] for b in buckets]
        return existing_dates
    
    async def get_multi_fields_unique(self, index_name: str, fields: list) -> list:
        """
        查詢數據中多個欄位「組合」的唯一值集合
        :param fields: 例如 ["metadata.basic_information.UPLOAD_DATE", "metadata.node_type"]
        :return: list of tuples，例如 [("2026-03-18", "file"), ("2026-03-18", "manual")]
        """
        if not self._es_repo.es.indices.exists(index=index_name):
            return []

        # 組合 multi_terms 所需的 terms 列表
        terms_list = [{"field": f} for f in fields]

        # aggs: 執行 multi_terms 聚合
        query_body = {
            "size": 0, 
            "aggs": {
                "combined_unique": {
                    "multi_terms": {
                        "terms": terms_list,
                        "size": 10000,             # 放大回傳數量限制
                        "order": {"_key": "asc"}   # 依照組合的 key 進行排序
                    }
                }
            }
        }
        
        # 若你的 es 是異步客戶端，請加上 await
        response = self._es_repo.es.search(
            index=index_name,
            body=query_body
        )
        
        # 提取結果
        buckets = response.get('aggregations', {}).get('combined_unique', {}).get('buckets', [])
        
        # ES 回傳的 key 會是一個陣列，例如 ["2026-03-18", "file"]
        # 這裡將其轉換為 tuple 方便後續在 Python 中做 set 的差集運算或比對
        existing_combinations = [tuple(b['key']) for b in buckets]
        return existing_combinations
    
    def _filter_existing_ids(self, index_name: str, ids: list[str]) -> set:
        """
        輸入一堆 ID，回傳那些「已經存在」於 ES 的 ID 集合。
        """
        if not ids:
            return set()
        try:
            # 使用 mget 批次查詢 (比 search 快)
            # 注意：如果 index 根本不存在，這裡會報錯，要 catch 起來
            if not self._es_repo.es.indices.exists(index=index_name):
                return set()
            pass
            response = self._es_repo.es.mget(
                index=index_name,
                body={"ids": ids},
                _source=False # 我們不需要內容，只要知道有沒有
            )
            # 蒐集所有 found=True 的 ID
            existing_ids = {doc['_id'] for doc in response['docs'] if doc['found']}
            return existing_ids
        except Exception as e:
            logging.warning(f"檢查 ID 存在失敗 (可能 Index 還沒建): {e}")
            return set()
    
    async def is_meaningful_content(self, content: str) -> bool:
        # 1. 預處理：先移除結構化標題（如 [功能描述] 等），以及空白換行
        clean_text = re.sub(r'\[[^\]]+\]', '', content)
        filtered_text = re.sub(r'\s+', '', clean_text)
        
        # 2. 基礎門檻檢查：如果扣除標題後，實質文字（包含中英數）太短（例如小於 5 個字），直接視為無意義
        if len(filtered_text) < 5:
            return False
            
        # 3. 複雜語意交給 LLM：調整 Prompt，明確定義「工程/不良分析情境」的有意義內容
        system_prompt = compile_langfuse_prompt_or_fallback(
            self.langfuse_client,
            "is_meaningful_content",
            fallback=(
                "你是一個工廠與工程分析系統的內容審查官。請判斷使用者輸入的結構化文字是否包含實質工程內容。\n"
                "請嚴格只輸出 True 或 False，不要包含任何額外解釋。"
            ),
        )
        
        usr_input = f"資料如下：\n{content}"
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=usr_input)
        ]
        
        response = await self.vllm_general_llm.ainvoke(messages)
        output = self.extract_deepseek_outputs(response.content).strip().lower()
        
        return 'true' in output
    
    async def rewrite_content(self, content: str, system_prompt:str) -> str:
        usr_input = f"資料如下：{content}"
        messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=usr_input)
            ]
        response = await self.vllm_general_llm.ainvoke(messages)
        new_query = self.extract_deepseek_outputs(response.content)
        return new_query
    
    
    def _normalize_faca_metadata(self, metadata: dict | None) -> dict:
        metadata = dict(metadata or {})
        content_type = metadata.get("content_type", "general")
        if content_type == "user_document":
            metadata.setdefault("content_type", "user_document")
            for key in ("faca_relevant", "has_solution", "has_root_cause", "has_action", "page_type"):
                metadata.pop(key, None)
            return metadata
        # 企業 FACA 索引：ES mapping 使用 integer 0/1（見 ae_graphs.faca_relevant_filter）
        metadata.setdefault("faca_relevant", 0)
        metadata.setdefault("content_type", "general")
        metadata.setdefault("has_solution", 0)
        metadata.setdefault("has_root_cause", 0)
        metadata.setdefault("has_action", 1 if metadata.get("has_solution") else 0)
        metadata.setdefault("page_type", "")
        return metadata
    
    async def build_nodes(self,
                    index_name:str,
                    input_path:str=None,
                    text_content:str=None,
                    node_type:str=None,
                    rewrite_system_prompt:str=None,
                    use_rewrited:bool=False,
                    metadata:dict=None,
                    score:float=None,
                    update:bool=False):
        metadata = self._normalize_faca_metadata(metadata)
        # 排除空字串
        if text_content is not None:
            if text_content.strip() == '':
                return []
        # meaningful_bool = await self.is_meaningful_content(content=text_content)
        # if not meaningful_bool:
        #     return []
        # else:
        #     print(text_content)
        vector_store = self._get_vector_store(index_name=index_name)
        ImagePath = None
        pdf_path = None
        raw_input_path = input_path
        all_nodes = []
        if (raw_input_path is None) and (text_content is None):
            raise ValueError("input_path 或 text_content ，須擇一指定")
        # 只有文件
        elif (raw_input_path is not None) and (text_content is None):
            if ".pdf" in input_path:
                ImagePath = build_pdf_image(file_path=input_path)
            else:
                pdf_path = file2pdf(raw_input_path)
                ImagePath = build_pdf_image(file_path=pdf_path)
                input_path = pdf_path # replace
            docs_nodes = await self._get_docs_nodes(input_path=input_path)
            # 刪除前面創造的檔案
            if pdf_path:
                if os.path.exists(pdf_path):
                    os.remove(pdf_path)
            if not docs_nodes:
                return []
            # 1. 先對所有節點進行清理並預生成 ID (還不跑 LLM), 這一步非常快，因為只是字串處理
            all_ids = []
            for n in docs_nodes:
                n.page_content = self.clean_text(n.page_content)
                chunk_id = generate_chunk_id(n.page_content) # generate_chunk_id 是基於原始內容
                n.metadata['id'] = chunk_id
                if metadata:
                    n.metadata.update(metadata)
                all_ids.append(chunk_id)
            # 2. 批次詢問 ES：這些 ID 誰已經有了？
            existing_ids_set = await asyncio.to_thread(self._filter_existing_ids, index_name, all_ids)
            if len(existing_ids_set)>0:
                logging.info(f"總共 {len(all_ids)} 個區塊，發現 {len(existing_ids_set)} 個已存在，將跳過處理。")
            # 3. 篩選：只保留 ES 裡沒有的
            new_nodes = [n for n in docs_nodes if n.metadata['id'] not in existing_ids_set]
            # 如果全部都做過了，直接結束
            if not new_nodes:
                return []
            #############
            for n in new_nodes:
                if n.page_content.strip() == "":
                    raise AssertionError("node.text.strip() empty")
                n.page_content = self.clean_text(n.page_content) 
                chunk_id = generate_chunk_id(n.page_content) # 以清洗過後的原始內容產生id
                n.metadata['id'] = chunk_id 
                if ImagePath:
                    n.metadata['file_name'] = os.path.basename(raw_input_path)
                    if os.path.exists(raw_input_path):
                        n.metadata['file_path'] = raw_input_path
                    else:
                        n.metadata['file_path'] = ""
                    try:
                        static_url = ImagePath[int(n.metadata['page_label']) - 1]
                    except Exception as e:
                        static_url = "0"
                    n.metadata.update({"page_snapshot":static_url})
                n.metadata.update({"node_type":node_type})
                n.metadata.update({"score":score})
                rewrited_content = None
                if rewrite_system_prompt:
                    rewrited_content = await self.rewrite_content(content=n.page_content, system_prompt=rewrite_system_prompt)
                n.metadata.update({"raw_content":n.page_content})
                n.metadata.update({"rewrite_content":rewrited_content})
                # 若要用改寫的內容做embedding
                if use_rewrited:
                    n.page_content = rewrited_content
            all_nodes.extend(docs_nodes)
        # 只有文字
        elif (raw_input_path is None) and (text_content is not None):
            text_content = self.clean_text(str(text_content)) 
            chunk_id = generate_chunk_id(text_content)
            # 非更新模式:  做過得跳過
            if not update:
                existing_ids_set = await asyncio.to_thread(
                    self._filter_existing_ids, index_name, [chunk_id]
                )
                if chunk_id in existing_ids_set:
                    logging.info(f"Exist chunk_id in {index_name}, pass...{chunk_id}")
                    return []
            rewrited_content = None
            if rewrite_system_prompt:
                rewrited_content = await self.rewrite_content(content=text_content, system_prompt=rewrite_system_prompt)
            n = Document(page_content=str(text_content), metadata=metadata)
            n.metadata.update({"id":chunk_id })
            n.metadata.update({"score":score})
            n.metadata.update({"node_type":node_type})
            n.metadata.update({"raw_content":n.page_content})
            n.metadata.update({"rewrite_content":rewrited_content})
            # 若要用改寫的內容做embedding
            if use_rewrited:
                n.page_content = rewrited_content
            all_nodes.append(n)
        
        # 先確定有沒有index
        if not self._es_repo.es.indices.exists(index=index_name):
            pass
            index_schema = self.generate_es_schema(all_nodes[0].metadata)
            self.create_index(index_schema=index_schema,index_name=index_name)
        pass
        # To elasticsearch
        uuids = [n.metadata["id"] for n in all_nodes]
        try:
            await asyncio.to_thread(
                vector_store.add_documents,
                documents=all_nodes,
                ids=uuids,
            )
        except BulkIndexError as e:
            for error in e.errors:
                logging.info(f"詳細錯誤原因: {error}") # 這會告訴你具體是哪個 document 哪裡出錯
            raise e
        return all_nodes

    async def get_existing_node(self, chunk_id):
            all_nodes = []
            try:
                hits = await self.get_nodes_from_es_by_id(index_name=INDEX_NAME, chunk_id=chunk_id)
                if hits:
                    for hit in hits:
                        source = hit["_source"]
                        node_type=source['metadata']['node_type']
                        ISSUE_ID=source.get('metadata', {}).get('basic_information', {}).get('ISSUE_ID', 'Not found Issue ID')

                        nodes = await self.handle_input(chunk_id=ISSUE_ID, input_path=None, text_content=source["content"], metadata=source["metadata"], node_type=node_type, score=source)
                        all_nodes.extend(nodes)
                    return all_nodes
                else:
                    logging.info(f"Node {chunk_id} not found in Elasticsearch.")
                    return None
            except Exception as e:
                logging.error(f"Error retrieving node {chunk_id} from Elasticsearch: {str(e)}")
                return None

    async def drop_index(self, index_name: str = None):
        if self._es_repo.es.indices.exists(index=index_name):
            self._es_repo.es.indices.delete(index=index_name)
            logging.info(f"成功刪除索引：{index_name}")
            return True
        else:
            logging.info(f"索引 {index_name} 不存在，無需刪除。")
            return False
    
    def generate_es_schema(self, sample_metadata: Dict[str, Any], text_field: str = "text") -> dict:
        """
        根據傳入的 metadata 樣本，遞迴生成 Elasticsearch 的 Mapping Schema。
        """
        def map_field_type(value):
            # 1. 處理字典型態 (遞迴核心)
            if isinstance(value, dict):
                # a = {k: map_field_type(v) for k, v in value.items() if v is not None}
                a = {}
                for k, v in value.items():
                    if v is not None:
                        a[k] = map_field_type(v)
                    else:
                        a[k] = {"type": "keyword", "ignore_above": 1024}
                return {
                    "properties": a
                }
            # 2. 處理字串型態
            elif isinstance(value, str):
                return {
                    "type": "keyword",
                    "ignore_above": 1024
                }
            # 3. 處理數字型態
            elif isinstance(value, int):
                return {"type": "integer"}
            elif isinstance(value, float):
                return {"type": "float"}
            # 4. 處理布林值
            elif isinstance(value, bool):
                return {"type": "boolean"}
            # 5. 處理陣列 (ES 的 keyword 陣列不需特別聲明，用 keyword 即可)
            elif isinstance(value, list):
                if len(value) > 0 and isinstance(value[0], dict):
                    # 建立一個聯集字典，確保所有出現在 list 字典中的 key 都被定義到
                    combined_properties = {}
                    for item in value:
                        if isinstance(item, dict):
                            for k, v in item.items():
                                if k not in combined_properties:
                                    combined_properties[k] = map_field_type(v)
                                elif v is not None and combined_properties[k].get("type") == "keyword":
                                    # 如果之前是 None (預設 keyword)，現在有值了，重新判定
                                    combined_properties[k] = map_field_type(v)
                    
                    return {
                        "type": "nested", # 建議改用 nested，處理物件陣列最穩
                        "properties": combined_properties
                    }
                else:
                    pass
                    return {
                        "type": "keyword",
                        "ignore_above": 1024
                    }
            # 6. 預設兜底
            else:
                return {"type": "keyword"}

        # 生成 metadata 下的所有屬性
        metadata_properties = {key: map_field_type(value) for key, value in sample_metadata.items()}

        # 組裝最終 Schema
        schema = {
            "mappings": {
                "properties": {
                    text_field: {
                        "type": "text"
                    },
                    "metadata": {
                        "properties": metadata_properties
                    }
                }
            }
        }
        return schema
    
    def create_index(self, index_schema:object,index_name:str):
        self._es_repo.es.indices.create(index=index_name, body=index_schema)

class RetriveService(RAGService):
    _QUERY_EXTRACTION_CACHE_TTL_SECONDS = 300
    # 合成 ISSUE_ID 前缀（格式尽量贴近 SFCA60122028C：前缀 + 10位十六进制）
    ISSUE_ID_PREFIX_SOP = "SOP"       # ae_sop_file_* 来源
    ISSUE_ID_PREFIX_OTHER = "DEVICE_MANUAL"   # file_* 等其他手册来源
    ISSUE_ID_SUFFIX_LEN = 10
    SYNTHETIC_ISSUE_ID_PREFIXES = (ISSUE_ID_PREFIX_SOP, ISSUE_ID_PREFIX_OTHER, "SOP_FILE_")

    def __init__(self, **deps) -> None:
        super().__init__(**deps)
        self._es_client = deps['es_repo'].es
        self._query_extraction_cache: dict[str, tuple[float, QueryExtraction]] = {}
        self._query_extraction_inflight: dict[str, asyncio.Task] = {}
    
    
    async def query_rewrite_keyword(self, query: str) -> str:
        prompt = f"""你是一位語意理解助手，請協助我從每一句話中提取「部件 (Component)」與「症狀(Symptom)」兩個核心, 並遵守以下原則:
        * 保留有意義的字
        * '原因'和'解決方案'不是核心, 也不是有意義的字。
        * 僅輸出句子的核心意義,不要有其他解釋。
        * 請輸出簡體與繁體的詞 例如 '螺絲' 、 '螺丝'
        Ex1: cover to bezel gap out spec -> 輸出： cover bezel gap, out spec
        Ex2: 缝大 -> 輸出：缝大
        Ex3: 徐熊(624170/526728)的主管是誰? -> 輸出： 徐熊
        Ex4: typeC 不良原因 -> 輸出： typeC 不良
        Ex5: 荧屏问题 -> 輸出： 荧屏, 螢屏
        Ex6: JDB47 no power -> 輸出： JDB47, no power
        Ex7: 螺絲 -> 輸出： 螺丝, 螺絲
        Ex8: 螺絲 -> 輸出： 螺丝, 螺絲
        句子：{query}
        """
        for i  in range(3):
            try:
                logging.info(f"query_rewrite_keyword({i+1})")
                t0 = datetime.datetime.now()
                new_query = self.llm.complete(prompt).text.strip()
                new_query = self.extract_deepseek_outputs(new_query)
                t1 = datetime.datetime.now()
                logging.info(f"Cost {(t1-t0).total_seconds()} seconds")
                return new_query
            except Exception as e:
                logging.info(f"{e}")
                pass
        raise AssertionError("Ollama timeout")
    
    # 更新附件檔案路徑
    def update_file_path(self, file_path):
        return file_path.replace("/app/rag_doc/iap/", "/reports/")
    
    
    # 移除所有私用區 (PUA) 符號
    def clean_pdf_text(self, text: str) -> str:
        return re.sub(r'[\ue000-\uf8ff\n\r]', '', text)
    
    async def full_text_search(self, prompt, tokens, index_name, size=100):
        # 各種full-text-technique
        logging.info(f"full_text_search\nprompt:{prompt}\ntokens:{tokens}")
        full_text_hits = []
        # wildcard:不分詞比對(不會被ES被分詞影響, 但會被LLM分詞影響)
        if len(full_text_hits) == 0 and len(tokens) != 0:
            should_clauses = [
            {"wildcard": {"content.raw": f"*{token}*"}}
            for token in tokens
            ]
            query_body = {
                    "query": {
                        "bool": {
                            "should": should_clauses,
                            "minimum_should_match": 1
                        }
                    },
                    "_source": True,
                    "size": size
                }
            pass
            wildcard_response = await self._es_client.search(
                index=index_name,
                body=query_body
            )
            wildcard_hits = wildcard_response['hits']['hits']
            #TEST!
            for idx, hit in enumerate(wildcard_hits):
                content = hit['_source'].get('content', '')
                score = sum(0.5 for token in tokens if token in content)
                hit['_score'] = score
            if len(wildcard_hits) != 0:
                logging.info(f"wildcard search find:{len(wildcard_hits)}")
                wildcard_hits.sort(key=lambda x: x["_score"], reverse=True)
                full_text_hits.extend(wildcard_hits)
        # multi_match:分詞比對 需要照順序(會ES被分詞影響)
        if len(full_text_hits) == 0:
            multi_match_body = {
            "explain": True,
                "query": {
                    "multi_match": {
                    "query": prompt,
                    "type": "phrase",
                    "fields": ["metadata", "content"]
                    }
                },
                "size": size,
                "sort": [{ "_score": { "order": "desc" } }]
            }
            try:
                multi_match_response = await self._es_client.search(
                index=index_name,
                body=multi_match_body)
            except Exception as e:
                print(e)
                pass
            multi_match_hits = multi_match_response["hits"]["hits"]
            if len(multi_match_hits) != 0:
                logging.info(f"multi_match find:{len(multi_match_hits)}")
                multi_match_hits.sort(key=lambda x: x["_score"], reverse=True)
                full_text_hits.extend(multi_match_hits)
        # match:分詞比對 不需要照順序(會被ES分詞影響)
        if len(full_text_hits) == 0:
            match_body = {
            "explain": True,
                "query": {
                    "match": {
                    "content": {
                        "query": prompt,
                        "fuzziness": "AUTO"
                    }
                    }
                },
                "size": size,
                "sort": [{ "_score": { "order": "desc" } }]
            }
            match_response = await self._es_client.search(index=index_name, body=match_body)
            match_hits = match_response["hits"]["hits"]
            if len(match_hits) != 0:
                logging.info(f"match search\n find:{len(match_hits)}")
                match_hits.sort(key=lambda x: x["_score"], reverse=True)
                full_text_hits.extend(match_hits)
        
        max_semantic_score = max([hit["_score"] for hit in full_text_hits], default=1.0)
        for hit in full_text_hits:
            hit["_normalized_score"] = hit["_score"] / max_semantic_score
 
        return full_text_hits
    
    async def lexical_search(self, query: str, top_k: int, index_name = "iap"):
        lexical_results = await self._es_repo.es.search(
            index=index_name,
            body={
                "query":{
                    "multi_match":{
                        "query": query,
                        "fields": ["content"],
                    }
                },            
                "size": top_k,
            },
            source_excludes=["description_vector"],
        )
        lexical_hits = lexical_results["hits"]["hits"]
        max_bm25_score = max([hit["_score"] for hit in lexical_hits], default=1.0)
        #Normalize lexical scores
        for hit in lexical_hits:
            hit["_normalized_score"] = hit["_score"] / max_bm25_score
        return lexical_hits 
    
    async def semantic_search(self, query: str, top_k: int, index_name="iap"):
        query_embedding = self.get_embedding(query)
        script_query ={
            "script_score": {
                "query": {"match_all":{}},
                "script":{
                    "source": "cosineSimilarity(params.embedding, 'embedding') + 1.0",
                    "params": {"embedding": query_embedding},
                }
        
            }
        }
        semantic_results = await self._es_repo.es.search(
            index=index_name,
            body={
                "query": script_query,
                "size": top_k,
                "sort": [{"_score": {"order": "desc"}}]
            },
        )
        semantic_hits = semantic_results["hits"]["hits"]
        max_semantic_score = max([hit["_score"] for hit in semantic_hits], default=1.0)
        if max_semantic_score == 0:
            raise AssertionError(f"max_semantic_score:{max_semantic_score}")
        # Normalize semantic scores
        for hit in semantic_hits:
            hit["_normalized_score"] = hit["_score"] / max_semantic_score
        return semantic_hits
    
    
    async def semantic_search_v2(self,index_name:str ,query: str, top_k: int, my_filter:dict=None):
        """
        語意向量檢索（ES knn）。

        - 使用 embedding 對 `vector` 欄位做近似最近鄰搜尋
        - `my_filter` 可限制在 lexical 候選 doc id 或 metadata 條件內搜尋
        - 排除 vector 等大欄位，避免傳輸過慢
        """
        top_k = max(1, int(top_k or 1))
        num_candidates = max(top_k, min(top_k * 5, 100))
        query_embedding = await asyncio.to_thread(self.embedding_model.embed_query, query)
        query_body = {
            "knn": {
                "field": "vector",
                "query_vector": query_embedding,
                "k": top_k,
                "num_candidates": num_candidates,
            },
            "size": top_k,
            "track_total_hits": False,
            "_source": {
                "excludes": ["embedding", "description_vector", "vector"]
            },
        }
        if my_filter:
            query_body["knn"]["filter"] = my_filter
        semantic_response = await asyncio.to_thread(
            self._es_repo.es.search,
            index=index_name,
            body=query_body,
        )
        results = []
        hits = semantic_response["hits"]["hits"]
        for hit in hits:
            source = hit.get("_source") or {}
            metadata = dict(source.get("metadata") or {})
            metadata.setdefault("id", hit.get("_id"))
            n = Document(page_content=str(source.get("text") or source.get("content") or ""), metadata=metadata)
            n.metadata["semantic_score"] = float(hit.get("_score") or 0)
            results.append(n)
        return results
    
    async def lexical_search_v2(self, index_name:str, query: str, top_k: int, my_filter: dict = None):
        """
        關鍵字檢索（ES BM25 multi_match）。

        - 查詢欄位：`text`, `metadata.raw_content`, `metadata.rewrite_content`
        - 用於第一階段 keyword prefilter，快速縮小候選集合
        """
        top_k = max(1, int(top_k or 1))
        query_body = {
            "query": {
                "bool": {
                    "must": [
                        {
                            "multi_match": {
                                "query": query,
                                "fields": ["text", "metadata.raw_content", "metadata.rewrite_content"],
                                "type": "best_fields",
                            }
                        }
                    ],
                }
            },
            "size": top_k,
            "track_total_hits": False,
            "_source": {
                "excludes": ["embedding", "description_vector", "vector"]
            },
        }
        if my_filter:
            query_body["query"]["bool"]["filter"] = [my_filter]

        lexical_response = await asyncio.to_thread(
            self._es_repo.es.search,
            index=index_name,
            body=query_body,
        )
        hits = lexical_response["hits"]["hits"]
        results = []
        for hit in hits:
            source = hit.get("_source") or {}
            metadata = dict(source.get("metadata") or {})
            metadata.setdefault("id", hit.get("_id"))
            n = Document(page_content=str(source.get("text") or source.get("content") or ""), metadata=metadata)
            score = float(hit.get("_score") or 0)
            n.metadata["lexical_score"] = score
            results.append(n)
        return results
    
    _QUERY_EXTRACTION_PROMPT = """你是一位工業設備故障檢索助手。請從使用者問題中同時提取「檢索關鍵字」與「SOP 實體」。

【檢索關鍵字】
- error_codes: 錯誤代碼、告警編號、故障代號（保留原文，形式不限，不可拆散）
- symptoms: 失效模式、故障現象、異常描述、告警名稱
- entities: 設備名稱、零件、型號、廠牌、工站等實體

【SOP 實體】（各欄位最多填一個最可能的值，無則留空字串）
- brand_en: 英文廠牌
- brand_zh: 中文廠牌
- model: 設備型號
- errorcode: 錯誤代碼
- category: 設備類別

規則：
1. 只提取問題中明確出現或可合理推斷的核心詞，排除「發生原因」「怎麼處理」「是什麼」等提問套話
2. 若分詞候選字被切碎，請合併回完整詞彙
3. 錯誤代碼形式不限，必須完整保留
4. 每個 list 分類最多 5 個詞
5. 禁止輸出思考過程或解釋，只回傳結構化欄位
"""

    _QUERY_EXTRACTION_MAX_TOKENS = 512

    _RETRIEVAL_STOPWORDS = {
        "原因", "處理", "处理", "解决", "解決", "如何", "怎麼", "怎么", "什么", "什麼",
        "发生", "發生", "方法", "是什么", "什麼是", "的", "与", "與", "和", "请", "請",
        "吗", "嗎", "什么", "哪些", "请问", "請問",
    }

    def _normalize_retrieval_token(self, token: str) -> str:
        return re.sub(r"\s+", " ", str(token or "").strip())

    def _query_extraction_cache_key(self, query: str) -> str:
        normalized = re.sub(r"\s+", " ", str(query or "").strip().lower())
        return hashlib.md5(normalized.encode("utf-8")).hexdigest()

    def _coerce_query_extraction(
        self,
        query_extraction: QueryExtraction | RetrievalKeywordExtraction | dict | None,
    ) -> QueryExtraction | None:
        if query_extraction is None:
            return None
        if isinstance(query_extraction, QueryExtraction):
            return query_extraction
        if isinstance(query_extraction, RetrievalKeywordExtraction):
            return QueryExtraction(**query_extraction.model_dump())
        if isinstance(query_extraction, dict):
            return QueryExtraction(**query_extraction)
        return None

    def _merge_retrieval_keywords(
        self,
        extraction: RetrievalKeywordExtraction | QueryExtraction,
    ) -> list[str]:
        """
        合併 LLM 分類結果為 flat keyword list。

        合併順序：error_codes → symptoms → entities（錯誤碼優先）
        並自動補充 OpenCC 簡繁變體，提升跨索引命中率。
        """
        merged: list[str] = []
        for group in (extraction.error_codes, extraction.symptoms, extraction.entities):
            for raw_token in group:
                token = self._normalize_retrieval_token(raw_token)
                if len(token) <= 1 or token in self._RETRIEVAL_STOPWORDS:
                    continue
                if any(token in existing and token != existing for existing in merged):
                    continue
                if token not in merged:
                    merged.append(token)
                for converted in (self.s2t.convert(token), self.t2s.convert(token)):
                    converted = self._normalize_retrieval_token(converted)
                    if (
                        converted
                        and len(converted) > 1
                        and converted not in merged
                        and converted not in self._RETRIEVAL_STOPWORDS
                    ):
                        merged.append(converted)
        return merged[:12]

    def _query_extraction_llm_label(self, llm: Any) -> str:
        return getattr(llm, "model_name", None) or getattr(llm, "model", None) or type(llm).__name__

    def _heuristic_query_extraction(
        self,
        query: str,
        jieba_tokens: list[str] | None = None,
    ) -> QueryExtraction:
        """LLM 不可用時，用分詞與規則從問題中抽出檢索關鍵字。"""
        raw_query = str(query or "").strip()
        tokens = [
            self._normalize_retrieval_token(token)
            for token in (jieba_tokens or [])
        ]
        tokens = [
            token for token in tokens
            if token and len(token) > 1 and token not in self._RETRIEVAL_STOPWORDS
        ]

        error_codes: list[str] = []
        for pattern in (
            r"[Ee]\d+(?:\.\d+)?",
            r"0x[0-9A-Fa-f]+",
            r"\b\d{2,4}\.\d+",
        ):
            for match in re.findall(pattern, raw_query):
                normalized = str(match).strip()
                if normalized and normalized not in error_codes:
                    error_codes.append(normalized)

        error_code_set = {code.upper() for code in error_codes}
        entities: list[str] = []
        symptoms: list[str] = []
        symptom_markers = ("异常", "異常", "故障", "报警", "警報", "失效", "異響", "异响")

        for token in tokens:
            if token.upper() in error_code_set:
                continue
            if any(marker in token for marker in symptom_markers):
                if token not in symptoms:
                    symptoms.append(token)
            elif token not in entities:
                entities.append(token)

        return QueryExtraction(
            error_codes=error_codes[:5],
            symptoms=symptoms[:5],
            entities=entities[:5],
            errorcode=error_codes[0] if error_codes else "",
        )

    def _parse_query_extraction_response(self, content: str) -> QueryExtraction:
        cleaned = self.extract_deepseek_outputs(str(content or "")).strip()
        if not cleaned:
            raise ValueError("empty query extraction response")
        try:
            payload = self.extract_first_json(cleaned)
        except ValueError:
            payload = json.loads(cleaned)
        if isinstance(payload, QueryExtraction):
            return payload
        if isinstance(payload, dict):
            return QueryExtraction(**payload)
        raise ValueError(f"unexpected query extraction payload: {type(payload)}")

    async def _llm_extract_query(
        self,
        query: str,
        jieba_tokens: list[str] | None = None,
    ) -> QueryExtraction:
        token_hint = ""
        if jieba_tokens:
            token_hint = f"\n分詞候選字: {jieba_tokens}"
        messages = [
            SystemMessage(content=self._QUERY_EXTRACTION_PROMPT),
            HumanMessage(content=f"使用者問題: {query}{token_hint}\n/no_think"),
        ]
        # query_extraction 先嘗試結構化輸出；若失敗再走一般輸出 JSON 解析。
        # 兩段都透過統一 gateway 控制 timeout / circuit breaker / fallback。
        try:
            response = await self._invoke_with_fallback(
                messages=messages,
                task_type="query_extraction",
                max_tokens=self._QUERY_EXTRACTION_MAX_TOKENS,
            )
            # 某些 provider 對 with_structured_output 支援不穩定，因此先嘗試解析 JSON。
            return self._parse_query_extraction_response(getattr(response, "content", ""))
        except Exception as exc:
            logging.warning("query extraction json path failed: %s", exc)
        try:
            llm = self._bind_llm(
                self.vllm_general_llm,
                task_type="query_extraction",
                max_tokens=self._QUERY_EXTRACTION_MAX_TOKENS,
                fallback_used=False,
                selected_model=self._llm_label(self.vllm_general_llm),
            )
            structured = llm.with_structured_output(QueryExtraction)
            result = await structured.ainvoke(messages)
            if isinstance(result, QueryExtraction):
                self._mark_primary_success("query_extraction")
                return result
        except Exception as exc:
            if self._is_retryable_llm_error(exc):
                self._mark_primary_failure("query_extraction")
            logging.warning("query extraction structured path failed: %s", exc)
            try:
                llm = self._bind_llm(
                    self.ollama_general_llm,
                    task_type="query_extraction",
                    max_tokens=self._QUERY_EXTRACTION_MAX_TOKENS,
                    fallback_used=True,
                    selected_model=self._llm_label(self.ollama_general_llm),
                )
                structured = llm.with_structured_output(QueryExtraction)
                result = await structured.ainvoke(messages)
                if isinstance(result, QueryExtraction):
                    return result
            except Exception as fallback_exc:
                logging.warning("query extraction structured fallback failed: %s", fallback_exc)

        fallback = self._heuristic_query_extraction(query, jieba_tokens=jieba_tokens)
        logging.info("query extraction using heuristic fallback: %s", fallback.model_dump())
        return fallback

    async def get_query_extraction(
        self,
        query: str,
        jieba_tokens: list[str] | None = None,
    ) -> QueryExtraction:
        """
        取得（或复用）LLM 結構化提取結果。

        優先順序：
        1. TTL 快取命中（預設 300 秒）
        2. 同 query 進行中的 inflight task（避免並行重複呼叫 LLM）
        3. 新建 LLM 結構化提取
        """
        raw_query = str(query or "").strip()
        if not raw_query:
            return QueryExtraction()

        cache_key = self._query_extraction_cache_key(raw_query)
        now = time.monotonic()
        cached = self._query_extraction_cache.get(cache_key)
        if cached and now - cached[0] < self._QUERY_EXTRACTION_CACHE_TTL_SECONDS:
            return cached[1]

        inflight = self._query_extraction_inflight.get(cache_key)
        if inflight is not None:
            return await inflight

        task = asyncio.create_task(self._llm_extract_query(raw_query, jieba_tokens=jieba_tokens))
        self._query_extraction_inflight[cache_key] = task
        try:
            result = await task
            self._query_extraction_cache[cache_key] = (time.monotonic(), result)
            return result
        finally:
            self._query_extraction_inflight.pop(cache_key, None)

    async def _llm_extract_retrieval_keywords(self, query: str) -> QueryExtraction:
        return await self.get_query_extraction(query)

    async def extract_retrieval_keywords(self, query: str) -> list[str]:
        """使用 LLM 從使用者輸入提取主要語意關鍵字，供 lexical 檢索使用。"""
        raw_query = str(query or "").strip()
        if not raw_query:
            return []

        extraction = await self.get_query_extraction(raw_query)
        keywords = self._merge_retrieval_keywords(extraction)
        if keywords:
            return keywords
        fallback = self._strip_retrieval_noise(raw_query)
        return [fallback] if fallback else []

    async def build_retrieval_queries(self, query: str) -> list[str]:
        """產生召回用 query variants；僅用於檢索，不直接交給 LLM 回答。"""
        extraction = await self.get_query_extraction(query)
        keywords = self._merge_retrieval_keywords(extraction)
        return self.build_lexical_queries(keywords, query, extraction)

    def build_lexical_queries(
        self,
        keywords: list[str],
        prompt: str,
        extraction: RetrievalKeywordExtraction | QueryExtraction | None = None,
    ) -> list[str]:
        """
        組成「單一」lexical query（為了檢索速度，不再拆多個 variant）。

        作法：去除提問套話/冗詞後，將核心關鍵字（錯誤碼 + 症狀/實體）合併成
        一個查詢字串，只打一次 ES BM25。
        """
        if not keywords:
            return self._fallback_retrieval_queries(prompt)

        if extraction:
            error_codes = [
                self._normalize_retrieval_token(token)
                for token in extraction.error_codes
                if self._normalize_retrieval_token(token)
            ]
            symptom_tokens = [
                self._normalize_retrieval_token(token)
                for token in extraction.symptoms + extraction.entities
                if self._normalize_retrieval_token(token)
            ]
        else:
            error_codes = keywords[:2]
            symptom_tokens = keywords[2:]

        merged_tokens = [token for token in dict.fromkeys(error_codes[:2] + symptom_tokens[:6]) if token]
        single_query = " ".join(merged_tokens).strip() or " ".join(keywords[:8]).strip()
        return [single_query] if single_query else self._fallback_retrieval_queries(prompt)

    def _fallback_retrieval_queries(self, prompt: str) -> list[str]:
        raw_query = str(prompt or "").strip()
        if not raw_query:
            return []
        return [raw_query]

    def _strip_retrieval_noise(self, prompt: str) -> str:
        cleaned = str(prompt or "").strip()
        noise_phrases = (
            "的发生原因与处理方法是什么", "發生原因與處理方法是什麼",
            "的发生原因", "發生原因", "的处理方法", "處理方法",
            "是什么原因", "是什麼原因", "怎么办", "怎麼辦", "如何處理", "如何处理",
            "是什么", "什麼", "吗", "嗎", "？", "?",
        )
        for phrase in noise_phrases:
            cleaned = cleaned.replace(phrase, " ")
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned or str(prompt or "").strip()

    def build_semantic_query(self, prompt: str, keywords: list[str] | None = None) -> str:
        """
        語意搜尋 query。

        優先使用合併後關鍵字（去除提問套話），讓 embedding 聚焦在
        錯誤碼、失效模式、設備實體等核心語意。
        """
        keyword_list = keywords or []
        if keyword_list:
            return " ".join(keyword_list[:8]).strip()
        return self._strip_retrieval_noise(prompt)

    def _dedupe_hits_by_id(self, hits: list, top_k: int, score_key: str = "lexical_score") -> list:
        """多個 lexical query variant 合併後，依 doc id 去重並保留最高分。"""
        best_hits = {}
        for hit in hits:
            doc_id = hit.metadata.get("id")
            if not doc_id:
                continue
            score = float(hit.metadata.get(score_key) or 0)
            if doc_id not in best_hits or score > float(best_hits[doc_id].metadata.get(score_key) or 0):
                best_hits[doc_id] = hit
        return sorted(
            best_hits.values(),
            key=lambda item: float(item.metadata.get(score_key) or 0),
            reverse=True,
        )[:top_k]

    def _is_retrieval_noise(self, text: str) -> bool:
        lowered = str(text or "").lower()
        noise_keywords = ("目录", "目錄", "table of contents", "一览表", "一覽表", "版本", "修訂", "修订", "索引")
        if any(keyword in lowered for keyword in noise_keywords):
            return True
        compact = re.sub(r"\s+", "", lowered)
        return len(compact) < 12

    def _filter_semantic_noise(self, hits: list, top_k: int) -> list:
        """
        semantic fallback 專用噪音過濾。

        僅在 lexical 完全無命中時啟用，過濾目錄頁、索引頁、過短文本等
        對回答無幫助的 chunk；若過濾後為空，則保留原始結果避免零召回。
        """
        filtered = [hit for hit in hits if not self._is_retrieval_noise(hit.page_content)]
        if filtered:
            return filtered[:top_k]
        return hits[:top_k]
    
    def hybrid_search_v2(
        self,
        semantic_results: list,
        lexical_results: list,
        k: int = 60,
        semantic_weight: float = 1.0,
        lexical_weight: float = 1.0
        ):
        """
        Reciprocal Rank Fusion (RRF) 合併 lexical 與 semantic 結果。

        公式：score(doc) += weight / (k + rank)
        - k 預設 60，用於平滑排名差異
        - lexical_weight 預設 0.7，略為偏好關鍵字命中的文檔
        """
        rrf_score_map = {} # {doc_id: Document}

        # 1. 處理 Semantic 排名
        for rank, hit in enumerate(semantic_results, start=1):
            metadata = hit.metadata
            rrf_score = semantic_weight * (1.0 / (k + rank))
            doc_id = metadata['id']
            if doc_id not in rrf_score_map:
                metadata['score'] = 0.0
                rrf_score_map[doc_id] = hit
            target_metadata = rrf_score_map[doc_id].metadata
            target_metadata['score'] = target_metadata.get('score', 0.0) + rrf_score
            target_metadata['semantic_rank'] = rank
            target_metadata['semantic_rrf_score'] = rrf_score
            target_metadata['semantic_score'] = metadata.get('semantic_score')
        # 2. 處理 Lexical 排名
        # 注意: lexical_search_v2 回傳的是 Document 物件，需與 semantic 格式對齊
        for rank, hit in enumerate(lexical_results, start=1):
            metadata = hit.metadata
            rrf_score = lexical_weight * (1.0 / (k + rank))
            doc_id = metadata['id']
            if doc_id not in rrf_score_map:
                metadata['score'] = 0.0
                rrf_score_map[doc_id] = hit
            target_metadata = rrf_score_map[doc_id].metadata
            target_metadata['score'] = target_metadata.get('score', 0.0) + rrf_score
            target_metadata['lexical_rank'] = rank
            target_metadata['lexical_rrf_score'] = rrf_score
            target_metadata['lexical_score'] = metadata.get('lexical_score')
        # 3. 根據 RRF 分數排序
        sorted_results = sorted(
            rrf_score_map.values(), 
            key=lambda x: x.metadata["score"], 
            reverse=True
        )

        return sorted_results

    def _merge_es_filters(self, *filters: dict | None) -> dict | None:
        """合併多個 ES filter（例如 metadata filter + doc id 白名單）。"""
        must_filters = [item for item in filters if item]
        if not must_filters:
            return None
        if len(must_filters) == 1:
            return must_filters[0]
        return {"bool": {"filter": must_filters}}

    def _build_id_filter(self, doc_ids: list[str]) -> dict | None:
        """將 lexical 候選 doc id 轉為 ES terms filter，供第二階段 semantic 限縮搜尋範圍。"""
        clean_ids = [str(doc_id) for doc_id in dict.fromkeys(doc_ids) if doc_id]
        if not clean_ids:
            return None
        return {"terms": {"metadata.id": clean_ids}}
    
    
    #TEST! es檢索 重要！
    
    async def es_retrive(self,
                         prompt,
                         lexical_top_k=20,
                         semantic_top_k=20,
                         lexical_weight=0.7,
                         index_name=INDEX_NAME,
                         metadata_filter: dict = None,
                         keyword_prefilter: bool = True,
                         keyword_prefilter_top_k: int | None = None,
                         query_extraction: QueryExtraction | RetrievalKeywordExtraction | dict | None = None):
        """
        ES 混合檢索主流程（keyword-first hybrid retrieval）。

        ┌─────────────────────────────────────────────────────────────────┐
        │ Phase 0: 關鍵字提取                                              │
        │   query_extraction（state 傳入）→ 否則 get_query_extraction()    │
        │   → merge keywords → lexical_queries + semantic_query           │
        ├─────────────────────────────────────────────────────────────────┤
        │ Phase 1: Keyword Lexical Prefilter（keyword_prefilter=True）     │
        │   對每個 lexical query variant 執行 lexical_search_v2 (BM25)     │
        │   → 合併去重 → lexical_hits（最多 lexical_top_k）                │
        ├─────────────────────────────────────────────────────────────────┤
        │ Phase 2A: Lexical 有命中 → Scoped Semantic                       │
        │   semantic 僅在 lexical 候選 doc id 內做 knn                     │
        │   retrieval_phase = keyword_prefilter_semantic                   │
        ├─────────────────────────────────────────────────────────────────┤
        │ Phase 2B: Lexical 無命中 → Semantic Fallback                     │
        │   全索引 semantic_search_v2 + _filter_semantic_noise 去噪          │
        │   retrieval_phase = semantic_fallback                            │
        ├─────────────────────────────────────────────────────────────────┤
        │ Phase 3: RRF 融合                                                │
        │   hybrid_search_v2(semantic_hits, lexical_hits)                  │
        │   lexical_weight=0.7 略偏好關鍵字命中                             │
        └─────────────────────────────────────────────────────────────────┘

        Args:
            prompt: 使用者原始問題（已由上游節點做簡繁轉換）
            lexical_top_k: lexical 去重後保留的候選數（預設 20）
            semantic_top_k: semantic 召回數（預設 20）
            lexical_weight: RRF 中 lexical 排名權重（預設 0.7）
            index_name: ES 索引名稱
            metadata_filter: 額外 ES filter，例如 faca_relevant=True
            keyword_prefilter: 是否啟用「先 lexical 再 semantic」策略
            keyword_prefilter_top_k: 每個 lexical variant 的召回上限（預設 min(max(semantic*2, lexical), 40)）
            query_extraction: 上游 intent 節點已提取的結構化結果，避免重複 LLM 呼叫
        """
        t0 = datetime.datetime.now()

        # ── Phase 0: 關鍵字提取 ──────────────────────────────────────────
        # 優先复用 AgentState 傳入的 query_extraction（intent 節點已呼叫 LLM）
        # 否則走 get_query_extraction（含 TTL 快取 + inflight 去重）
        extraction = self._coerce_query_extraction(query_extraction)
        if extraction is None:
            extraction = await self.get_query_extraction(prompt)

        # 合併 error_codes / symptoms / entities，補充簡繁變體，最多 12 個
        keywords = self._merge_retrieval_keywords(extraction)
        # 產生最多 4 個 BM25 query variant（錯誤碼單獨查、混合查、症狀查）
        lexical_queries = self.build_lexical_queries(keywords, prompt, extraction)
        # 語意 query 使用去噪後的關鍵字拼接，避免「發生原因」等套話干擾 embedding
        semantic_query = self.build_semantic_query(prompt, keywords)
        logging.info(f"ES检索关键词:{keywords}")
        logging.info(f"ES检索keyword_extraction:{extraction.model_dump()}")
        logging.info(f"ES检索lexical_queries:{lexical_queries}")
        logging.info(f"ES检索semantic_query:{semantic_query}")

        # 每個 lexical variant 的召回上限，避免拉取過多候選導致 ES 變慢
        keyword_prefilter_top_k = keyword_prefilter_top_k or min(max(semantic_top_k * 2, lexical_top_k), 40)

        lexical_hits: list = []
        semantic_hits: list = []
        lexical_candidates: list = []

        # ── Phase 1: Keyword Lexical Prefilter ───────────────────────────
        if keyword_prefilter:
            for query_variant in lexical_queries:
                current_lexical_hits = await self.lexical_search_v2(
                    index_name=index_name,
                    query=query_variant,
                    top_k=keyword_prefilter_top_k,
                    my_filter=metadata_filter,
                )
                for hit in current_lexical_hits:
                    hit.metadata["query_variant"] = query_variant
                    hit.metadata["retrieval_phase"] = "keyword_lexical"
                lexical_candidates.extend(current_lexical_hits)
            # 多 variant 結果依 doc id 去重，保留最高 BM25 分
            lexical_hits = self._dedupe_hits_by_id(lexical_candidates, lexical_top_k)

        # ── Phase 2A / 2B: Semantic 檢索 ─────────────────────────────────
        if keyword_prefilter and lexical_hits:
            # Phase 2A: lexical 有命中 → 在候選 doc id 範圍內做 scoped semantic
            candidate_ids = [
                str(doc_id)
                for doc_id in dict.fromkeys(hit.metadata.get("id") for hit in lexical_hits)
                if doc_id
            ]
            semantic_filter = self._merge_es_filters(
                metadata_filter,
                self._build_id_filter(candidate_ids),
            )
            semantic_k = min(semantic_top_k, len(candidate_ids)) or semantic_top_k
            try:
                current_semantic_hits = await self.semantic_search_v2(
                    index_name=index_name,
                    query=semantic_query,
                    top_k=semantic_k,
                    my_filter=semantic_filter,
                )
            except Exception as exc:
                # knn + id filter 偶發失敗時，降級為僅 metadata_filter 的全域 semantic
                logging.warning(f"{index_name} keyword prefilter semantic search failed, fallback to metadata filter: {exc}")
                current_semantic_hits = await self.semantic_search_v2(
                    index_name=index_name,
                    query=semantic_query,
                    top_k=semantic_top_k,
                    my_filter=metadata_filter,
                )
            for hit in current_semantic_hits:
                hit.metadata["query_variant"] = semantic_query
                hit.metadata["retrieval_phase"] = "keyword_prefilter_semantic"
            semantic_hits = current_semantic_hits
        else:
            # Phase 2B: lexical 無命中 → 直接 semantic fallback
            if keyword_prefilter:
                logging.info(f"{index_name} keyword lexical returned no results, fallback to semantic search")
            current_semantic_hits = await self.semantic_search_v2(
                index_name=index_name,
                query=semantic_query,
                top_k=semantic_top_k,
                my_filter=metadata_filter,
            )
            for hit in current_semantic_hits:
                hit.metadata["query_variant"] = semantic_query
                hit.metadata["retrieval_phase"] = "semantic_fallback"
            # fallback 路徑額外過濾目錄頁、索引頁等低價值 chunk
            semantic_hits = self._filter_semantic_noise(current_semantic_hits, semantic_top_k)

        # ── Phase 3: RRF 融合 ────────────────────────────────────────────
        # 合併 semantic 排名與 lexical 排名，lexical_weight=0.7 略偏好精確匹配
        target_nodes = self.hybrid_search_v2(
            semantic_results=semantic_hits,
            lexical_results=lexical_hits,
            k=60,
            semantic_weight=1.0,
            lexical_weight=lexical_weight,
        )
        t1 = datetime.datetime.now()
        logging.info(f"ES search cost:{round((t1-t0).total_seconds(),3)} seconds")
        return target_nodes

    #TEST! RAG重點
    async def process_query(
        self,
        prompt,
        index_name="iap",
        metadata_filter: dict = None,
        query_extraction: QueryExtraction | RetrievalKeywordExtraction | dict | None = None,
    ):
        """
        RAG 檢索入口：包裝 es_retrive 並回傳 Document 列表。

        上游 faca_retrieve 會并行呼叫此函數 3 次（issue / file / sop_file），
        並传入相同的 query_extraction 以避免重复 LLM 提取。
        """
        chat_response = "檢索結果:"
        target_nodes = await self.es_retrive(
            prompt,
            index_name=index_name,
            metadata_filter=metadata_filter,
            query_extraction=query_extraction,
        )
        response={"chat_response": chat_response,"results": target_nodes}
        return response
    
    def process_att(self, node):
        meta_data = node.metadata
        node_type = meta_data['node_type']
        attachments = meta_data.get('attachment', [])
        basic_field = {"DESCRIPTION":"",
                                "ImagePath_0":"",
                                "ImagePath_1":"",
                                "ImagePath_2":"",
                                "file_0":{"path":"","hits":[]},
                                "file_1":{"path":"","hits":[]},
                                "file_2":{"path":"","hits":[]},
                                }
        att_keys = {
            "rootCause": copy.deepcopy(basic_field),
            "actionCause": copy.deepcopy(basic_field),
            "explanCause": copy.deepcopy(basic_field),
            "poorDescription": copy.deepcopy(basic_field),
            "studyCause": copy.deepcopy(basic_field)
        }
        
        for att in attachments:
            FIELD_NAME = att["FIELD_NAME"]
            match = re.match(r'^[a-zA-Z]+', FIELD_NAME)
            if match:
                base_name = match.group()
            else:
                raise AssertionError(f"原始字串: {FIELD_NAME} -> 無匹配")
            if base_name not in att_keys:
                raise AssertionError(f"{base_name} not in att_keys")
            # 描述
            if "description" in FIELD_NAME:
                att_keys[base_name]["DESCRIPTION"] = att["DESCRIPTION"]
            elif "pic" in FIELD_NAME:
                # 使用正則表達式提取最後一個數字
                match = re.search(r'(\d+)$', FIELD_NAME)
                if match:
                    pic_n = match.group(1)
                else:
                    raise AssertionError("沒有找到數字")
                para = f"ImagePath_{pic_n}"
                if para in att_keys[base_name]:
                    try:
                        file_path  = att["PATH"]
                        file_path = self.update_file_path(file_path)
                    except:
                        file_path = ""
                else:
                    raise AssertionError(f"{para} not in att_keys")
                att_keys[base_name][para] = file_path
        return att_keys
    
    def _issue_id_prefix_for_index(self, index_name: str | None) -> str:
        """依 ES 索引决定合成 ISSUE_ID 前缀。"""
        normalized = str(index_name or "").lower()
        if normalized.startswith("ae_sop_file"):
            return self.ISSUE_ID_PREFIX_SOP
        if normalized.startswith("file_") or normalized == "file":
            return self.ISSUE_ID_PREFIX_OTHER
        return self.ISSUE_ID_PREFIX_OTHER

    @classmethod
    def is_synthetic_issue_id(cls, issue_id: str) -> bool:
        """判断是否为非 FACA 案例库的合成 ISSUE_ID。"""
        normalized = str(issue_id or "").strip().upper()
        if not normalized:
            return False
        if normalized.startswith("SOP_FILE_"):
            return True
        for prefix in (cls.ISSUE_ID_PREFIX_SOP, cls.ISSUE_ID_PREFIX_OTHER):
            if normalized.startswith(prefix) and len(normalized) == len(prefix) + cls.ISSUE_ID_SUFFIX_LEN:
                suffix = normalized[len(prefix):]
                if re.fullmatch(r"[0-9A-F]+", suffix):
                    return True
        return False

    def _build_dummy_issue_id(self, node, prefix: str | None = None):
        """
        为无 ISSUE_ID 的文档 chunk 生成合成 ID。

        格式：`{前缀}{10位SHA1}`，例如：
        - ae_sop_file_zh → SOP962DE35A3D
        - file_zh        → OTHER962DE35A3D
        """
        meta_data = node.metadata
        source = (
            meta_data.get("file_path")
            or meta_data.get("file_name")
            or meta_data.get("id")
            or node.page_content
        )
        digest = hashlib.sha1(str(source).encode("utf-8")).hexdigest()[:self.ISSUE_ID_SUFFIX_LEN].upper()
        issue_prefix = prefix or self.ISSUE_ID_PREFIX_OTHER
        return f"{issue_prefix}{digest}"

    def _get_issue_id(self, node, index_name: str | None = None):
        """
        取得 ISSUE_ID。

        - iap_ae_issue：优先使用 metadata.basic_information.ISSUE_ID（如 SFCA60122028C）
        - file / ae_sop_file：若无真实 ISSUE_ID，则依索引来源生成 SOP* / OTHER* 合成 ID
        """
        basic_information = node.metadata.get("basic_information") or {}
        issue_id = basic_information.get("ISSUE_ID")
        if issue_id is None or str(issue_id).strip() in {"", "Not found Issue ID"}:
            prefix = self._issue_id_prefix_for_index(index_name)
            return self._build_dummy_issue_id(node, prefix=prefix), True
        return str(issue_id).strip(), False
    
    def process_match(self, node, att_keys, allow_unmapped_file=False):
        # 若命中的是檔案 則需要將檔案資訊整理
        meta_data = node.metadata
        hit_file_path = self.update_file_path(meta_data['file_path'])
        tmp_hit_path = hit_file_path.split('/')[-1].split('.')[0]
        # 使用正則表達式base name
        match = re.match(r'^[a-zA-Z]+', tmp_hit_path)
        if match:
            base_name = match.group()
        elif allow_unmapped_file:
            base_name = "studyCause"
        else:
            raise AssertionError(f"原始字串: {tmp_hit_path} -> 無匹配")
        if base_name not in att_keys and allow_unmapped_file:
            base_name = "studyCause"
        elif base_name not in att_keys:
            raise AssertionError(f"{base_name} not in att_keys")
        # 使用正則表達式提取最後一個數字
        match = re.search(r'(\d+)$', tmp_hit_path)
        if match:
            file_n = match.group(1)
        elif allow_unmapped_file:
            file_n = "0"
        else:
            raise AssertionError("沒有找到數字")
        if allow_unmapped_file and f"file_{file_n}" not in att_keys[base_name]:
            file_n = "0"
        
        Page = meta_data.get("page_label", "")
        content  = node.page_content
        page_snapshot = ""
        if 'page_snapshot' in meta_data and meta_data.get("page_snapshot"):
            if ENV == "prod":
                a = "/mnt/hdd/Projects/iap"
            else:
                a = "/mnt/hdd1/Projects/iap"
            page_snapshot = meta_data['page_snapshot'].replace('/app', a)
        elif hit_file_path:
            # MinerU file ETL 可能沒有 page_snapshot，改用檔案路徑+頁碼當唯一識別
            page_snapshot = f"{hit_file_path}#page={Page}"
        if base_name in att_keys:
            if allow_unmapped_file and att_keys[base_name]["DESCRIPTION"] == "":
                att_keys[base_name]["DESCRIPTION"] = meta_data.get("file_name", tmp_hit_path)
            if att_keys[base_name][f'file_{file_n}']['path'] == "":
                att_keys[base_name][f'file_{file_n}']['path'] = hit_file_path
        else:
            raise AssertionError(f"{base_name}不存在")
        
        if hit_file_path != "" and Page != '' and content != "":
            check_falg = True
            for h in att_keys[base_name][f'file_{file_n}']['hits']:
                if h['snapshot'] == page_snapshot and h['page'] == Page:
                    check_falg = False
                    break
            if check_falg:
                att_keys[base_name][f'file_{file_n}']['hits'].append({'snapshot':page_snapshot,
                                                            'page':Page,
                                                            'content':content
                                                            })
        return  att_keys
    
    
    def _get_source_doc_id(self, node, index_name: str | None = None):
        meta_data = node.metadata
        return str(
            meta_data.get("id")
            or meta_data.get("source_doc_id")
            or self._build_dummy_issue_id(
                node,
                prefix=self._issue_id_prefix_for_index(index_name),
            )
        )
    
    async def faca_retrieve(
        self,
        prompt,
        index_name='iap',
        use_llm=False,
        result_limit: int = 5,
        metadata_filter: dict = None,
        query_extraction: QueryExtraction | RetrievalKeywordExtraction | dict | None = None,
    ):
        # 執行查詢以獲取相關資料
        try:
            responses = await self.process_query(
                prompt,
                index_name=index_name,
                metadata_filter=metadata_filter,
                query_extraction=query_extraction,
            )
        except Exception as exc:
            if not metadata_filter:
                raise
            logging.warning(f"{index_name} metadata_filter failed, fallback to unfiltered retrieval: {exc}")
            responses = await self.process_query(
                prompt,
                index_name=index_name,
                query_extraction=query_extraction,
            )
        if metadata_filter and not responses.get("results"):
            logging.info(f"{index_name} metadata_filter returned no results, fallback to unfiltered retrieval")
            responses = await self.process_query(
                prompt,
                index_name=index_name,
                query_extraction=query_extraction,
            )
 
        results_dic = {}
        for node in responses['results']:
            meta_data = node.metadata
            node_type = meta_data['node_type']
            synthesize = meta_data['raw_content'] if meta_data['rewrite_content'] is None else meta_data['rewrite_content']
            ISSUE_ID, is_dummy_issue = self._get_issue_id(node, index_name=index_name)
            score = float(meta_data.get("score") or meta_data.get("semantic_score") or 0)
            source_doc_id = self._get_source_doc_id(node, index_name=index_name)
            # 格式化文本內容
            ss = f"{synthesize}"
            if node_type == "file":
                ss += f" ** from {meta_data['file_name']} 第{meta_data['page_label']}頁"
            if ISSUE_ID not in results_dic:
                tmp_results = {
                    "ISSUE_ID": ISSUE_ID,
                    "Text": [ss],
                    "Score": score,
                    "source_doc_id": source_doc_id,
                    "chunk_ids": [source_doc_id],
                    "source_node_type": node_type,
                    "source_file_name": meta_data.get("file_name", ""),
                    "source_page_label": meta_data.get("page_label", ""),
                    "retrieval_debug": {
                        "score": meta_data.get("score"),
                        "semantic_rank": meta_data.get("semantic_rank"),
                        "lexical_rank": meta_data.get("lexical_rank"),
                        "semantic_score": meta_data.get("semantic_score"),
                        "lexical_score": meta_data.get("lexical_score"),
                    },
                    "relatedFile": self.process_att(node)
                }
                if "file" in node_type:
                    self.process_match(node, tmp_results["relatedFile"], allow_unmapped_file=is_dummy_issue)
                results_dic[ISSUE_ID] = tmp_results
            else:
                results_dic[ISSUE_ID]["Score"] = max(results_dic[ISSUE_ID].get("Score", 0), score)
                if source_doc_id not in results_dic[ISSUE_ID].setdefault("chunk_ids", []):
                    results_dic[ISSUE_ID]["chunk_ids"].append(source_doc_id)
                if "file" in node_type:
                    results_dic[ISSUE_ID]['Text'].append(ss)
                    self.process_match(node, results_dic[ISSUE_ID]["relatedFile"], allow_unmapped_file=is_dummy_issue)
        results = list(results_dic.values())[:result_limit]
        chat_response = responses.get('chat_response')
        return results, chat_response
    
    def _load_sop_errorcode_table(self):
        iap_ae_errorcodelist = self._tb_repo.query_table("iap_ae_errorcodelist")
        iap_ae_errorcodelist = iap_ae_errorcodelist[iap_ae_errorcodelist["brand_zh"] != "无"]
        iap_ae_errorcodelist = iap_ae_errorcodelist[iap_ae_errorcodelist["brand_zh"] != ""]
        iap_ae_errorcodelist = iap_ae_errorcodelist[
            ['brand_en', 'brand_zh', 'model', 'errorcode', 'category']
        ].dropna(subset=['brand_en', 'brand_zh', 'model', 'errorcode']).drop_duplicates()
        iap_ae_errorcodelist.fillna("", inplace=True)
        return iap_ae_errorcodelist

    def build_sop_jieba_tokens(self, query: str, iap_ae_errorcodelist=None) -> list[str]:
        cc = OpenCC('t2s')
        normalized_query = cc.convert(query or "").replace("SOP", "").replace("sop", "")
        normalized_query = normalized_query.upper()

        if iap_ae_errorcodelist is None:
            iap_ae_errorcodelist = self._load_sop_errorcode_table()

        cols = ["brand_en", "brand_zh", "model", "errorcode", 'category']
        d = iap_ae_errorcodelist[cols].drop_duplicates()
        brands_en_set = set(map(lambda x: str(x).strip().upper(), d["brand_en"].astype(str)))
        brands_zh_set = set(map(str, d["brand_zh"].astype(str)))
        models_set = set(map(str, d["model"].astype(str)))
        errorcodes_set = set(map(str, d["errorcode"].astype(str)))
        category_set = set(map(str, d["category"].astype(str)))

        for w in (brands_en_set | brands_zh_set | errorcodes_set | models_set | category_set):
            if w and len(str(w)) > 1:
                jieba.add_word(str(w), freq=1000000)

        tokens = list(jieba.cut(normalized_query, HMM=False))
        for pattern in (
            r"\d+\.\d+",
            r"\d+[A-Z]\.\d+",
            r"\d\d\.+[A-Z]",
            r"MITSUBISHI ELECTRIC",
        ):
            tokens.extend(re.findall(pattern, normalized_query))
        return tokens

    def map_extraction_to_sop_entities(
        self,
        extraction: QueryExtraction,
        iap_ae_errorcodelist,
    ) -> dict:
        output_dict = {"model": "", "errorcode": "", "brand_en": "", "brand_zh": "", "category": ""}
        direct_fields = {
            "brand_en": extraction.brand_en,
            "brand_zh": extraction.brand_zh,
            "model": extraction.model,
            "errorcode": extraction.errorcode or (extraction.error_codes[0] if extraction.error_codes else ""),
            "category": extraction.category,
        }
        for field, value in direct_fields.items():
            if value and output_dict[field] == "":
                output_dict[field] = str(value).strip()

        candidates: list[str] = []
        for value in direct_fields.values():
            if value:
                candidates.append(str(value).strip())
        candidates.extend(str(code).strip() for code in extraction.error_codes if code)
        candidates.extend(str(entity).strip() for entity in extraction.entities if entity)

        for v in dict.fromkeys(candidates):
            if v == "":
                continue
            for k in output_dict.keys():
                if output_dict[k] != "":
                    continue
                if k in ("brand_zh", "category"):
                    target_df_exactly_match = iap_ae_errorcodelist[iap_ae_errorcodelist[k].str.contains(v, na=False)]
                else:
                    target_df_exactly_match = iap_ae_errorcodelist[iap_ae_errorcodelist[k] == v]
                if target_df_exactly_match.empty:
                    continue
                keyword = target_df_exactly_match.reset_index().loc[0, k]
                output_dict[k] = keyword
        return output_dict

    #TEST! 提取SOP中關鍵字
    async def sop_entity_extract(self, query):
        logging.info("sop_entity_extract start")
        tt0 = datetime.datetime.now()
        iap_ae_errorcodelist = self._load_sop_errorcode_table()
        tokens = self.build_sop_jieba_tokens(query, iap_ae_errorcodelist=iap_ae_errorcodelist)
        tt1 = datetime.datetime.now()
        logging.info(f"jieba 分詞耗時: {(tt1-tt0).total_seconds()} seconds")
        logging.info(f"{tokens}")

        extraction = await self.get_query_extraction(query, jieba_tokens=tokens)
        output_dict = self.map_extraction_to_sop_entities(extraction, iap_ae_errorcodelist)

        tt2 = datetime.datetime.now()
        logging.info(f"sop_entity_extract 耗時: {(tt2-tt1).total_seconds()} seconds")
        logging.info(f"result: {output_dict}")
        return output_dict, extraction
    
    def extract_first_json(self, text: str):
        match = re.search(r'\{.*?\}', text, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise ValueError("無法從輸入中解析出 JSON")

    # LLM先掃過一遍粗看  第二段剩下一兩個在一次看    
    
    async def sop_content_extract(self, errorcode:str, results:list):
        t0 = datetime.datetime.now()
        chunck_map = {chunck.metadata["id"]: chunck for chunck in results}
        # 快速篩選
        system_prompt1 = compile_langfuse_prompt_or_fallback(
            self.langfuse_client,
            "sop_content_relevance",
            fallback="請判斷文章內容是否真正描述錯誤代碼 {errorcode} 的發生原因或處理方式，僅回答 Y 或 N。",
            errorcode=errorcode,
        )
        output_results=[]
        for n in results:
            content = n.page_content
            pass
            usr_input = f"文章內容:{content}"
            _, content_judge = self.vllm_general_llm.chat(system_prompt=system_prompt1,
                                                    usr_input=usr_input,
                                                    max_tokens=512, thinking=False)
            if content_judge == "Y":
                output_results.append((content_judge, n))
            output_results.append(("Y", n))
        output_results = output_results[:2]
        # 多個內文給LLM一次看選出一個
        logging.info("簡化找到的頁面")
        system_prompt2 = compile_langfuse_prompt_or_fallback(
            self.langfuse_client,
            "sop_content_extract_short",
            fallback="請取出關於錯誤代碼 {errorcode} 的發生原因或處理方式，並且儘量簡短。",
            errorcode=errorcode,
        )
        candidate_contents = ""
        for judge, n in output_results:
            usr_input = f"文章內容:{n.page_content}"
            _, content_sys = self.vllm_general_llm.chat(system_prompt=system_prompt2,
                                                        usr_input=usr_input,
                                                        max_tokens=512, thinking=False)
            candidate_contents += f"[文件ID]:{n.metadata['id']}\n[內文]\n{content_sys}\n"
        logging.info("簡化結束")
        if len(output_results) == 0:
            raise AssertionError("LLM 沒有找到任何符合的頁面")
        system_prompt = compile_langfuse_prompt_or_fallback(
            self.langfuse_client,
            "sop_content_select",
            fallback=(
                "請找出最符合用戶問題的內容，輸出 JSON：{{\"Node ID\":\"...\",\"content\":\"...\"}}\n"
                "以下是文章段落：\n{candidate_contents}"
            ),
            candidate_contents=candidate_contents,
        )
        #vllm
        usr_input = f"用戶問題: 錯誤代碼`{errorcode}` 如何處理與發生原因？"
        _, llm_response = self.vllm_general_llm.chat(system_prompt=system_prompt,
                                                    usr_input=usr_input,
                                                    max_tokens=1024, thinking=False)
        cleaned = llm_response.strip('`json\n ')
        json_response = self.extract_first_json(cleaned)
        chunck_id = json_response['Node ID']
        chat_response = json_response['content']
        t1 = datetime.datetime.now()
        results = [chunck_map[chunck_id]]
        logging.info(f"sop_content_extract 耗時: {(t1-t0).total_seconds()} seconds")
        return results, chat_response
    
    
    async def cosine_similarity(self, a, b):
        a = np.array(a)
        b = np.array(b)
        # normalize a
        a_norm = a / (np.linalg.norm(a) + 1e-12)
        # 如果 b 是 1 維 → 單筆比較
        if b.ndim == 1:
            b_norm = b / (np.linalg.norm(b) + 1e-12)
            return float(np.dot(a_norm, b_norm))
        # 如果 b 是 2 維 → 批次比較
        elif b.ndim == 2:
            b_norm = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-12)
            return np.dot(b_norm, a_norm)
        else:
            raise ValueError("b 必須是一維或二維的向量")
    
    async def embedding_search(self, query_emb, content_embedding, chunks):
        chunks = chunks.copy()
        scores = await self.cosine_similarity(query_emb, content_embedding)
        for idx, n in enumerate(chunks):
            score = scores[idx]
            n['_score'] += score
        # 排序取 Top N
        semantic_hist = sorted(chunks, key=lambda x: x['_score'], reverse=True)
        return semantic_hist
    
    
    async def sop_content_retrieve(self, query:str,
                                   errorcode: str,
                                   model:str=None,
                                   brand:str=None, lang_code:str="ZH"):
        errorcode = errorcode.strip() if errorcode else ""
        model = model.strip() if model else ""
        brand = brand.strip() if brand else ""
        t0 = datetime.datetime.now()
        #TEST! 以metada 篩選 需要 content命中error_code 與 model 存在於filename
        if (errorcode != "") and (errorcode != None):
            my_filter = []
            models = model.split("/")
            models_filter = []
            for m in models:
                models_filter.append({
                "wildcard": {
                    "metadata.file_name": f"*{m}*"
                }
            })
            my_filter = {
                "bool": {
                    # must 代表這是一個 AND 邏輯，裡面的條件都必須成立
                    "must": [
                        # 條件一：檔名的 OR 邏輯 (包在一個 should 裡面)
                        {
                            "bool": {
                                "should": models_filter,
                                "minimum_should_match": 1  # 確保 should 裡面至少要中一個
                            }
                        },
                        # 條件二：內文的條件 (與上面的 should 區塊是 AND 關係)
                        {
                            "match_phrase": {
                                "text": f"{errorcode}"
                            }
                        }
                    ]
                }
            }
            target_nodes = await self.semantic_search_v2(index_name=f"ae_sop_file_{lang_code.lower()}",
                                                          query=errorcode,
                                                          top_k=50, my_filter=my_filter)
            if len(target_nodes) ==0:
                raise AssertionError(f"errorcode:{errorcode} not match!")
            
           
            if target_nodes == []:
                results, chat_response = [], f"很抱歉，本次找不到數據，請提供更完整資訊或重新嘗試, 以下是本次提取資訊, brand:{brand.strip()}、errorcode:{errorcode.strip()}、模型:{model.strip()}"
            else:
                chat_response  = "尚未生成"
                chat_response  = await self.ae_sop_llm_systhsis(target_nodes, errorcode=errorcode)
                pass
                results = []
                for n in target_nodes:
                    content  = n.page_content
                    metadata = n.metadata
                    Score    = metadata['score']
                    page     = metadata["page_label"]
                    file_name= metadata["file_name"]
                    file_path= metadata["file_path"]
                    snapshot = metadata["page_snapshot"]
                    
                    if ENV == 'dev':
                        file_path = file_path.replace("/app", "/mnt/hdd1/Projects/iap")
                        snapshot  = snapshot.replace("/app", "/mnt/hdd1/Projects/iap")
                    else:
                        file_path = file_path.replace("/app", "/mnt/hdd/Projects/iap")
                        snapshot  = snapshot.replace("/app", "/mnt/hdd/Projects/iap")
                    results.append({
                        "content": content, "Score": Score, "page": page,
                        "file_name": file_name, "file_path": file_path, "snapshot": snapshot
                    })
        else:
            logging.info("沒有提供errorcode 以文字檢索SOP文件 ")
            pass
            my_filter = []
            models = model.split("/")
            models_filter = []
            for m in models:
                models_filter.append({
                "wildcard": {
                    "metadata.file_name": f"*{m}*"
                }
            })
            my_filter = {
                "bool": {
                    # must 代表這是一個 AND 邏輯，裡面的條件都必須成立
                    "must": [
                        # 條件一：檔名的 OR 邏輯 (包在一個 should 裡面)
                        {
                            "bool": {
                                "should": models_filter,
                                "minimum_should_match": 1  # 確保 should 裡面至少要中一個
                            }
                        }
                    ]
                }
            }
            pass
            target_nodes = await self.semantic_search_v2(index_name=f"ae_sop_file_{lang_code.lower()}",
                                                          query=errorcode,
                                                          top_k=50, my_filter=my_filter)
            results_, chat_response = await self.sop_content_extract(query=query,
                                                                     results=target_nodes)
            results = []
            for n in results_:
                content  = n.page_content
                Score    = n["Similarity"]
                page     = n["meta_data"]["page_label"]
                file_name= n["meta_data"]["file_name"]
                file_path= n["meta_data"]["file_path"]
                snapshot = n["meta_data"]["page_snapshot"]
                if ENV == 'dev':
                    file_path = file_path.replace("/app", "/mnt/hdd1/Projects/iap")
                    snapshot  = snapshot.replace("/app", "/mnt/hdd1/Projects/iap")
                else:
                    file_path = file_path.replace("/app", "/mnt/hdd/Projects/iap")
                    snapshot  = snapshot.replace("/app", "/mnt/hdd/Projects/iap")
                results.append({
                    "content": content, "Score": Score, "page": page,
                    "file_name": file_name, "file_path": file_path, "snapshot": snapshot
                })
        return results, chat_response