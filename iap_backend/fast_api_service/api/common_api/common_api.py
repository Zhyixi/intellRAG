import os, sys


from fast_api_service.api.common_api.schemas import TranslateRequest, TranslateResponse
from services.services import TranslatorService

sys.path.extend(['.', '..'])
from typing import List
from pydantic import BaseModel, Field
from common.utils import get_url_path, send_py_error_msg_to_slack
from common.web_search import web_search
from fast_api_service.response import setErrorResponse
from fastapi import APIRouter, Depends, HTTPException, status
import traceback  # 導入 traceback 模組，用於捕獲和格式化異常堆棧信息
import logging  # 導入 logging 模組，用於記錄日誌
from configs.config import common_url
from fastapi.responses import StreamingResponse
from urllib.parse import quote
from fastapi.responses import FileResponse
from dependency_injector.wiring import inject, Provide
from containers import Container


router = APIRouter(prefix="/api/v1/common", tags=["iap-common"])


class WebSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="搜尋關鍵字")
    max_results: int = Field(8, ge=1, le=20)


class WebSearchResult(BaseModel):
    title: str
    url: str
    snippet: str


class WebSearchResponse(BaseModel):
    results: List[WebSearchResult]

route = get_url_path(url=common_url.check_backend)
@router.get(route, status_code=status.HTTP_200_OK)
async def check_backend():
    endpoint = route
    try:
        return {"status": "Backend is ready to accept requests", "code": 200}
    except Exception as e:
        from fast_api_service.api_errors import log_and_build_public_http_exception

        raise log_and_build_public_http_exception(endpoint=endpoint, exc=e)

def search_files_by_keyword(directory: str, keyword: str, extensions: List[str] = None) -> List[str]:
    """
    搜尋指定目錄下包含特定字詞的檔案路徑，可篩選副檔名。

    Args:
        directory (str): 要搜尋的目錄。
        keyword (str): 檔名中包含的字詞。
        extensions (List[str], optional): 要篩選的副檔名清單，例如 ['.pdf', '.txt']。預設為 None，表示搜尋所有檔案類型。

    Returns:
        List[str]: 符合條件的檔案路徑清單。
    """
    matched_files = []
    all = []
    # 遍歷目錄及其子目錄
    for root, dirs, files in os.walk(directory):
        for file in files:
            all.append(file)
            # 檢查檔名中是否包含關鍵字
            if keyword in file:
                # 檢查副檔名是否符合篩選條件
                if extensions is None or any(file.endswith(ext) for ext in extensions):
                    matched_files.append(os.path.join(root, file))
    return matched_files

route = get_url_path(url=common_url.download_pdf)
@router.get(route)
async def download_pdf(file_info:str):
    # 測試範例
    directory = "/app/rag_doc"  # 替換為指定目錄
    keyword = file_info.split(" ")[0]           # 替換為搜尋的關鍵字
    keyword = keyword.rstrip("_")
    file_paths = search_files_by_keyword(directory, keyword)
    if len(file_paths) == 0:
        raise AssertionError(f"download file duplicated")
    file_path = file_paths[0]
    
    try:
        # 對文件名稱進行 URL 編碼
        encoded_file_name = quote("Smart Repair 修護資料 AI 對話查詢系統操作指南_簡中.pdf")
        headers = {
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_file_name}"
        }
        # file = open(file_path, "rb")
        # return StreamingResponse(file, media_type="application/pdf", headers=headers)
        return FileResponse(path=file_path, media_type="application/pdf", headers=headers)
    except Exception as e:
        from fast_api_service.api_errors import log_and_build_public_http_exception

        raise log_and_build_public_http_exception(
            endpoint=route,
            exc=e,
            input_para={"file_info": file_info},
            slack_notify=send_py_error_msg_to_slack,
        )

route = get_url_path(url=common_url.translate_zh2vi)
@router.post(route)
@inject
async def translate_zh2vi(texts:list[str],
                          translate_service: TranslatorService = Depends(Provide[Container.translate_service])):
    response = translate_service.translate_zh2vi(texts=texts)
    return response



route = get_url_path(url=common_url.translate)
@router.post(route, response_model=TranslateResponse) # 指定回傳格式
@inject
async def translate(
    request: TranslateRequest, # <-- 改這裡：接收 Pydantic Model
    translate_service: TranslatorService = Depends(Provide[Container.translate_service])
):
    # 從 request 物件中取出參數傳給 Service
    results = await translate_service.translate(
        texts=request.texts,
        source_lang=request.source_lang,
        target_lang=request.target_lang
    )
    # 回傳符合 TranslateResponse 結構的字典
    return {"results": results}


route_web_search = get_url_path(url=common_url.web_search)


@router.post(route_web_search, response_model=WebSearchResponse)
async def search_web_endpoint(body: WebSearchRequest):
    """網路搜尋（DuckDuckGo），與 MCP tool `search_web` 同源。"""
    endpoint = route_web_search
    try:
        raw = web_search(body.query, max_results=body.max_results)
        return {"results": raw}
    except Exception as e:
        from fast_api_service.api_errors import log_and_build_public_http_exception

        raise log_and_build_public_http_exception(
            endpoint=endpoint,
            exc=e,
            input_para=body.model_dump(),
            slack_notify=send_py_error_msg_to_slack,
        )

