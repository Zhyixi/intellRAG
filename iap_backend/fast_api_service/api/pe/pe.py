import re, os, sys
from typing import List
from langchain_openai import ChatOpenAI
import pymongo

from fast_api_service.api.ae.schemas import MessageSchemaDirectV1
from repositories.repositories import TableManagerRepository
from services.pe_graphs import process_agent_retrieve
from services.services import MongoService, TranslatorService
from services.rag_service import RetriveService
sys.path.extend(['.', '..'])
import pandas as pd
import numpy as np
from fast_api_service.response import setErrorResponse
from fastapi import APIRouter, HTTPException, status
import datetime
import traceback
import logging
from common.utils import get_url_path, send_py_error_msg_to_slack
import datetime
from common.utils import replace_invalid_values, custom_jsonable_encoder
from fast_api_service.api.pe.schemas import MessageSchema, MessageSchemaDirect, iapAnalystDetailRequest, iapAnalystRequest, ResponseModel
from configs.config import pe_api_url
from fastapi import APIRouter, Depends
from dependency_injector.wiring import inject, Provide
from containers import Container, get_container
from fastapi.responses import JSONResponse
from PIL import Image
from io import BytesIO
import base64
from common.utils import get_url_path, list_all_files, send_py_error_msg_to_slack
from langfuse import observe
import datetime
from langgraph.checkpoint.mongodb import MongoDBSaver
from common.langfuse_tracing import build_agent_run_config
from typing import Any, Dict, List, Optional
from fastapi import APIRouter
from pydantic import BaseModel, Field
import requests
import pandas as pd
import ast
import httpx
import asyncio
from collections import defaultdict
logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/pe", tags=["iap-pe"])

iap_CHAT_HISTORY_COLLECTION = "iap_Chat_History"


def _build_history_payload(
    *,
    session_id: str,
    user_input: str,
    user_role: str,
    final_state: dict,
    ret_type: str,
    started_at: datetime.datetime,
    elapsed_seconds: float,
) -> dict:
    return {
        "session_id": session_id,
        "timestamp": started_at,
        "Question": user_input,
        "Role": user_role,
        "Response": final_state["final_response"],
        "Results": final_state["results"],
        "ret_type": ret_type,
        "Time-Consuming": str(elapsed_seconds),
    }


async def _run_agent_and_store_history(
    *,
    user_input: str,
    session_id: str,
    user_role: str,
    run_config: dict,
    checkpointer: MongoDBSaver,
    mongo_service: MongoService,
    ret_type: str,
) -> dict:
    started_at = datetime.datetime.now()
    final_state = await process_agent_retrieve(
        user_input,
        run_config,
        checkpointer,
    )
    elapsed_seconds = (datetime.datetime.now() - started_at).total_seconds()
    insert_data = _build_history_payload(
        session_id=session_id,
        user_input=user_input,
        user_role=user_role,
        final_state=final_state,
        ret_type=ret_type,
        started_at=started_at,
        elapsed_seconds=elapsed_seconds,
    )
    mongo_service.insert_many(insert_data=insert_data, collection_name=iap_CHAT_HISTORY_COLLECTION)
    return final_state


def _history_session_id_pipeline(usr: str, limit: int = 10) -> list[dict]:
    return [
        {"$match": {"Role": usr}},
        {"$sort": {"timestamp": -1}},
        {
            "$group": {
                "_id": "$session_id",
                "Question": {"$first": "$Question"},
                "timestamp": {"$first": "$timestamp"},
            }
        },
        {"$sort": {"timestamp": -1}},
        {"$limit": limit},
    ]


def _history_records_to_response(records: list[dict]) -> dict:
    base_data = pd.DataFrame(records)
    if base_data.empty:
        return {"result": []}
    base_data.reset_index(drop=True, inplace=True)
    if "_id" in base_data.columns:
        base_data.drop(columns=["_id"], inplace=True)
    base_data = base_data.map(lambda x: custom_jsonable_encoder(x))
    base_data = replace_invalid_values(base_data)
    return {"result": base_data.to_dict(orient="records")}


def _collect_faq_questions(mongo_service: MongoService, collection_name: str) -> list[str]:
    df = mongo_service.find_df(
        query={},
        collection_name=collection_name,
        sort=[("timestamp", pymongo.DESCENDING)],
        limit=100,
    )
    if df.empty:
        return []
    df.sort_values(by="timestamp", ascending=False, inplace=True)
    return list(set(df["Question"].values))[:5]



route_retrieve = get_url_path(url=pe_api_url.retrieve)
@router.post(route_retrieve, status_code=status.HTTP_200_OK, response_model=ResponseModel)
@observe(name="pe_retrieve")
@inject
async def retrieve(input_para: MessageSchemaDirect,
                      mongo_service: MongoService = Depends(Provide[Container.mongo_service]),
                      retrice_service: RetriveService = Depends(Provide[Container.retrive_service]),
                      tb_repo: TableManagerRepository = Depends(Provide[Container.table_manager_repository]),
                      llm: ChatOpenAI = Depends(Provide[Container.vllm_general_llm]),
                      translator: TranslatorService = Depends(Provide[Container.translator])):
    session_id = input_para.session_id
    user_input = input_para.content
    user_role = input_para.role
    syslang = input_para.syslang
    logging.info(f"{input_para}")
    t0 = datetime.datetime.now()
    checkpointer = container.mongo_checkpointer()
    trace_name = "pe_retrieve"
    run_config = build_agent_run_config(
        session_id=session_id,
        retrice_service=retrice_service,
        tb_repo=tb_repo,
        llm=llm,
        translator=translator,
        version="v3",
        syslang=syslang,
        intent="",
        trace_name=trace_name,
        domain="pe",
        user_role=user_role,
    )
    final_state = await _run_agent_and_store_history(
        user_input=user_input,
        session_id=session_id,
        user_role=user_role,
        run_config=run_config,
        checkpointer=checkpointer,
        mongo_service=mongo_service,
        ret_type="pe_faca",
    )
    tt = (datetime.datetime.now() - t0).total_seconds()
    logging.info("pe retrieve elapsed: %ss", tt)
    return ResponseModel(
            role="assistant",
            results=final_state.get("results", []), 
            chat_response=final_state.get("final_response", "").strip('/not_think'), 
            ret_type="pe_faca",
            source = final_state.get('source', []),
            suggested_questions= final_state.get("suggested_questions", [])
        )

route = get_url_path(url=pe_api_url.invoke)
@router.post(route, response_model=ResponseModel) # 指定回傳格式
@observe(name="pe_invoke")
@inject
async def invoke(
    input_para: MessageSchema,
    mongo_service: MongoService = Depends(Provide[Container.mongo_service]),
    retrice_service: RetriveService = Depends(Provide[Container.retrive_service]),
    tb_repo: TableManagerRepository = Depends(Provide[Container.table_manager_repository]),
    llm: ChatOpenAI = Depends(Provide[Container.vllm_general_llm]),
    translator: TranslatorService = Depends(Provide[Container.translator]),
):
    session_id = input_para.session_id
    user_input = input_para.content
    user_role = input_para.role
    syslang = input_para.syslang
    logging.info(f"{input_para}")
    checkpointer = container.mongo_checkpointer()
    trace_name = "pe_invoke"

    run_config = build_agent_run_config(
        session_id=session_id,
        retrice_service=retrice_service,
        tb_repo=tb_repo,
        llm=llm,
        translator=translator,
        version="v3",
        syslang=syslang,
        intent="direct_analysis",
        trace_name=trace_name,
        domain="pe",
        user_role=user_role,
    )
    final_state = await _run_agent_and_store_history(
        user_input=user_input,
        session_id=session_id,
        user_role=user_role,
        run_config=run_config,
        checkpointer=checkpointer,
        mongo_service=mongo_service,
        ret_type="pe_faca_invoke",
    )
    return ResponseModel(
            role="assistant",
            results=final_state.get("results", []), 
            chat_response=final_state.get("final_response", "").strip('/not_think'), 
            ret_type="pe_faca_invoke",
            source = final_state.get('source', []),
            suggested_questions= final_state.get("suggested_questions", [])
        )


route = get_url_path(url=pe_api_url.history_session_id)
@router.get(route, tags=["iap-pe"], status_code=status.HTTP_200_OK)
@inject
async def get_history_session_id(usr: str="10038437",
                                 mongo_service: MongoService = Depends(Provide[Container.mongo_service])):
    try:
        # 定義聚合管道
        pipeline = [
            # A. 篩選使用者
            { "$match": { "Role": usr } },
            # B. 先按時間排序
            { "$sort": { "timestamp": -1 } },
            # C. 核心：按 session_id 分組，取每一組的第一筆 (即最新一筆)
            { "$group": {
                "_id": "$session_id",
                "Question": { "$first": "$Question" },
                "timestamp": { "$first": "$timestamp" }
            }},
            # D. 分組後重新按時間排序 (讓最新的 session 排在最前)
            { "$sort": { "timestamp": -1 } },
            # E. 限制 10 筆
            { "$limit": 10 }
        ]
        # 執行聚合查詢
        df = mongo_service.aggregate_to_df(pipeline, collection_name=iap_CHAT_HISTORY_COLLECTION)
        if df.empty:
            return []
        df['session_id'] = df['_id']
        result = list([tuple(n) for n in df[['session_id', 'Question', 'timestamp']].dropna().values])
        return result
    except Exception as e:
        from fast_api_service.api_errors import log_and_build_public_http_exception

        raise log_and_build_public_http_exception(
            endpoint=route,
            exc=e,
            input_para={"usr": "history_session_id"},
            slack_notify=send_py_error_msg_to_slack,
        )

route = get_url_path(url=pe_api_url.history_session)
@router.get(route, tags=["iap-pe"], status_code=status.HTTP_200_OK)
@inject
async def get_history_session(session_id: str="b0ee604b-08e9-4241-94ec-2f5e70f627e0",
                              mongo_service: MongoService = Depends(Provide[Container.mongo_service])):
    try:
        # 創建 MongoDB 類別的實例
        records = mongo_service.find_list(session_id=session_id, collection_name=iap_CHAT_HISTORY_COLLECTION)
        return _history_records_to_response(records)
    except Exception as e:
        from fast_api_service.api_errors import log_and_build_public_http_exception

        raise log_and_build_public_http_exception(
            endpoint=route,
            exc=e,
            input_para={"session_id": session_id},
        )

route_delete = get_url_path(url=pe_api_url.history_session) 
@router.delete(route_delete, tags=["iap-pe"], status_code=status.HTTP_200_OK)
@inject
async def delete_history_session(
    session_id: str, 
    mongo_service: MongoService = Depends(Provide[Container.mongo_service])
):
    try:
        result = mongo_service.delete_many(query={'session_id': session_id}, collection_name=iap_CHAT_HISTORY_COLLECTION)
        return {
            "status": "success",
            "session_id": session_id,
            "deleted_count": result.deleted_count
        }
    except Exception as e:
        from fast_api_service.api_errors import log_and_build_public_http_exception

        raise log_and_build_public_http_exception(
            endpoint=route_delete,
            exc=e,
            input_para={"session_id": session_id},
        )


def _extract_filename_and_pages(file_info):
    # 假設檔名格式以 `.pdf` 結尾，頁數為數字，並以 'page:' 開頭
    filename_pattern = r'(.+?)_?\d*\.pdf'  # 匹配所有字母、數字和下劃線，直到 .pdf
    page_pattern = r'第\s*(\d+)頁'  # 匹配 "第" 開頭，後面接數字的格式
    filename_match = re.search(filename_pattern, file_info)
    if filename_match:
        filename = filename_match.group(0).replace(".pdf", "")
    else:
        filename=""
    page_match = re.search(page_pattern, file_info)
    if page_match:
        pages = int(page_match.group(1))
    else:
        pages = None
    return {"filename": filename, "page": pages}


def _resolve_pe_image_path(file_info: str, project_name: str) -> str:
    """僅允許讀取白名單目錄內圖片（與 iap_ae 相同策略，避免任意路徑讀取）。"""
    from common.path_security import UnsafePathError, pe_image_roots, resolve_allowed_file_path

    roots = pe_image_roots()
    try:
        if os.path.isfile(file_info):
            return resolve_allowed_file_path(file_info, allowed_roots=roots)
        image_path = get_image_path(file_info, base_dir=project_name)
        if not image_path:
            raise UnsafePathError("image not found")
        return resolve_allowed_file_path(image_path, allowed_roots=roots)
    except UnsafePathError as exc:
        raise AssertionError("Not support") from exc


def get_image_path(file_info, base_dir):
    result = _extract_filename_and_pages(file_info)
    if not result:
        return JSONResponse(content={"image": None})
    filename, page_number = result["filename"], result["page"] # 提取檔名與頁數
    # 組合圖片檔案路徑
    filename = filename.replace(".pdf", "")
    if isinstance(page_number, str):
        page_number = int(page_number)
    else:
        page_number = int(page_number)
    all_path = list_all_files(f"/app/db/images/{base_dir}")
    image_path = [p for p in all_path if f"{filename}/{page_number}" in p]
    image_path = [p for p in image_path if f"/{filename}" in p]
    image_path = [p for p in image_path if f"/{page_number}.jpg" in p]
    image_path = list(set(image_path))
    if len(image_path) != 1 and len(image_path) != 0:
        raise AssertionError("Error, the lenght of the list must be 1")
    elif len(image_path) == 0:
        logger.debug(f"圖片不存在於目錄")
        return None
    # 檢查圖片是否存在
    if os.path.exists(image_path[0]):
        return image_path[0]
    else:
        logger.debug(f"圖片不存在於目錄")
        return None

route = get_url_path(url=pe_api_url.get_image)
@router.get(route, status_code=status.HTTP_200_OK)
async def get_image(file_info: str, project_name: str = "pe"):
    endpoint = route
    t0 = datetime.datetime.now()
    try:
        # 一律經白名單驗證，不再直接把客戶端傳入的絕對路徑交給 PIL
        image_path = _resolve_pe_image_path(file_info, project_name)
        image = Image.open(image_path)
        buf = BytesIO()
        image.save(buf, format="JPEG", quality=100)
        buf.seek(0)
        image_base64 = base64.b64encode(buf.getvalue()).decode()
        t1 = datetime.datetime.now()
        tt = t1-t0
        tt = tt.total_seconds()
        logger.debug(f"圖片{image_path}\n轉換花費:{tt} sec")
        return JSONResponse(content={"image": image_base64})
    except Exception as e:
        from fast_api_service.api_errors import log_and_build_public_http_exception

        raise log_and_build_public_http_exception(
            endpoint=endpoint,
            exc=e,
            input_para={"file_info": file_info, "project_name": project_name},
        )

@router.get("/faqs", response_model=List[str])
@inject
async def get_faqs_query(mongo_service: MongoService = Depends(Provide[Container.mongo_service])):
    try:
        return _collect_faq_questions(mongo_service, iap_CHAT_HISTORY_COLLECTION)
    except Exception as e:
        from fast_api_service.api_errors import log_and_build_public_http_exception

        raise log_and_build_public_http_exception(endpoint="/api/v1/iap/faqs", exc=e)


#TMP!

BASE_URL = "https://faca.compal.com/api/reports"
# =========================================================
# 載入本地 ERROR_CODE 統計表並建立索引
# =========================================================
container = get_container()

def get_db_from_container():
    """
    透過這個函式直接從 Container 拿資料庫實體。
    這樣就完全不需要依賴容易失效的 @inject 跟 .wire() 了！
    """
    return container.sql_db()


# =========================================================
# 共用函式
# =========================================================
def get_latest_summary_df(engine) -> pd.DataFrame:
    try:
        df = pd.read_sql_table("pe_faca_error_code_summary", con=engine)
        if df.empty:
            return df
        df["ERROR_CODE"] = df["ERROR_CODE"].astype(str)
        df["COUNT"] = df["COUNT"].astype(int)
        df["TOTAL"] = df["TOTAL"].astype(int)
        df["PERCENTAGE"] = df["PERCENTAGE"].astype(float)
        
        if "ISSUE_ID_LIST" not in df.columns:
            df["ISSUE_ID_LIST"] = ""
        return df
    except Exception as e:
        print(f"[ERROR] 讀取資料庫失敗: {e}")
        return pd.DataFrame()

def parse_issue_id_list(value: Any) -> List[str]:
    if pd.isna(value) or value == "":
        return []
    if isinstance(value, list):
        return value
    value = str(value).strip()
    if value.startswith("[") and value.endswith("]"):
        try:
            parsed = ast.literal_eval(value)
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
        except Exception:
            pass
    return [x.strip() for x in value.split(";") if x.strip()]

async def fetch_error_code(client: httpx.AsyncClient, issue_id: str) -> Optional[str]:
    params = {
        "SHIFT": "", "FUNCTION_DESC": "", "ISSUE_ID": issue_id,
        "ERROR_CODE": "", "syslang": "ZH"
    }
    try:
        response = await client.get(BASE_URL, params=params, timeout=30.0)
        response.raise_for_status()
        json_data = response.json()
        reports = json_data.get("data", [])
        if reports:
            return reports[0].get("basic_information", {}).get("ERROR_CODE")
    except httpx.RequestError as e:
        print(f"[ERROR] HTTP request failed for issue_id={issue_id}, error={e}")
    except Exception as e:
        print(f"[ERROR] Unexpected error for issue_id={issue_id}, error={e}")
    return None


async def _build_error_code_issue_map(issue_ids: List[str]) -> dict[str, List[str]]:
    error_code_to_issue_ids = defaultdict(list)
    async with httpx.AsyncClient(verify=False) as client:
        tasks = [fetch_error_code(client, iid) for iid in issue_ids]
        fetched_codes = await asyncio.gather(*tasks)

    for issue_id, error_code in zip(issue_ids, fetched_codes):
        if error_code:
            error_code_to_issue_ids[error_code].append(issue_id)
    return error_code_to_issue_ids


def _build_errorcode_summary_dict(summary_df: pd.DataFrame) -> dict[str, list[dict]]:
    summary_dict = {}
    if summary_df.empty:
        return summary_dict
    for err_code, group in summary_df.groupby("ERROR_CODE"):
        sorted_group = group.sort_values("COUNT", ascending=False)
        summary_dict[str(err_code)] = sorted_group.to_dict("records")
    return summary_dict


def _build_errorcode_statistics(
    error_code_to_issue_ids: dict[str, List[str]],
    summary_dict: dict[str, list[dict]],
) -> dict[str, list[dict]]:
    equipment_statistics = []
    for error_code in error_code_to_issue_ids:
        records = summary_dict.get(error_code, [])
        if not records:
            equipment_statistics.append({
                "ErrorCode": error_code,
                "issue_total_count": 0,
                "problem_statistics": [],
                "message": "資料庫統計表查無此 ERROR_CODE"
            })
            continue

        problem_statistics = [
            {
                "category": row["ISSUE_TYPE"],
                "count": row["COUNT"],
                "percentage": row["PERCENTAGE"],
            }
            for row in records
        ]
        equipment_statistics.append({
            "ErrorCode": error_code,
            "issue_total_count": records[0]["TOTAL"],
            "problem_statistics": problem_statistics,
        })
    return {"errorcode_statistics": equipment_statistics}


def _build_errorcode_issue_list(summary_df: pd.DataFrame, error_codes: List[str]) -> dict[str, list[dict]]:
    equipment_issue_list = []
    for error_code in error_codes:
        if summary_df.empty:
            result_df = pd.DataFrame()
        else:
            result_df = summary_df[summary_df["ERROR_CODE"] == str(error_code)].copy()
            result_df = result_df.sort_values("COUNT", ascending=False)

        if result_df.empty:
            equipment_issue_list.append({
                "ErrorCode": error_code,
                "problem_statistics": [],
                "message": "資料庫統計表查無此 ERROR_CODE",
            })
            continue

        problem_statistics = [
            {
                "category": row["ISSUE_TYPE"],
                "IssueID_list": parse_issue_id_list(row.get("ISSUE_ID_LIST", "")),
            }
            for _, row in result_df.iterrows()
        ]
        equipment_issue_list.append({
            "ErrorCode": error_code,
            "problem_statistics": problem_statistics,
        })
    return {"equipment_issue_list": equipment_issue_list}



# =========================================================
# API：iap 設備異常分析 API
# =========================================================
@router.post(
    "/analyst",
    summary="iap 設備異常分析 API",
    description="""
此 API 用於根據指定的 IssueID 清單查詢 ERROR_CODE，
再由本地 ERROR_CODE 統計表回傳 ISSUE_TYPE 統計結果。

## 輸入參數說明

### IssueID

- 型別：array[string]
- 必填：是
- 說明：
  異常事件 IssueID 清單。
  系統會根據輸入的 IssueID 查詢對應異常資料並進行統計分析。
"""
)
async def analyst_iap(
    request: iapAnalystRequest,
    db = Depends(get_db_from_container)
):
    engine = db.engine
    error_code_to_issue_ids = await _build_error_code_issue_map(request.IssueID)
    summary_df = get_latest_summary_df(engine)
    summary_dict = _build_errorcode_summary_dict(summary_df)
    return _build_errorcode_statistics(error_code_to_issue_ids, summary_dict)

# =========================================================
# API：iap 設備異常分析詳細資料 API
# =========================================================

@router.post(
    "/analyst-detail",
    summary="iap 設備異常分析詳細資料 API",
    description="""
此 API 用於根據指定的 ErrorCode 清單查詢設備異常資料，
取得該設備各問題類別所對應的 IssueID 清單。

## 輸入參數說明

### ErrorCode

- 型別：array[string]
- 必填：是
- 說明：
  設備 ID 清單。
"""
)
async def analyst_detail_iap(
    request: iapAnalystDetailRequest,
    db = Depends(get_db_from_container)
):
    engine = db.engine
    summary_df = get_latest_summary_df(engine)
    return _build_errorcode_issue_list(summary_df, request.ErrorCode)