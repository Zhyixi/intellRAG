from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field
from containers import Container
import requests
import pandas as pd
import ast
import httpx
import asyncio
from collections import defaultdict

router = APIRouter(
    prefix="/api/v1/pe",
    tags=["iap 設備異常分析 API"]
)


BASE_URL = "https://faca.compal.com/api/reports"
SUMMARY_CSV_PATH = "/app/pe_faca_error_code_issue_type_summary.csv"



# =========================================================
# 載入本地 ERROR_CODE 統計表並建立索引
# =========================================================
summary_df = pd.read_csv(SUMMARY_CSV_PATH, dtype=str)
summary_df["COUNT"] = summary_df["COUNT"].astype(int)
summary_df["TOTAL"] = summary_df["TOTAL"].astype(int)
summary_df["PERCENTAGE"] = summary_df["PERCENTAGE"].astype(float)
if "ISSUE_ID_LIST" not in summary_df.columns:
    summary_df["ISSUE_ID_LIST"] = ""

# 預先將資料依 ERROR_CODE 分組並轉為 Dict，後續 API 直接 O(1) 取值
summary_dict = {}
for error_code, group in summary_df.groupby("ERROR_CODE"):
    # 預先排序好
    sorted_group = group.sort_values("COUNT", ascending=False)
    summary_dict[str(error_code)] = sorted_group.to_dict("records")


def parse_issue_id_list(value: Any) -> List[str]:
    """
    將 CSV 裡的 ISSUE_ID_LIST 轉成 list。

    支援兩種格式：
    1. SFC001;SFC002;SFC003
    2. ["SFC001", "SFC002", "SFC003"]
    """

    if pd.isna(value) or value == "":
        return []

    if isinstance(value, list):
        return value

    value = str(value).strip()

    # JSON / Python list 字串格式
    if value.startswith("[") and value.endswith("]"):
        try:
            parsed = ast.literal_eval(value)
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
        except Exception:
            pass

    # 分號格式
    return [x.strip() for x in value.split(";") if x.strip()]


def get_error_code_by_issue_id(issue_id: str) -> Optional[str]:
    """
    根據 ISSUE_ID 呼叫 FACA API，取得 ERROR_CODE。
    """

    params = {
        "SHIFT": "",
        "FUNCTION_DESC": "",
        "ISSUE_ID": issue_id,
        "ERROR_CODE": "",
        "syslang": "ZH"
    }

    try:
        response = requests.get(
            BASE_URL,
            params=params,
            timeout=30,
            verify=False
        )
        response.raise_for_status()
        json_data = response.json()

    except Exception as e:
        print(f"[ERROR] get_error_code_by_issue_id failed, issue_id={issue_id}, error={e}")
        return None

    reports = json_data.get("data", [])

    if not reports:
        return None

    return reports[0].get("basic_information", {}).get("ERROR_CODE")

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

# =========================================================
# Request Schema：/analyst
# =========================================================

class iapAnalystRequest(BaseModel):
    IssueID: List[str] = Field(
        ...,
        description=(
            "異常事件 IssueID 清單。"
            "系統會根據輸入的 IssueID 查詢對應異常資料並進行統計分析。"
        ),
        examples=[
            [
                "SFCW50826012B",
                "SFCW50612015B",
                "SFCW51106031B",
                "SFCW60108005B",
                "SFCW41228009B"
            ]
        ]
    )


# =========================================================
# Request Schema：/analyst-detail
# =========================================================

class iapAnalystDetailRequest(BaseModel):
    ErrorCode: List[str] = Field(
        ...,
        description="ErrorCode 清單。",
        examples=[
            [
                "6A07",
                "6U99"
            ]
        ]
    )



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
async def analyst_iap(request: iapAnalystRequest):
    error_code_to_issue_ids = defaultdict(list)
    not_found_issue_ids = []

    # 1. 異步併發取得 Error Code
    async with httpx.AsyncClient(verify=False) as client:
        tasks = [fetch_error_code(client, iid) for iid in request.IssueID]
        fetched_codes = await asyncio.gather(*tasks)

    for issue_id, error_code in zip(request.IssueID, fetched_codes):
        if not error_code:
            not_found_issue_ids.append(issue_id)
        else:
            error_code_to_issue_ids[error_code].append(issue_id)

    # 2. 查表組合回傳資料 (使用 O(1) 的 summary_dict)
    equipment_statistics = []

    for error_code, input_issue_ids in error_code_to_issue_ids.items():
        records = summary_dict.get(error_code, [])

        if not records:
            equipment_statistics.append({
                "ErrorCode": error_code,
                "issue_total_count": 0,
                "problem_statistics": [],
                "message": "本地統計表查無此 ERROR_CODE"
            })
            continue

        problem_statistics = [
            {
                "category": row["ISSUE_TYPE"],
                "count": row["COUNT"],
                "percentage": row["PERCENTAGE"]
            }
            for row in records
        ]

        equipment_statistics.append({
            "ErrorCode": error_code,
            "issue_total_count": records[0]["TOTAL"],
            "problem_statistics": problem_statistics
        })

    return {"errorcode_statistics": equipment_statistics}

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
async def analyst_detail_iap_ae(request: iapAnalystDetailRequest):
    equipment_issue_list = []

    for error_code in request.ErrorCode:
        result_df = summary_df[
            summary_df["ERROR_CODE"].astype(str) == str(error_code)
        ].copy()

        result_df = result_df.sort_values("COUNT", ascending=False)

        if result_df.empty:
            equipment_issue_list.append({
                "DeviceID": error_code,
                "equipment_name": error_code,
                "problem_statistics": [],
                "message": "本地統計表查無此 ERROR_CODE"
            })
            continue

        problem_statistics = []

        for _, row in result_df.iterrows():
            problem_statistics.append({
                "category": row["ISSUE_TYPE"],
                "IssueID_list": parse_issue_id_list(row.get("ISSUE_ID_LIST", ""))
            })

        equipment_issue_list.append({
            "DeviceID": error_code,
            "equipment_name": error_code,
            "problem_statistics": problem_statistics
        })

    return {
        "equipment_issue_list": equipment_issue_list
    }