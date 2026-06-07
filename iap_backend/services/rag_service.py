import hashlib
import os, sys, re
import time

sys.path.extend(['.', '..'])
import uuid
from common.utils import generate_chunk_id
from services.services import TranslatorService
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
import re, unicodedata, datetime, logging
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
from langchain_text_splitters import RecursiveCharacterTextSplitter
from typing import Any, Optional, Literal, Sequence
from pydantic import BaseModel, Field, model_validator
from elasticsearch import Elasticsearch
from typing import Dict, Any
import asyncio
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field
from typing import List, Optional
from pydantic import BaseModel, Field
from typing import List, Optional
from langchain_core.messages import SystemMessage, HumanMessage
import datetime
import logging
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
        metadata.setdefault("content_type", "user_document")
        for key in ("faca_relevant", "has_solution", "has_root_cause", "has_action", "page_type"):
            metadata.pop(key, None)
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
    
    
    
    # 更新附件檔案路徑
    def update_file_path(self, file_path):
        return file_path.replace("/app/rag_doc/iap/", "/reports/")
    
    
    # 移除所有私用區 (PUA) 符號
    def clean_pdf_text(self, text: str) -> str:
        return re.sub(r'[\ue000-\uf8ff\n\r]', '', text)
    
    
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

        上游 retrieve 會并行呼叫此函數 3 次（issue / file / sop_file），
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
    
    async def retrieve(
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
    
