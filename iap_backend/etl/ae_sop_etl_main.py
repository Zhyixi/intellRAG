import sys, os
sys.path.extend(['.', '..'])
import asyncio
import logging
from fastapi import FastAPI
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR
from fastapi.middleware.cors import CORSMiddleware
from common.log import init_logging
import etl.iap_ae_sop_task as iap_ae_sop_task
from contextlib import asynccontextmanager
import warnings
from sqlalchemy.exc import SAWarning
from containers import Container

# 初始化日誌
name = 'ae_sop_etl'
init_logging(f'{name}')
# 初始化 APScheduler 排程器
scheduler = AsyncIOScheduler()
# 任務執行成功或失敗的處理
def job_listener(event):
    if event.exception:
        logging.error(f"Job {event.job_id} failed: {event.exception}")
    else:
        logging.info(f"Job {event.job_id} executed successfully")

scheduler.add_listener(job_listener, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.info("Starting scheduler...")
    # 先執行一次任務
    logging.info("Executing initial index build task...")
    table_manager = container.table_manager_repository()
    iap_table_manager = container.iap_table_manager_repository()
    rag_service = container.rag_service()
    # TEST! refresh index
    await iap_ae_sop_task.build_index_task(refresh=1,
                                       table_manager=table_manager,
                                       iap_ae_table_manager=iap_table_manager,
                                       rag_service=rag_service)
    logging.info("Initial index build task completed successfully.")
    # 啟動排程
    scheduler.start()
    scheduler.add_job(iap_ae_sop_task.build_index_task, 'cron', hour=6, minute=0, second=0, kwargs={
        'table_manager': table_manager,
        'iap_ae_table_manager':iap_table_manager,
        'rag_service': rag_service,
        'refresh': 0
    })
    logging.info("Scheduler started and job added.")
    yield
    scheduler.shutdown()



app = FastAPI(title="iap_ae",
                summary="etl sop",
                lifespan=lifespan)
# TEST! DI 注入依賴
container = Container()
container.wire(modules=["etl.iap_ae_sop_task"])
db = container.sql_db()
db.create_database()
app.container = container
# CORS 設定
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 測試排程任務
@app.get("/run-index")
async def run_index(refresh: int = 0):
    table_manager = container.table_manager_repository()
    rag_service = container.rag_service()
    iap_table_manager = container.iap_table_manager_repository()
    asyncio.create_task(iap_ae_sop_task.build_index_task(refresh=refresh,
                                       table_manager=table_manager,
                                       iap_ae_table_manager=iap_table_manager,
                                       rag_service=rag_service))
    return {"message": "Index task started"}

# 啟動測試
@app.get("/ping")
async def ping():
    return {"message": "pong"}

# 使用 uvicorn 啟動
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("ae_sop_etl_main:app", host="0.0.0.0", port=8002, reload=False)
