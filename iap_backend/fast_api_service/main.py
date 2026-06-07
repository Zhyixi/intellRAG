import sys
sys.path.extend(['.', '..'])
from fastapi import FastAPI
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from common.log import init_logging
import httpx
import signal, asyncio
import threading
import logging, json
import pandas as pd
import time, os
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from common.cors_settings import build_cors_origins
from fast_api_service.api_errors import sanitize_http_exception_detail
from common.langfuse_tracing import flush_langfuse_client
import os
import datetime
from containers import Container, get_container, shutdown_container
import warnings
warnings.simplefilter("always", ResourceWarning)
pd.set_option('display.max_rows', None)
pd.set_option('display.max_columns', None)
from common.platform import PLATFORM_NAME

init_logging("iap_api")
## Swagger 文件設定
tags_metadata = [
    {
        "name": "iap-pe",
        "description": f"{PLATFORM_NAME} — PE 檢索 API。",
    },
    {
        "name": "iap-common",
        "description": f"{PLATFORM_NAME} 共用 API。",
    },
    {
        "name": "iap-auth",
        "description": f"{PLATFORM_NAME} — 用户注册登录。",
    },
    {
        "name": "iap-notebook",
        "description": f"{PLATFORM_NAME} — 个人笔记本 API。",
    },
    {
        "name": "iap-account",
        "description": f"{PLATFORM_NAME} — 个人管理（API Key / 用量）。",
    },
]

container = get_container()
container.wire(modules=[
    "fast_api_service.api.pe.pe",
    "fast_api_service.api.ae.ae",
    "fast_api_service.api.common_api.common_api",
    "fast_api_service.api.auth.auth",
    "fast_api_service.api.notebook.notebook",
    "fast_api_service.api.notebook.documents",
    "fast_api_service.api.account.account",
])
db = container.sql_db()
from models.user_models import User, UserApiKey  # noqa: F401 — register tables
db.create_database()



@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.info("API initialize...")
    try:
        from services.document_worker import mark_stale_processing_docs
        from services.job_service import JobService

        mongo = container.mongo_service()
        redis = container.redis_client()
        n = mark_stale_processing_docs(mongo, JobService(redis))
        if n:
            logging.info("Marked %s stale document jobs as interrupted", n)
    except Exception as exc:
        logging.warning("Startup job recovery skipped: %s", exc)
    yield
    shutdown_container()

app = FastAPI(
    title=PLATFORM_NAME,
    summary=f"{PLATFORM_NAME} — RAG 檢索與 Agent 服務",
    version="2.0.0",
    openapi_tags=tags_metadata,
    lifespan=lifespan,
)
app.container = container
from fastapi.staticfiles import StaticFiles
# 靜態資源僅允許讀取專案目錄下檔案（避免 /static/../../etc/passwd 類路徑穿越）
_STATIC_ROOT = os.path.realpath(os.getenv("STATIC_FILES_ROOT", "/app"))


@app.get("/static/{file_path:path}")
async def serve_static(file_path: str, request: Request):
    from common.path_security import UnsafePathError, resolve_allowed_file_path

    try:
        candidate = os.path.join(_STATIC_ROOT, file_path.lstrip("/"))
        safe_path = resolve_allowed_file_path(candidate, allowed_roots=[_STATIC_ROOT])
        logging.info("access static file: %s", safe_path)
        return FileResponse(safe_path)
    except (UnsafePathError, OSError) as exc:
        logging.warning("static file denied: %s (%s)", file_path, exc)
        return JSONResponse(status_code=404, content={"error": "File not found"})


from fast_api_service.api.common_api import common_api
from fast_api_service.api.pe import pe as pe_api
from fast_api_service.api.ae import ae as ae_api
from fast_api_service.api.auth import auth as auth_api
from fast_api_service.api.notebook import notebook as notebook_api
from fast_api_service.api.notebook import documents as notebook_documents_api
from fast_api_service.api.account import account as account_api

app.include_router(common_api.router)
app.include_router(pe_api.router)
app.include_router(ae_api.router)
app.include_router(auth_api.router)
app.include_router(notebook_api.router)
app.include_router(notebook_documents_api.router)
app.include_router(account_api.router)

# CORS：不使用 '*'，否則與 allow_credentials 衝突且過度開放；可經 CORS_ORIGINS 擴充前端網域
_cors_origins = build_cors_origins()
logging.info("CORS allow_origins=%s", _cors_origins)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """兜底：舊程式若仍把 traceback 放進 detail，在此脫敏後再回傳前端。"""
    safe_detail = sanitize_http_exception_detail(exc.detail)
    if safe_detail is exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(status_code=exc.status_code, content=safe_detail)


@app.exception_handler(StarletteHTTPException)
async def starlette_http_exception_handler(request: Request, exc: StarletteHTTPException):
    safe_detail = sanitize_http_exception_detail(exc.detail)
    return JSONResponse(status_code=exc.status_code, content=safe_detail)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """未捕捉例外：僅回傳通用錯誤，完整堆疊寫 log。"""
    from fast_api_service.api_errors import log_and_build_public_http_exception

    http_exc = log_and_build_public_http_exception(
        endpoint=request.url.path,
        exc=exc,
        input_para=None,
    )
    return JSONResponse(status_code=http_exc.status_code, content=http_exc.detail)


@app.middleware("http")
async def langfuse_flush_middleware(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        flush_langfuse_client(container.langfuse_client())
    return response

def signal_handler(sig, frame):
    print("Caught signal:", sig)
    print("Cleaning up resources...")
    loop = asyncio.get_event_loop()
    loop.stop()
    sys.exit(0)

# 確保信號處理僅在主線程中設置
if threading.current_thread() is threading.main_thread():
    signal.signal(signal.SIGINT, signal_handler)

# for unit test
async def get_client():
    async with httpx.AsyncClient() as client:
        yield client

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=44000,
        access_log=True,
        log_config=None,
        reload=False,
        workers=1
    )