import logging
from pathlib import Path
import sys
import os
import asyncio
import aiohttp
from tqdm import tqdm

# ==========================================
# 1. 參數設定
# ==========================================
TOKEN = "eyJ0eXBlIjoiSldUIiwiYWxnIjoiSFM1MTIifQ.eyJqdGkiOiIxNjMwMDIxNyIsInJvbCI6IlJPTEVfUkVHSVNURVIiLCJpc3MiOiJPcGVuWExhYiIsImlhdCI6MTc3OTc4Mzc2NCwiY2xpZW50SWQiOiJsa3pkeDU3bnZ5MjJqa3BxOXgydyIsInBob25lIjoiIiwib3BlbklkIjpudWxsLCJ1dWlkIjoiYmZjZDUzOTgtZDgzOS00NDEzLTk1Y2YtMTYxYzg2MmYxMjljIiwiZW1haWwiOiIiLCJleHAiOjE3ODc1NTk3NjR9.gn3c0DXsw4kwlrq8KSudqp1fxOucQzJJx51jcYDQhwTGv_XZLILE4o8D_qRoWa070njSQ5C5Og_6rQgplzkmcQ"

INPUT_DIR = '/app/rag_doc/file' 
OUTPUT_DIR = '/app/rag_doc/parsed_results'

# 關閉預設的 logging 輸出，避免干擾 tqdm 進度條畫面，改用 tqdm.write
logging.getLogger().setLevel(logging.WARNING)

# ==========================================
# 2. 工具函式
# ==========================================
def list_all_files(directory):
    file_list = []
    for root, _, files in os.walk(directory):
        for file in files:
            file_list.append(os.path.join(root, file))
    return file_list

async def download_file(session: aiohttp.ClientSession, url: str, save_path: str):
    """非同步下載檔案並分塊寫入，避免佔用過多記憶體"""
    try:
        async with session.get(url) as response:
            response.raise_for_status()
            with open(save_path, 'wb') as f:
                async for chunk in response.content.iter_chunked(8192):
                    f.write(chunk)
    except Exception as e:
        tqdm.write(f"[❌] 下載失敗 {save_path}: {e}")

# ==========================================
# 3. 核心處理邏輯 (單一檔案)
# ==========================================
async def process_single_file(semaphore: asyncio.Semaphore, session: aiohttp.ClientSession, file_path: str, token: str, output_dir: str, pbar: tqdm):
    file_name = Path(file_path).name
    headers_json = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    headers_auth = {"Authorization": f"Bearer {token}"}
    
    async with semaphore:
        # ----------------------------------------
        # 步驟 A: 申請網址
        # ----------------------------------------
        batch_url = "https://mineru.net/api/v4/file-urls/batch"
        data = {"model_version": "vlm", "files": [{"name": file_name}]}
        
        try:
            async with session.post(batch_url, headers=headers_json, json=data) as res:
                batch_data = await res.json()
                if res.status != 200 or batch_data.get("code") != 0:
                    tqdm.write(f"[❌] {file_name} 申請失敗: {batch_data.get('message', '')}")
                    pbar.update(1)
                    return
                upload_url = batch_data["data"]["file_urls"][0]
                batch_id = batch_data["data"]["batch_id"]
        except Exception as e:
            tqdm.write(f"[❌] {file_name} 申請異常: {e}")
            pbar.update(1)
            return

        # ----------------------------------------
        # 步驟 B: 上傳檔案 (防 403 Forbidden)
        # ----------------------------------------
        try:
            with open(file_path, "rb") as f:
                file_bytes = f.read()
            async with session.put(upload_url, data=file_bytes, skip_auto_headers=['Content-Type']) as upload_res:
                if upload_res.status != 200:
                    tqdm.write(f"[❌] {file_name} 上傳失敗 HTTP {upload_res.status}")
                    pbar.update(1)
                    return
        except Exception as e:
            tqdm.write(f"[❌] {file_name} 讀取/上傳錯誤: {e}")
            pbar.update(1)
            return

        # ----------------------------------------
        # 步驟 C: 輪詢狀態與下載完整 ZIP 檔
        # ----------------------------------------
        query_url = f"https://mineru.net/api/v4/extract-results/batch/{batch_id}"
        retry_count = 0
        
        while True:
            try:
                async with session.get(query_url, headers=headers_auth) as status_res:
                    if status_res.status != 200:
                        retry_count += 1
                        if retry_count > 5:
                            tqdm.write(f"[❌] {file_name} 輪詢連續失敗，放棄。")
                            break
                        await asyncio.sleep(5)
                        continue
                    
                    retry_count = 0
                    status_data = await status_res.json()
                    
                    if status_data.get("code") != 0:
                        tqdm.write(f"[❌] {file_name} API 錯誤: {status_data}")
                        break
                        
                    extract_results = status_data.get("data", {}).get("extract_result", [])
                    if not extract_results:
                        await asyncio.sleep(5)
                        continue
                        
                    file_result = extract_results[0]
                    state = file_result.get("state")
                    
                    if state == "done":
                        # ----------------------------------------------------
                        # 【改回下載 ZIP】取得官方 API 返回的壓縮檔下載網址
                        # ----------------------------------------------------
                        zip_url = file_result.get("full_zip_url") or file_result.get("zip_url")
                        
                        if zip_url:
                            # 儲存檔名改為 原檔名 + .zip (例如: example.pdf.zip 或 example.zip)
                            zip_path = os.path.join(output_dir, f"{file_name}.zip")
                            await download_file(session, zip_url, zip_path)
                            # tqdm.write(f"[✅] {file_name} ZIP 下載完成")
                        else:
                            tqdm.write(f"[⚠️] {file_name} 解析完成，但找不到 ZIP 連結")
                        break
                        
                    elif state == "failed":
                        tqdm.write(f"[❌] {file_name} MinerU 解析任務失敗。")
                        break
                    else:
                        await asyncio.sleep(5)
            except Exception as e:
                tqdm.write(f"[❌] {file_name} 輪詢異常: {e}")
                break
                
        # 任務結束 (成功/失敗)，進度條前進 1 格
        pbar.update(1)

# ==========================================
# 4. 主任務調度
# ==========================================
async def build_index_task():
    print("開始掃描目錄...")
    if not os.path.exists(INPUT_DIR):
        print(f"[❌] 找不到目錄: {INPUT_DIR}")
        return
        
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    all_files = list_all_files(INPUT_DIR)
    all_files = [f for f in all_files if not Path(f).name.startswith('.')]
    total_files = len(all_files)
    
    if total_files == 0:
        print("沒有檔案需要處理。")
        return

    print(f"總共找到 {total_files} 個檔案。開始上傳與解析...")
    
    # 限制全域最多 8 個檔案在執行上傳或下載
    semaphore = asyncio.Semaphore(8)
    connector = aiohttp.TCPConnector(limit=20, force_close=True)
    
    with tqdm(total=total_files, desc="處理進度", unit="file") as pbar:
        async with aiohttp.ClientSession(connector=connector, trust_env=True) as session:
            running_tasks = []
            for idx, f_path in enumerate(all_files):
                task = asyncio.create_task(
                    process_single_file(semaphore, session, f_path, TOKEN, OUTPUT_DIR, pbar)
                )
                running_tasks.append(task)
                
                # 每分鐘 50 files 限制 -> 每 1.3 秒發起一個任務
                if idx < total_files - 1:
                    await asyncio.sleep(1.3)
                    
            # 等待所有背景排隊的任務完成
            await asyncio.gather(*running_tasks)
            
    print("\n🎉 所有任務執行完畢！")

if __name__ == '__main__':
    asyncio.run(build_index_task())