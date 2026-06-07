"""IAP Platform — 統一 ETL 排程服務（單一入口）。"""
import asyncio
import logging
import os
import warnings

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import SAWarning

from common.embedding_resolver import apply_embedding_mode_to_config
from common.log import init_logging
from common.platform import PLATFORM_NAME
from configs import config as app_config
from containers import Container
from contextlib import asynccontextmanager

import etl.unified_etl_task as unified_etl_task

warnings.filterwarnings("ignore", category=SAWarning)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy").setLevel(logging.WARNING)
logging.getLogger("elasticsearch").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
os.environ["PYDEVD_DISABLE_FILE_VALIDATION"] = "1"

init_logging("iap_etl")
scheduler = AsyncIOScheduler()


def job_listener(event):
    if event.exception:
        logging.error("Job %s failed: %s", event.job_id, event.exception)
    else:
        logging.info("Job %s executed successfully", event.job_id)


scheduler.add_listener(job_listener, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)

container = Container()
container.wire(modules=["etl.unified_etl_task"])

_embedding_mode = apply_embedding_mode_to_config()
logging.info("%s embedding mode: %s (cloud=%s)", PLATFORM_NAME, _embedding_mode, app_config.rag_model_cloud)


def _cron_kwargs() -> dict:
    hour = 6
    minute = 0
    if app_config.config.has_section("etl_inbox"):
        hour = app_config.config.getint("etl_inbox", "scan_cron_hour", fallback=6)
        minute = app_config.config.getint("etl_inbox", "scan_cron_minute", fallback=0)
    return {"hour": hour, "minute": minute, "second": 0}


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.info("Starting %s unified ETL scheduler...", PLATFORM_NAME)
    table_manager = container.table_manager_repository()
    rag_service = container.rag_service()
    translator = container.translator()

    await unified_etl_task.scan_inbox_task(
        refresh=False,
        table_manager=table_manager,
        rag_service=rag_service,
        translator=translator,
    )

    scheduler.start()
    scheduler.add_job(
        unified_etl_task.scan_inbox_task,
        "cron",
        **_cron_kwargs(),
        kwargs={
            "refresh": False,
            "table_manager": table_manager,
            "rag_service": rag_service,
            "translator": translator,
        },
    )
    logging.info("Scheduler started (cron %s)", _cron_kwargs())
    yield
    scheduler.shutdown()


app = FastAPI(
    title=f"{PLATFORM_NAME} ETL",
    summary="Unified inbox scanner: with_embedding / without_embedding",
    version="2.0.0",
    lifespan=lifespan,
)
app.container = container

db = container.sql_db()
db.create_database()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/ping")
async def ping():
    return {
        "message": "pong",
        "platform": PLATFORM_NAME,
        "embedding_cloud": app_config.rag_model_cloud,
    }


@app.get("/run-scan")
async def run_scan(refresh: int = 0):
    table_manager = container.table_manager_repository()
    rag_service = container.rag_service()
    translator = container.translator()

    async def _run():
        return await unified_etl_task.scan_inbox_task(
            refresh=bool(refresh),
            table_manager=table_manager,
            rag_service=rag_service,
            translator=translator,
        )

    asyncio.create_task(_run())
    return {"message": "Inbox scan started", "refresh": bool(refresh)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("etl.unified_etl_main:app", host="0.0.0.0", port=8000, reload=False)
