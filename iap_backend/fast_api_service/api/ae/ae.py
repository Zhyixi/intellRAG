import asyncio
import base64
import datetime
from functools import lru_cache
from io import BytesIO
import json
import operator
import os
from PIL import Image
import traceback
from typing import Annotated, Any, Dict, List, Optional, TypedDict, Union
from fastapi.responses import JSONResponse
import httpx
import re
from langchain_core.messages import AnyMessage, SystemMessage, AIMessage, HumanMessage
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.mongodb import MongoDBSaver
from pydantic import BaseModel, Field
from common.utils import custom_jsonable_encoder, get_url_path, replace_invalid_values, send_py_error_msg_to_slack
from configs.config import ae_api_url
from fast_api_service.api.ae.schemas import MessageSchema, MessageSchemaDirect, MessageSchemaDirectV1, ResponsesModel

from fast_api_service.response import setErrorResponse
from services.ae_graphs import process_agent_retrieve
from services.services import MongoService, TranslatorService
from langchain_openai import ChatOpenAI
from services.rag_service import RetriveService
from repositories.repositories import TableManagerRepository
import logging
from fastapi import APIRouter, HTTPException, status, Depends
from dependency_injector.wiring import inject, Provide
from containers import Container, get_container
import pandas as pd
from langchain_core.runnables import RunnableConfig
from langfuse import observe
import uuid
from common.langfuse_tracing import build_agent_run_config
import pymongo



# 建立兩個版本的 Router 以及一個共用的 Router (放 history / image)
router = APIRouter(prefix="/api/v1/ae", tags=["iap-ae"])

iap_ae_CHAT_HISTORY_COLLECTION = "iap_ae_Chat_History"


def extract_device_ids(user_input: str) -> List[str]:
    """從輸入中擷取所有獨立的 10 位設備 ID，並去重保留出現順序。"""
    matches = re.findall(r"(?<!\d)(\d{10})(?!\d)", str(user_input or ""))
    return list(dict.fromkeys(matches))


def normalize_session_id(session_id: Optional[str]) -> str:
    return str(session_id or uuid.uuid4())


def raise_iap_ae_http_error(endpoint: str, exc: Exception, input_para: Any):
    from fast_api_service.api_errors import log_and_build_public_http_exception

    # 完整堆疊僅進 log / Slack；前端維持既有 responseCode 格式，但不帶 traceback
    raise log_and_build_public_http_exception(
        endpoint=endpoint,
        exc=exc,
        input_para=input_para,
        slack_notify=send_py_error_msg_to_slack,
    ) from exc


async def safe_process_agent_retrieve(
    *,
    endpoint: str,
    input_para: Any,
    user_input: str,
    run_config: dict,
    checkpointer: MongoDBSaver,
) -> dict:
    try:
        return await process_agent_retrieve(user_input, run_config, checkpointer)
    except Exception as exc:
        raise_iap_ae_http_error(endpoint, exc, input_para)


def safe_insert_history(
    *,
    endpoint: str,
    input_para: Any,
    mongo_service: MongoService,
    insert_data: dict,
):
    try:
        mongo_service.insert_many(
            insert_data=insert_data,
            collection_name=iap_ae_CHAT_HISTORY_COLLECTION
        )
    except Exception as exc:
        raise_iap_ae_http_error(endpoint, exc, input_para)

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
    endpoint: str,
    input_para: Any,
    user_input: str,
    session_id: str,
    user_role: str,
    run_config: dict,
    checkpointer: MongoDBSaver,
    mongo_service: MongoService,
    ret_type: Optional[str] = None,
) -> dict:
    started_at = datetime.datetime.now()
    final_state = await safe_process_agent_retrieve(
        endpoint=endpoint,
        input_para=input_para,
        user_input=user_input,
        run_config=run_config,
        checkpointer=checkpointer,
    )
    elapsed_seconds = (datetime.datetime.now() - started_at).total_seconds()
    insert_data = _build_history_payload(
        session_id=session_id,
        user_input=user_input,
        user_role=user_role,
        final_state=final_state,
        ret_type=ret_type or final_state.get("intent", "ae_faca"),
        started_at=started_at,
        elapsed_seconds=elapsed_seconds,
    )
    safe_insert_history(
        endpoint=endpoint,
        input_para=input_para,
        mongo_service=mongo_service,
        insert_data=insert_data,
    )
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
    if "_id" in base_data.columns:
        base_data.drop(columns=["_id"], inplace=True)
    base_data = base_data.map(lambda x: custom_jsonable_encoder(x))
    return {"result": replace_invalid_values(base_data).to_dict(orient="records")}


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


def _encode_image_to_base64(file_path: str) -> str:
    image = Image.open(file_path)
    buf = BytesIO()
    image.save(buf, format="JPEG", quality=100)
    buf.seek(0)
    return base64.b64encode(buf.getvalue()).decode()


def _validate_iap_ae_image_path(file_info: str) -> str:
    from common.path_security import UnsafePathError, iap_ae_image_roots, resolve_allowed_file_path

    try:
        return resolve_allowed_file_path(file_info, allowed_roots=iap_ae_image_roots())
    except UnsafePathError as exc:
        raise AssertionError("Not support") from exc





route = get_url_path(url=ae_api_url.invoke)
@router.post(route, response_model=ResponsesModel) # 指定回傳格式
@observe(name="ae_invoke")
@inject
async def invoke(
    input_para: MessageSchema,
    mongo_service: MongoService = Depends(Provide[Container.mongo_service]),
    retrice_service: RetriveService = Depends(Provide[Container.retrive_service]),
    tb_repo: TableManagerRepository = Depends(Provide[Container.table_manager_repository]),
    llm: ChatOpenAI = Depends(Provide[Container.vllm_general_llm]),
    tranlator: TranslatorService = Depends(Provide[Container.translate_service])):
    session_id = normalize_session_id(input_para.session_id)
    user_input = input_para.content
    user_role = input_para.role
    syslang = input_para.syslang
    logging.info(f"{input_para}")
    checkpointer = container.mongo_checkpointer()
    trace_name = "ae_invoke"

    run_config = build_agent_run_config(
        session_id=session_id,
        retrice_service=retrice_service,
        tb_repo=tb_repo,
        llm=llm,
        translator=tranlator,
        version="v2",
        syslang=syslang,
        intent="direct_analysis",
        trace_name=trace_name,
        domain="ae",
        user_role=user_role,
    )
    final_state = await _run_agent_and_store_history(
        endpoint=trace_name,
        input_para=input_para,
        user_input=user_input,
        session_id=session_id,
        user_role=user_role,
        run_config=run_config,
        checkpointer=checkpointer,
        mongo_service=mongo_service,
        ret_type="ae_faca_invoke",
    )
    return ResponsesModel(
            role="assistant",
            results=final_state.get("results", []), 
            chat_response=final_state.get("final_response", "").strip('/not_think'), 
            ret_type="ae_faca_invoke",
            source = final_state.get('source', []),
            suggested_questions= final_state.get("suggested_questions", [])
        )
# ==========================================
# 綁定路由 API 端點 (v1 與 v2)
# ==========================================
route_retrieve = get_url_path(url=ae_api_url.retrieve)
@router.post(route_retrieve, status_code=status.HTTP_200_OK, response_model=ResponsesModel)
@observe(name="ae_retrieve")
@inject
async def retrieve(input_para: MessageSchemaDirect,
                      mongo_service: MongoService = Depends(Provide[Container.mongo_service]),
                      retrice_service: RetriveService = Depends(Provide[Container.retrive_service]),
                      tb_repo: TableManagerRepository = Depends(Provide[Container.table_manager_repository]),llm: ChatOpenAI = Depends(Provide[Container.vllm_general_llm]),
                    tranlator: TranslatorService = Depends(Provide[Container.translate_service]),):
    session_id = normalize_session_id(input_para.session_id)
    user_input = input_para.content
    device_ids = extract_device_ids(user_input)
    user_role = input_para.role
    syslang = input_para.syslang
    logging.info(f"{input_para}")
    t0 = datetime.datetime.now()
    checkpointer = container.mongo_checkpointer()
    trace_name = "ae_retrieve"
    run_config = build_agent_run_config(
        session_id=session_id,
        retrice_service=retrice_service,
        tb_repo=tb_repo,
        llm=llm,
        translator=tranlator,
        version="v3",
        syslang=syslang,
        intent="",
        trace_name=trace_name,
        domain="ae",
        user_role=user_role,
    )
    final_state = await _run_agent_and_store_history(
        endpoint=trace_name,
        input_para=input_para,
        user_input=user_input,
        session_id=session_id,
        user_role=user_role,
        run_config=run_config,
        checkpointer=checkpointer,
        mongo_service=mongo_service,
        ret_type=None,
    )
    tt = (datetime.datetime.now() - t0).total_seconds()
    logging.error(f"iap_ae v3耗時:{tt}")
    return ResponsesModel(
            role="assistant",
            results=final_state.get("results", []), 
            chat_response=final_state.get("final_response", "").strip('/not_think'), 
            ret_type=final_state.get('intent', 'ae_faca'),
            source = final_state.get('source', []),
            suggested_questions= final_state.get("suggested_questions", []),
            device_ids=device_ids
        )

# ==========================================
# 共用周邊 API (History, Image 等)
# ==========================================

route_history_id = get_url_path(url=ae_api_url.history_session_id)
@router.get(route_history_id, tags=["iap-ae"], status_code=status.HTTP_200_OK)
@inject
async def get_history_session_id(usr: str="10038437", mongo_service: MongoService = Depends(Provide[Container.mongo_service])):
    try:
        pipeline = _history_session_id_pipeline(usr=usr, limit=10)
        df = mongo_service.aggregate_to_df(pipeline, collection_name=iap_ae_CHAT_HISTORY_COLLECTION)
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
            input_para={"usr": usr},
            slack_notify=send_py_error_msg_to_slack,
        )

route_history = get_url_path(url=ae_api_url.history_session)
@router.get(route_history, tags=["iap-ae"], status_code=status.HTTP_200_OK)
@inject
async def get_history_session(session_id: str="b0ee604b-08e9-4241-94ec-2f5e70f627e0", mongo_service: MongoService = Depends(Provide[Container.mongo_service])):
    try:
        records = mongo_service.find_list(session_id=session_id, collection_name=iap_ae_CHAT_HISTORY_COLLECTION)
        return _history_records_to_response(records)
    except Exception as e:
        from fast_api_service.api_errors import log_and_build_public_http_exception

        raise log_and_build_public_http_exception(
            endpoint=route_history,
            exc=e,
            input_para={"session_id": session_id},
        )


route_delete = get_url_path(url=ae_api_url.history_session) 
@router.delete(route_delete, tags=["iap-ae"], status_code=status.HTTP_200_OK)
@inject
async def delete_history_session(
    session_id: str, 
    mongo_service: MongoService = Depends(Provide[Container.mongo_service])
):
    try:
        result = mongo_service.delete_many(
            query={'session_id': session_id}, 
            collection_name=iap_ae_CHAT_HISTORY_COLLECTION
        )
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


route_image = get_url_path(url=ae_api_url.get_image)
@router.get(route_image, tags=["iap-ae"], status_code=status.HTTP_200_OK)
async def get_image(file_info: str):
    try:
        file_path = _validate_iap_ae_image_path(file_info)
        return JSONResponse(content={"image": _encode_image_to_base64(file_path)})
    except Exception as e:
        from fast_api_service.api_errors import log_and_build_public_http_exception

        raise log_and_build_public_http_exception(
            endpoint=route_image,
            exc=e,
            input_para={"file_info": file_info},
        )


@router.get("/faqs", response_model=List[str])
@inject
async def get_faqs_query(mongo_service: MongoService = Depends(Provide[Container.mongo_service])):
    try:
        return _collect_faq_questions(mongo_service, iap_ae_CHAT_HISTORY_COLLECTION)
    except Exception as e:
        from fast_api_service.api_errors import log_and_build_public_http_exception

        raise log_and_build_public_http_exception(endpoint="/api/v1/iap_ae/faqs", exc=e)

#TEST! MOCK Group Device ID (By Simpson)
# =========================================================
# 全域設定
# =========================================================

BASE_URL = "https://faca.compal.com/api/aereports"

container = get_container()

def get_db_from_container():
    """
    透過這個函式直接從 Container 拿資料庫實體。
    這樣就完全不需要依賴容易失效的 @inject 跟 .wire() 了！
    """
    return container.sql_db()


# =========================================================
# Request Schema
# =========================================================

class iap_aeAnalystRequest(BaseModel):
    IssueID: Optional[List[str]] = Field(
        default_factory=list,
        description=(
            "異常事件 IssueID 清單。"
            "系統會根據輸入的 IssueID 查詢對應異常資料並進行統計分析。"
        ),
        examples=[
            [
                "SFCA60122028C",
                "SFCA60114025C",
                "SFCA60107010E",
                "SFCA60131011E",
                "SFCA60302012E"
            ]
        ]
    )
    DeviceID: Optional[List[str]] = Field(
        default_factory=list,  # 修正 2：將預設值 "AUTO" 改為空陣列 []
        description=("指定查詢的設備 ID 清單。若未指定 (空陣列)，系統會自動根據 IssueID 反查設備。"),
        examples=[
            [
                "1109063006",
                "1103117049"
            ]
        ]
    )

class iap_aeAnalystDetailRequest(BaseModel):
    DeviceID: List[str] = Field(
        ...,
        description="設備 ID 清單。",
        examples=[
            [
                "1103060038",
                "1106062011"
            ]
        ]
    )

# =========================================================
# iap_ae API 呼叫與資料攤平 (非同步優化版)
# =========================================================

async def fetch_iap_ae_api_async(client: httpx.AsyncClient, params: Dict[str, Any], timeout: int = 30) -> Dict[str, Any]:
    """
    非同步呼叫 iap_ae API。
    """
    try:
        resp = await client.get(
            BASE_URL,
            params=params,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=timeout
        )
        resp.raise_for_status()
        return {
            "res": resp.json(),
            "status_code": resp.status_code,
            "url": str(resp.url)
        }
    except Exception as e:
        status_code = getattr(getattr(e, "response", None), "status_code", None)
        return {
            "res": [],
            "status_code": status_code,
            "error": str(e)
        }

def flatten_iap_ae_data(api_result: Dict[str, Any]) -> pd.DataFrame:
    """
    將 iap_ae API 回傳的巢狀 JSON 資料攤平成 Pandas DataFrame。
    """
    res = api_result.get("res", [])

    if isinstance(res, str):
        try:
            res = json.loads(res)
        except Exception:
            return pd.DataFrame()

    if isinstance(res, dict):
        res = res.get("data") or res.get("result") or res.get("rows") or res

    if not isinstance(res, list):
        return pd.DataFrame()

    rows = []
    for item in res:
        if isinstance(item, str):
            try:
                item = json.loads(item)
            except Exception:
                continue

        if not isinstance(item, dict):
            continue

        basic = item.get("basic_information", {})
        atts = {
            att.get("FIELD_NAME"): att.get("DESCRIPTION", "")
            for att in item.get("attachment", [])
            if isinstance(att, dict)
        }

        rows.append({
            "ISSUE_ID": basic.get("ISSUE_ID", ""),
            "PLANT": basic.get("PLANT", ""),
            "DEVICE_ID": basic.get("DEVICE_ID", ""),
            "DEVICE_NAME": basic.get("DEVICE_NAME", ""),
            "FUNCTION_DESC": basic.get("FUNCTION_DESC", ""),
            "ABNORMAL_DATE": basic.get("ABNORMAL_DATE", ""),
            "ROOT_CAUSE": atts.get("rootCause_description", ""),
            "EXPLAIN_CAUSE": atts.get("explanCause_description", ""),
            "ACTION": atts.get("actionCause_description", "")
        })

    return pd.DataFrame(rows)

async def get_device_ids_by_issue_ids_async(issue_ids: List[str], syslang: str = "ZH") -> pd.DataFrame:
    """
    非同步併發查詢：根據 IssueID 反查 DeviceID。
    """
    dfs = []
    
    # 建立一個全局的 HTTP Client 來重複利用連線
    async with httpx.AsyncClient(verify=False) as client:
        tasks = [
            fetch_iap_ae_api_async(client, {"isMaintain": "Y", "ISSUE_ID": issue_id, "syslang": syslang})
            for issue_id in issue_ids
        ]
        # 平行發送所有請求，大幅減少等待時間
        results = await asyncio.gather(*tasks)

    for api_result in results:
        df = flatten_iap_ae_data(api_result)
        if not df.empty:
            dfs.append(df)

    if not dfs:
        return pd.DataFrame(columns=["ISSUE_ID", "DEVICE_ID", "DEVICE_NAME"])

    df = pd.concat(dfs, ignore_index=True)

    if df.empty or "DEVICE_ID" not in df.columns:
        return pd.DataFrame(columns=["ISSUE_ID", "DEVICE_ID", "DEVICE_NAME"])

    mapping_df = (
        df[["ISSUE_ID", "DEVICE_ID", "DEVICE_NAME"]]
        .dropna()
        .drop_duplicates()
    )

    mapping_df["DEVICE_ID"] = mapping_df["DEVICE_ID"].astype(str).str.strip()
    mapping_df = mapping_df[mapping_df["DEVICE_ID"] != ""]

    return mapping_df


def get_device_data_from_db(db_engine, device_ids: List[str]) -> pd.DataFrame:
    """從資料庫一次性撈取多台設備的明細資料"""
    clean_ids = [str(d).strip() for d in device_ids if str(d).strip()]
    if not clean_ids:
        return pd.DataFrame()

    # 使用參數化查詢避免 SQL Injection，並且一次查詢多個 DEVICE_ID
    format_strings = ','.join(['%s'] * len(clean_ids))
    query = f"SELECT * FROM iap_ae_device_detail WHERE DEVICE_ID IN ({format_strings})"

    try:
        df = pd.read_sql(query, con=db_engine, params=tuple(clean_ids))
        df["DEVICE_ID"] = df["DEVICE_ID"].astype(str)
        return df
    except Exception as e:
        print(f"[ERROR] 讀取資料庫失敗: {e}")
        return pd.DataFrame()

def build_equipment_statistics_from_df(df: pd.DataFrame) -> Dict[str, Any]:
    if df.empty:
        return {"equipment_statistics": []}

    required_cols = ["DEVICE_ID", "DEVICE_NAME", "CATEGORY", "ABNORMAL_DATE"]
    missing_cols = [col for col in required_cols if col not in df.columns]
    
    if missing_cols:
        raise HTTPException(
            status_code=500,
            detail=f"資料庫欄位不足，缺少欄位：{missing_cols}"
        )

    df = df.copy()
    df["ABNORMAL_DATE"] = pd.to_numeric(df["ABNORMAL_DATE"], errors="coerce").fillna(0)

    stats = []

    for device_id, device_df in df.groupby("DEVICE_ID"):
        total = len(device_df)

        # 1. 先計算整台設備的「總異常時間」作為分母
        device_total_time = float(device_df["ABNORMAL_DATE"].sum())
        
        # 修正：使用 regex 清除空白字串，避免 Future Warning
        device_name_series = device_df["DEVICE_NAME"].replace(r"^\s*$", pd.NA, regex=True).dropna()
        device_name = device_name_series.iloc[0] if not device_name_series.empty else ""

        problem_statistics = []
        for category, category_df in device_df.groupby("CATEGORY"):
            category_total_time = float(category_df["ABNORMAL_DATE"].sum())
            
            # 3. 計算時間佔比，並加入分母為 0 的防呆機制
            if device_total_time > 0:
                time_percentage = round((category_total_time / device_total_time) * 100, 1)
            else:
                time_percentage = 0.0
                
            problem_statistics.append({
                "category": str(category),
                "count": int(len(category_df)),
                "count_percentage": round(len(category_df) / total * 100, 1),
                "average_abnormal_time": round(float(category_df["ABNORMAL_DATE"].mean()), 1),
                "total_abnormal_time": round(category_total_time, 1),
                "abnormal_time_percentage": time_percentage,
            })

        # 依照發生次數降冪排序 (保留排序，讓最高頻的異常依然在前面)
        problem_statistics = sorted(problem_statistics, key=lambda x: x["count"], reverse=True)

        stats.append({
            "DeviceID": str(device_id),
            "equipment_name": device_name,
            "issue_total_count": int(total),
            "problem_statistics": problem_statistics 
        })

    return {
        "equipment_statistics": sorted(
            stats,
            key=lambda x: x["issue_total_count"],
            reverse=True
        )
    }


def generate_report_from_db(device_ids: List[str], db_engine) -> Dict[str, Any]:
    # 一次性查詢所有設備
    df = get_device_data_from_db(db_engine, device_ids)

    if df.empty:
        return {
            "equipment_statistics": [],
            "message": "資料庫中找不到任何對應的設備資料"
        }

    found_ids = df["DEVICE_ID"].unique().tolist()
    missing_device_ids = list(set(device_ids) - set(found_ids))

    result = build_equipment_statistics_from_df(df)
    
    return result

def build_equipment_issue_list_from_db(device_ids: List[str], db_engine) -> Dict[str, Any]:
    df = get_device_data_from_db(db_engine, device_ids)
    equipment_issue_list = []
    
    if df.empty:
        return {"equipment_issue_list": equipment_issue_list}

    required_cols = ["ISSUE_ID", "DEVICE_ID", "DEVICE_NAME", "CATEGORY"]
    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        raise HTTPException(
            status_code=500,
            detail=f"資料庫欄位不足，缺少欄位：{missing_cols}"
        )

    for device_id, device_df in df.groupby("DEVICE_ID"):
        device_name_series = device_df["DEVICE_NAME"].replace(r"^\s*$", pd.NA, regex=True).dropna()
        device_name = device_name_series.iloc[0] if not device_name_series.empty else ""

        problem_statistics = []
        for category, category_df in device_df.groupby("CATEGORY"):
            issue_id_list = (
                category_df["ISSUE_ID"]
                .dropna()
                .astype(str)
                .drop_duplicates()
                .tolist()
            )
            problem_statistics.append({
                "category": str(category),
                "IssueID_list": issue_id_list
            })

        equipment_issue_list.append({
            "DeviceID": str(device_id),
            "equipment_name": device_name,
            "problem_statistics": problem_statistics
        })

    return {
        "equipment_issue_list": equipment_issue_list
    }


async def _resolve_iap_ae_device_ids(issue_ids: List[str]) -> List[str]:
    mapping_df = await get_device_ids_by_issue_ids_async(issue_ids)
    if mapping_df.empty:
        return []
    return (
        mapping_df["DEVICE_ID"]
        .dropna()
        .astype(str)
        .str.strip()
        .drop_duplicates()
        .tolist()
    )


def _clean_device_ids(device_ids: List[str]) -> List[str]:
    return [str(d).strip() for d in (device_ids or []) if str(d).strip()]


async def _build_iap_ae_analyst_report(
    *,
    issue_ids: List[str],
    device_ids: List[str],
    db_engine,
) -> Dict[str, Any]:
    if device_ids:
        cleaned_ids = _clean_device_ids(device_ids)
        if not cleaned_ids:
            raise HTTPException(status_code=400, detail="DeviceID 列表內無有效的值")
        return generate_report_from_db(cleaned_ids, db_engine)

    resolved_device_ids = await _resolve_iap_ae_device_ids(issue_ids)
    if not resolved_device_ids:
        return {
            "equipment_statistics": [],
            "message": "無法根據輸入的 IssueID 查到對應 DeviceID"
        }
    return generate_report_from_db(resolved_device_ids, db_engine)
# =========================================================
# API：iap_ae 設備異常分析 API
# =========================================================

@router.post(
    "/analyst",
    summary="iap_ae 設備異常分析 API",
    description="""
此 API 用於根據指定的 IssueID 清單查詢設備異常資料，並回傳設備異常統計結果。

## 輸入參數說明

### IssueID

- 型別：array[string]
- 必填：是
- 說明：
  異常事件 IssueID 清單。
  系統會根據輸入的 IssueID 查詢對應異常資料並進行統計分析。

### DeviceID

- 型別：array[string]
- 必填：否
- 說明：指定的設備 ID 清單。若填寫此欄位，系統將直接撈取這些設備的資料，忽略 IssueID。
"""
)
async def analyst_iap_ae(request: iap_aeAnalystRequest,
    db = Depends(get_db_from_container)
    ):
    db_engine = db.engine
    issue_ids = request.IssueID or []
    device_ids = request.DeviceID or []
    if not device_ids and not issue_ids:
        raise HTTPException(status_code=400, detail="請至少提供 IssueID 或 DeviceID 其中一項")
    return await _build_iap_ae_analyst_report(
        issue_ids=issue_ids,
        device_ids=device_ids,
        db_engine=db_engine,
    )


# =========================================================
# API：iap_ae 設備異常分析詳細資料 API
# =========================================================

@router.post(
    "/analyst-detail",
    summary="iap_ae 設備異常分析詳細資料 API",
    description="""
此 API 用於根據指定的 DeviceID 清單查詢設備異常資料，
取得該設備各問題類別所對應的 IssueID 清單。

## 輸入參數說明

### DeviceID

- 型別：array[string]
- 必填：是
- 說明：
  設備 ID 清單。
"""
)
async def analyst_detail_iap_ae(
    request: iap_aeAnalystDetailRequest,
    db = Depends(get_db_from_container)
    ):
    db_engine = db.engine
    if not request.DeviceID:
        raise HTTPException(
            status_code=400,
            detail="DeviceID 不可為空"
        )

    return build_equipment_issue_list_from_db(
        device_ids=request.DeviceID,
        db_engine=db_engine
    )