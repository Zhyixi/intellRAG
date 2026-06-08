from fast_api_service.api.common_api.schemas import TranslateRequest, TranslateResponse
from services.services import TranslatorService

from typing import List
from pydantic import BaseModel, Field
from common.utils import get_url_path, send_py_error_msg_to_slack
from common.web_search import web_search
from fast_api_service.response import setErrorResponse
from fastapi import APIRouter, Depends, HTTPException, status
import logging
from configs.config import common_url
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
    """網路搜尋（SearXNG → DuckDuckGo fallback），與 MCP tool `search_web` 同源。"""
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

