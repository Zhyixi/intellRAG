import sys, os
sys.path.extend(['.', '..'])
import asyncio
import logging
import warnings
from fastapi import FastAPI
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from sqlalchemy.exc import SAWarning
import uvicorn

# 匯入剛剛分離出來的任務檔案
import etl.ae_statistics_task as ae_statistics_task

# 若你有既有的 common.log 與 containers，請保持匯入
# from common.log import init_logging
from containers import Container

warnings.filterwarnings("ignore", category=SAWarning)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
os.environ["PYDEVD_DISABLE_FILE_VALIDATION"] = "1"

# init_logging('faca_etl') # 請替換為你的 logger 初始化
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- 排程器設定 ---
scheduler = AsyncIOScheduler()

def job_listener(event):
    if event.exception:
        logging.error(f"Job {event.job_id} failed: {event.exception}")
    else:
        logging.info(f"Job {event.job_id} executed successfully")

scheduler.add_listener(job_listener, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)

# --- FastAPI 生命週期 ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.info("Starting scheduler and background processes...")
    
    # 【DI 注入與 DB 獲取區塊】
    db = app.container.sql_db()
    engine = db.engine 
    
    # 把 engine 存到 app.state 中
    app.state.engine = engine
    
    # 1. 啟動時先執行一次任務
    # ⚠️ 【關鍵修正】：使用 run_iap_ae_etl_pipeline，並透過 to_thread 丟到背景執行，避免卡死 FastAPI
    logging.info("Executing initial AE FACA ETL task...")
    await asyncio.to_thread(ae_statistics_task.run_iap_ae_etl_pipeline)
    logging.info("Initial AE FACA ETL task completed successfully.")
    
    # 2. 啟動排程器 (設定每天早上 6:00 執行)
    scheduler.start()
    scheduler.add_job(
        ae_statistics_task.run_iap_ae_etl_pipeline,  # ⚠️ 【關鍵修正】
        'cron', 
        hour=3, minute=0, second=0, 
        id="daily_ae_faca_etl"
    )
    logging.info("Scheduler started and daily job added.")
    
    yield
    
    scheduler.shutdown()
    logging.info("Scheduler shut down.")


# --- FastAPI 應用設定 ---
app = FastAPI(title="AE FACA API", summary="AE FACA 統計任務與排程", lifespan=lifespan)

# 模擬你的 DI 注入設定
container = Container()
container.wire(modules=["__main__", "etl.ae_statistics_task"])
app.container = container

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 測試排程任務
@app.get("/run-etl", summary="手動觸發 ETL 任務")
async def run_etl():
    """
    提供 API 介面手動觸發。
    將任務透過 asyncio.to_thread 丟到背景執行，API 會立刻回應。
    """
    # ⚠️ 【關鍵修正】：使用 run_iap_ae_etl_pipeline
    engine = app.state.engine
    asyncio.create_task(asyncio.to_thread(ae_statistics_task.run_iap_ae_etl_pipeline))
    return {"message": "AE FACA Statistics task started in background!"}

@app.get("/ping")
async def ping():
    return {"message": "pong"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8003)