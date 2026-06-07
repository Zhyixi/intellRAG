import asyncio
import logging
import requests
import urllib3
import pandas as pd

# 關閉因為 verify=False 所產生的 SSL 不安全警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://faca.compal.com/api/reports"

def _sync_faca_etl_job(engine):
    """(同步且阻塞的內部函式) 實際執行爬蟲與 Pandas 處理"""
    logging.info("開始抓取 FACA Reports...")
    params = {
        "SHIFT": "", "FUNCTION_DESC": "", "ISSUE_ID": "", 
        "ERROR_CODE": "", "syslang": "ZH"
    }
    
    try:
        response = requests.get(BASE_URL, params=params, timeout=(10, 60), verify=False)
        response.raise_for_status()
        reports = response.json().get("data", [])
    except requests.RequestException as e:
        logging.error(f"API 請求失敗: {e}")
        return

    if not reports:
        logging.info("未取得任何資料，流程結束。")
        return

    logging.info(f"成功取得 {len(reports)} 筆報告，開始進行資料轉換...")
    
    # --- 1. 轉換明細表 ---
    rows = [
        {
            "ISSUE_ID": info.get("ISSUE_ID"),
            "ERROR_CODE": info.get("ERROR_CODE"),
            "ISSUE_TYPE": info.get("ISSUE_TYPE"),
            "FUNCTION_DESC": info.get("FUNCTION_DESC"),
            "ISSUE_DESC": info.get("ISSUE_DESC"),
            "PLANT": info.get("PLANT"),
            "HAPPEN_DATE": info.get("HAPPEN_DATE"),
        }
        for item in reports
        if (info := item.get("basic_information", {})) 
        and info.get("ERROR_CODE") 
        and info.get("ISSUE_TYPE")
    ]
    detail_df = pd.DataFrame(rows)

    # --- 2. 轉換統計表 ---
    if not detail_df.empty:
        summary_df = (
            detail_df.groupby(["ERROR_CODE", "ISSUE_TYPE"])
            .agg(
                COUNT=("ISSUE_ID", "count"),
                ISSUE_ID_LIST=("ISSUE_ID", lambda x: ";".join(x.dropna().astype(str).unique()))
            ).reset_index()
        )
        summary_df["TOTAL"] = summary_df.groupby("ERROR_CODE")["COUNT"].transform("sum")
        summary_df["PERCENTAGE"] = (summary_df["COUNT"] / summary_df["TOTAL"] * 100).round(1)
        summary_df = summary_df.sort_values(["ERROR_CODE", "COUNT"], ascending=[True, False])
    else:
        summary_df = pd.DataFrame()

    # --- 3. 寫入資料庫 ---
    try:
        logging.info("開始寫入資料庫...")
        # 這裡依賴你從外部傳入的 SQLAlchemy engine
        detail_df.to_sql(name="faca_error_code_detail", con=engine, if_exists="replace", index=False)
        summary_df.to_sql(name="faca_error_code_summary", con=engine, if_exists="replace", index=False)
        logging.info(f"寫入完成！明細: {len(detail_df)} 筆, 統計: {len(summary_df)} 筆")
    except Exception as e:
        logging.error(f"寫入資料庫時發生錯誤: {e}")


async def build_faca_etl_task(engine):
    """
    (非同步對外接口) 供 FastAPI 或排程器呼叫。
    使用 asyncio.to_thread 避免 Pandas 與 Requests 阻塞 FastAPI 事件迴圈。
    """
    logging.info("啟動 FACA ETL 背景任務...")
    # 將同步的阻塞函數丟到背景執行緒運作
    await asyncio.to_thread(_sync_faca_etl_job, engine)