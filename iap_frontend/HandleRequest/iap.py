import json
import logging
import os
import sys

import pandas as pd
import requests

sys.path.extend([".", ".."])
from configs.config import common_url, pe_api_url
from HandleRequest.api_adapter import normalize_retrieve_response

DEFAULT_TIMEOUT = int(os.getenv("iap_API_TIMEOUT", "600"))
EMPLOYEE_CSV = os.path.join(os.path.dirname(__file__), "..", "Employee.csv")

# 容器內後端 API 必須直連，不可走公司 proxy（proxy 無法解析 iap_backend 等服務名）
_BACKEND_PROXIES = {"http": None, "https": None}


def _request_json(method: str, url: str, **kwargs) -> tuple[dict | list | None, int]:
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    kwargs.setdefault("proxies", _BACKEND_PROXIES)
    try:
        response = requests.request(method, url, **kwargs)
        status = response.status_code
        if status == 200:
            try:
                return response.json(), status
            except ValueError:
                return None, status
        logging.warning("API %s %s -> %s", method, url, status)
        return None, status
    except requests.RequestException as exc:
        logging.error("API %s %s failed: %s", method, url, exc)
        return None, 0


def download_pdf(file_info: str) -> bytes | None:
    url = f"{common_url.download_pdf}?file_info={requests.utils.quote(str(file_info))}"
    try:
        response = requests.get(url, timeout=120, proxies=_BACKEND_PROXIES)
        if response.status_code == 200:
            return response.content
    except requests.RequestException as exc:
        logging.error("download_pdf failed: %s", exc)
    return None


def get_response(input_para: dict) -> dict:
    """POST /api/v1/pe/retrieve，並正規化 results 供 UI 使用。"""
    payload = {
        "role": input_para.get("role"),
        "content": input_para.get("content"),
        "session_id": input_para.get("session_id"),
        "syslang": input_para.get("syslang", "ZH"),
    }
    data, status_code = _request_json(
        "POST",
        pe_api_url.retrieve,
        json=payload,
        headers={"Content-Type": "application/json"},
    )
    if data is None:
        return {"results": [], "status_code": status_code, "chat_response": ""}
    result = normalize_retrieve_response(data)
    result["status_code"] = status_code
    return result


def get_image(file_info: str) -> dict:
    params = {
        "file_info": file_info,
        "project_name": "pe",
    }
    data, status_code = _request_json("GET", pe_api_url.get_image, params=params)
    image = None
    if isinstance(data, dict):
        image = data.get("image")
    return {"res": image, "status_code": status_code}


def _load_employees_from_csv() -> dict:
    df = pd.read_csv(EMPLOYEE_CSV)
    records = df.to_dict(orient="records")
    return {"result": records}


def get_emp() -> dict:
    """後端無 /emp 端點時，改讀本地 Employee.csv。"""
    data, status_code = _request_json("GET", pe_api_url.get_emp)
    if status_code == 200 and data is not None:
        if isinstance(data, dict) and "result" in data:
            return {"res": data, "status_code": status_code}
        return {"res": {"result": data}, "status_code": status_code}
    logging.info("get_emp: 使用本地 Employee.csv")
    return {"res": _load_employees_from_csv(), "status_code": 200}


def get_history_session_id(user_id: str) -> dict:
    url = f"{pe_api_url.history_session_id}?usr={requests.utils.quote(str(user_id))}"
    data, status_code = _request_json("GET", url)
    return {"res": data if data is not None else [], "status_code": status_code}


def get_history_session(session_id: str) -> dict:
    url = (
        f"{pe_api_url.history_session}"
        f"?session_id={requests.utils.quote(str(session_id))}"
    )
    data, status_code = _request_json("GET", url)
    if isinstance(data, dict):
        return {"res": data, "status_code": status_code}
    return {"res": {"result": []}, "status_code": status_code}


def check_backend() -> dict:
    data, status_code = _request_json("GET", common_url.check_backend)
    return {"res": data, "status_code": status_code}
