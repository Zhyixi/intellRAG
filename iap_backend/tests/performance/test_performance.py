import sys
import time
import asyncio
import logging
from pathlib import Path
from statistics import mean, median  # ✨ 引入 median 來處理極端值

import pandas as pd
import pytest
import httpx
from httpx import AsyncClient


pytestmark = [pytest.mark.performance, pytest.mark.slow, pytest.mark.manual]

# --- 效能測試參數 ---
CONCURRENCY_LEVELS =[1, 2, 3, 4, 5, 6, 7, 8]  # 測試的併發人數
REPEATS = 5                        # 正式實驗的輪數
WARMUP_ROUNDS = 2                  # ✨ 暖機輪數 (讓 DB/LiteLLM/模型 先熱身)
COOLDOWN_SEC = 2.0                 # ✨ 輪次之間的冷卻時間 (釋放 GPU VRAM)
ME = "least-busy_8"                         # ⚡ 記得根據目前測試的目標修改名稱以利區分
REPORT_DIR = Path("/app/tests/reports")

# 定義要測試的 API 集合與輸入參數
URLMAP = {
    "iap_retrieve_v3": {"url": "/api/v1/pe/retrieve", "query": "螺絲浮鎖的原因分析"},
    "iap_ae_retrieve_v3": {"url": "/api/v1/ae/retrieve", "query": "馬達異音的原因分析"}
}

async def send_request(client: AsyncClient, url: str, payload: dict, req_id: int):
    """執行單次請求並記錄耗時"""
    start = time.perf_counter()
    try:
        response = await client.post(url, json=payload, timeout=180.0) 
        status = response.status_code
    except Exception as e:
        status = f"Error: {str(e)}"
    end = time.perf_counter()
    return {
        "req_id": req_id,
        "latency": end - start, 
        "status": status
    }

@pytest.mark.asyncio
async def test_performance_report():
    """
    併發效能測試主程式 (包含暖機、冷卻、中位數防呆機制)
    """
    # 關閉不必要的 Log 避免干擾終端機報表輸出
    logging.getLogger("services").setLevel(logging.ERROR)
    logging.getLogger("common").setLevel(logging.ERROR)
    logging.getLogger("httpx").setLevel(logging.ERROR)
    
    # 💡 提示：使用 ASGITransport 測的是 FastAPI 本身的性能，若要測包含 Nginx/Uvicorn 的真實網路延遲，
    # 建議不使用 transport，並將 base_url 設為真實的 "http://localhost:8000"
    from fast_api_service.main import app

    transport = httpx.ASGITransport(app=app)
    
    # 儲存報表的資料結構
    summary_results_data =[]   # 總表 (中位數)
    detailed_results_data =[]  # 明細表 (每次實驗數據)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for api_tag, v in URLMAP.items():
            url = v['url']
            query = v['query']
            
            print(f"\n{'='*80}")
            print(f"🚀 開始測試 API: {api_tag} | 查詢: '{query}'")
            print(f"{'='*80}")
            
            TEST_PAYLOAD = {
                "role": "10038437",
                "content": query,
                "session_id": f"perf-test-{api_tag}",
                "syslang": "en", 
                "test_flag": False,
            }

            for concurrency in CONCURRENCY_LEVELS:
                print(f"\n⏳[測試維度: N = {concurrency} 人併發 | 暖機 {WARMUP_ROUNDS} 次 | 實測 {REPEATS} 次]")
                
                # ==========================================
                # ✨ 暖機階段 (Warm-up Phase)
                # 建立連線池、預熱模型快取、Elasticsearch 索引預載
                # ==========================================
                print(f"  🔥 開始暖機...")
                for _ in range(WARMUP_ROUNDS):
                    tasks =[send_request(client, url, TEST_PAYLOAD, req_id=i) for i in range(concurrency)]
                    await asyncio.gather(*tasks)
                print(f"  ❄️ 暖機完畢，冷卻 {COOLDOWN_SEC} 秒以釋放資源...")
                await asyncio.sleep(COOLDOWN_SEC)

                # ==========================================
                # 🚀 正式測試階段
                # ==========================================
                rounds_total_times = []
                rounds_first_times = []
                all_user_latencies =[] 

                for run in range(1, REPEATS + 1):
                    print(f"  ➡️ 第 {run}/{REPEATS} 輪測試開始...")
                    run_start = time.perf_counter()
                    
                    # 齊發請求 (併發)
                    tasks =[send_request(client, url, TEST_PAYLOAD, req_id=i+1) for i in range(concurrency)]
                    con_results = await asyncio.gather(*tasks)
                    
                    run_total_time = time.perf_counter() - run_start
                    
                    # 分析這輪的數據
                    con_user_waits = [r['latency'] for r in con_results]
                    all_user_latencies.extend(con_user_waits)
                    
                    con_first_wait = min(con_user_waits)
                    con_last_wait = max(con_user_waits)
                    con_avg_wait = mean(con_user_waits)
                    
                    print(f"      - 整體耗時: {run_total_time:.2f}s | 首位完成: {con_first_wait:.2f}s | 最後完成: {con_last_wait:.2f}s")
                    
                    detailed_results_data.append({
                        "API 名稱": api_tag,
                        "並發人數(N)": concurrency,
                        "測試輪次": run,
                        "整體完工耗時(秒)": round(run_total_time, 2),
                        "最快完工_首位(秒)": round(con_first_wait, 2),
                        "最慢完工_末位(秒)": round(con_last_wait, 2),
                        "單輪平均等待(秒)": round(con_avg_wait, 2)
                    })

                    rounds_total_times.append(run_total_time)
                    rounds_first_times.append(con_first_wait)

                    # ✨ 實施真正的強制冷卻，避免前一輪殘留的 I/O 阻塞下一輪
                    if run < REPEATS:
                        await asyncio.sleep(COOLDOWN_SEC)

                # ==========================================
                # ✨ 計算統計值 (使用中位數取代平均數，抗雜訊)
                # ==========================================
                summary_results_data.append({
                    "API 名稱": api_tag,
                    "並發人數(N)": concurrency,
                    "重複測試次數": REPEATS,
                    "中位數_整體完工時間(秒)": round(median(rounds_total_times), 2),  # ✨ 改用 median
                    "中位數_首位完工時間(秒)": round(median(rounds_first_times), 2),  # ✨ 改用 median
                    "中位數_等待時間(秒)": round(median(all_user_latencies), 2)       # ✨ 改用 median
                })

    # ==========================================
    # 產出整合報表
    # ==========================================
    df_summary = pd.DataFrame(summary_results_data)
    df_detailed = pd.DataFrame(detailed_results_data)
    
    print("\n" + "🌟"*50)
    print(" 📊 效能測試統計摘要報告 (嚴格變數控制 + 中位數)")
    print("🌟"*50)
    
    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', 2000)
    pd.set_option('display.colheader_justify', 'center')
    print(df_summary.to_string(index=False))
    print("🌟"*50)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"併發效能測試報告_{ME}_{int(time.time())}.xlsx"
    
    with pd.ExcelWriter(report_path, engine='xlsxwriter') as writer:
        df_summary.to_excel(writer, sheet_name='防雜訊中位數統計', index=False)
        df_detailed.to_excel(writer, sheet_name='每次實驗詳細數據', index=False)
        
    print(f"\n✅ 測試完成！完整表格已儲存至: {report_path}\n")

if __name__ == "__main__":
    sys.exit(pytest.main(["-v", "-s", "-p", "no:warnings", __file__]))