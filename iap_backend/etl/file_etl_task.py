import logging
from pathlib import Path
import sys, os
import pickle
from common.utils import generate_chunk_id
import requests
from services.file_etl_graph import create_repair_graph
from services.services import TranslatorService
sys.path.extend(['.', '..'])
from configs.config import ENV, rag_url, rag_folder_path, rag_index_dir, rag_input_dir, \
ES_PORT, ES_IP, INDEX_NAME, rag_dim, rag_start_time, rag_end_time
import datetime, inspect
from common.utils import list_all_files, sync_directories
from HandleRequest.peface import iap_report, iap_report_v2
import pandas as pd
import tqdm
import re
import asyncio
from pathlib import Path
import shutil
from repositories.repositories import ElasticsearchManagerRepository, TableManagerRepository
from services.rag_service import RAGService
import fitz  # PyMuPDF
from LLM.RagEngine.build_image import build_pdf_image
from LLM.RagEngine.rag_utils import file2pdf
import time
import zipfile
import os
from pathlib import Path

def extract_zip(zip_path):
    """將 ZIP 檔解壓縮到同目錄下的專屬資料夾"""
    zip_path = Path(zip_path)
    if not zip_path.exists():
        print(f"❌ 找不到檔案: {zip_path}")
        return

# 1. 建議將拼字修正為 extracted，並統一使用 Path 進行路徑安全拼接
    extract_to_dir = Path('/app/rag_doc/extracted') / zip_path.stem
    extract_to_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"🔄 正在解壓縮至: {extract_to_dir} ...")
    
    try:
        # 使用 ZipFile 開啟並解壓縮
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_to_dir)
        print("✅ 解壓縮完成！")
        
    except zipfile.BadZipFile:
        print(f"❌ 錯誤: {zip_path} 不是一個有效的 ZIP 檔案，或檔案已損毀。")
    except Exception as e:
        print(f"❌ 解壓縮發生未預期錯誤: {e}")


def extract_and_format_date(file_path):
    # 使用正則表達式提取日期部分
    match = re.search(r'/(\d{4})(\d{2})(\d{2})/', file_path)
    if match:
        year, month, day = match.groups()
        formatted_date = f"{year}/{month}/{day}"
        return formatted_date
    else:
        return None

def get_date_list(start_date:str, end_date:str):
    if end_date == "" or end_date == None:
        end_date = datetime.datetime.now() # datetime.datetime(2024, 10, 2)
    else:
        end_date=end_date.split('/')
        end_date = datetime.datetime(int(end_date[0]), int(end_date[1]), int(end_date[2]))
    if start_date == "":
        start_date = datetime.datetime(2024, 10, 1)
    else:
        start_date=start_date.split('/')
        start_date = datetime.datetime(int(start_date[0]), int(start_date[1]), int(start_date[2]))
    pass
    date_list = []
    # 生成從開始日期到结束日期的所有日期
    current_date = start_date
    while current_date <= end_date:
        date_string = current_date.strftime("%Y/%m/%d")
        date_list.append(date_string)
        current_date += datetime.timedelta(days=1)  # 每次增加一天
    return date_list



def clean_directory(path: str):
    """
    清除指定資料夾內的所有檔案與子資料夾
    :param path: 要清理的資料夾路徑（str 或 Path 皆可）
    """
    target = Path(path)
    if not target.exists() or not target.is_dir():
        print(f"[⚠️] {target} 不是一個有效資料夾路徑")
        return

    for item in target.iterdir():
        try:
            if item.is_file():
                item.unlink()
            elif item.is_dir():
                shutil.rmtree(item)
        except Exception as e:
            print(f"[❌] 無法刪除 {item}: {e}")
    print(f"[✅] 已清除 {target} 資料夾內所有內容")



def check_garbage_text(text, threshold=0.1):
    if not text:
        return True
    
    # 1. 匹配常见的乱码特征：大量的控制字元和非标准 Unicode
    # 这里的正则匹配了非打印字符和一些偏僻的扩展区字符
    garbage_pattern = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\u07b0-\u0fff\u1700-\u18ff]')
    garbage_chars = garbage_pattern.findall(text)
    
    # 2. 计算乱码比例
    garbage_ratio = len(garbage_chars) / len(text)
    
    # 3. 结果判定
    is_garbage = garbage_ratio > threshold
    return is_garbage, garbage_ratio


def dict_to_chunk_text(data: dict, key_map: dict = None) -> str:
    """
    將 dict 轉換為格式化的文字，每個 key 一行。
    key_map 可用來轉換欄位名稱（例如 key_map['ISSUE_TYPE'] = '問題類型'）
    """
    lines = []
    for k, v in data.items():
        if not v:
            continue
        # 嘗試從 key_map 轉換欄位名稱，否則保留原名
        label = key_map.get(k, k) if key_map else k
        lines.append(f"[{label}] {v}")
    return "\n".join(lines)

ATTACHMENT_LABELS = {
    "zh": {
        "action": "解決方案",
        "explan": "解釋分析",
        "root": "發生原因",
        "study": "經驗總結",
        "poor": "不良描述"
    },
    "en": {
        "action": "Solution",
        "explan": "Explanation / Analysis",
        "root": "Root Cause",
        "study": "Lessons Learned",
        "poor": "Defect Description"
    },
    "vi": {
        "action": "Giải pháp",
        "explan": "Phân tích",
        "root": "Nguyên nhân gốc",
        "study": "Bài học kinh nghiệm",
        "poor": "Mô tả lỗi"
    },
    "es": {
        "action": "Solución",
        "explan": "Explicación / Análisis",
        "root": "Causa raíz",
        "study": "Lecciones aprendidas",
        "poor": "Descripción del defecto"
    },
    "pt": {
        "action": "Solução",
        "explan": "Explicação / Análise",
        "root": "Causa raiz",
        "study": "Lições aprendidas",
        "poor": "Descrição do defeito"
    }
}

BASIC_KEY_MAPS = {
                    "zh": {
                        "FUNCTION_DESC": "功能描述",
                        "ISSUE_DESC": "問題描述"
                    },
                    "en": {
                        "FUNCTION_DESC": "Function Description",
                        "ISSUE_DESC": "Issue Description"
                    },
                    "vi": {
                        "FUNCTION_DESC": "Mô tả chức năng",
                        "ISSUE_DESC": "Mô tả vấn đề"
                    },
                    "es": {
                        "FUNCTION_DESC": "Descripción de la función",
                        "ISSUE_DESC": "Descripción del problema"
                    },
                    "pt": {
                        "FUNCTION_DESC": "Descrição da função",
                        "ISSUE_DESC": "Descrição do problema"
                    }
                }

def attach_to_chunk_text(attachments, lang_code="zh") -> str:

    if lang_code not in ATTACHMENT_LABELS:
        raise AssertionError(f"{lang_code} not support")

    labels = ATTACHMENT_LABELS[lang_code]

    att_text_content = ""

    for att in attachments:
        FIELD_NAME = att["FIELD_NAME"]

        match = re.match(r'^[a-zA-Z]+', FIELD_NAME)
        if not match:
            raise AssertionError(f"原始字串: {FIELD_NAME} -> 無匹配")

        base_name = match.group().lower()

        for key in labels:
            if key in base_name:
                base_name = labels[key]
                break

        if "description" in FIELD_NAME.lower() and att['DESCRIPTION'].strip()!="":
            att_text_content += f"[{base_name}] {att['DESCRIPTION']}\n"

    return att_text_content

def read_pdf(path):
    doc = fitz.open(path)
    texts = []
    for i, page in enumerate(doc):
        blocks = page.get_text("blocks")  # [(x0, y0, x1, y1, text, block_no, line_no, word_no), ...]
        # 依 y0（從上到下）、再依 x0（從左到右）排序
        blocks = sorted(blocks, key=lambda b: (round(b[1]), round(b[0])))
        page_text = "\n".join([b[4].strip() for b in blocks if b[4].strip()])
        if page_text:
            texts.append({"page": i+1, "content": page_text})
    return texts
def split_text_into_chunks(text: str, chunk_size: int = 500, chunk_overlap: int = 50) -> list[str]:
    """
    將長文字切分成固定大小的 chunks，並保留重疊區域，避免語意斷層。
    
    :param text: 要切分的原始長文字
    :param chunk_size: 每個 chunk 的最大字數 (長度)
    :param chunk_overlap: 鄰近 chunk 之間的重疊字數
    :return: 切分後的文字片段列表 (list of strings)
    """
    if not text:
        return []
        
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap 必須小於 chunk_size")

    # 定義切分的優先順序：段落 -> 換行 -> 空格 -> 單字
    separators = ["\n\n", "\n", " ", ""]
    
    def _chunk_recursive(content: str, current_seps: list[str]) -> list[str]:
        # 如果內容已經小於等於目標大小，直接返回
        if len(content) <= chunk_size:
            return [content]
            
        # 如果所有切分符號都用完了，硬性按字數切分
        if not current_seps:
            return [content[i:i + chunk_size] for i in range(0, len(content), chunk_size - chunk_overlap)]
            
        sep = current_seps[0]
        next_seps = current_seps[1:]
        
        # 嘗試用目前的切分符號分割
        if sep == "":
            splits = list(content)
        else:
            splits = content.split(sep)
            
        chunks = []
        current_chunk = ""
        
        for piece in splits:
            # 重建時補回切分符號 (最後一個不補)
            join_sep = sep if sep != "" else ""
            
            # 測試加了這一段後會不會超標
            test_chunk = current_chunk + (join_sep if current_chunk else "") + piece
            
            if len(test_chunk) <= chunk_size:
                current_chunk = test_chunk
            else:
                # 現有的 chunk 有內容，先存起來
                if current_chunk:
                    chunks.append(current_chunk)
                    
                    # 計算重疊區域：從當前 chunk 的尾端往前抓 overlap 長度
                    # 並與即將加入的新 piece 組合
                    overlap_start = max(0, len(current_chunk) - chunk_overlap)
                    current_chunk = current_chunk[overlap_start:] + (join_sep if join_sep else "") + piece
                else:
                    # 單一碎片就超過 chunk_size，交給下一個更細階的切分符號處理
                    chunks.extend(_chunk_recursive(piece, next_seps))
                    
        if current_chunk:
            chunks.append(current_chunk)
            
        return chunks

    return _chunk_recursive(text, separators)


# TASK
async def build_index_task(refresh:bool,
                           table_manager: TableManagerRepository,
                           iap_table_manager: TableManagerRepository,
                           rag_service: RAGService,
                           translator = TranslatorService):
    
    project_name = "file"
    current_function_name = inspect.currentframe().f_code.co_name
    input_dir = f"/app/rag_doc/{project_name}"
    snapshot_dir = f"/app/db/images/{project_name}"
    target_languages = {
        "zh": "繁體中文",
        "en": "英文",
        "vi": "越南文",
        "pt": "葡萄牙文",
        "es": "西班牙文"
    }
    if refresh:
        clean_directory(snapshot_dir)
        logging.info(f"Refresh {snapshot_dir}")
    
    
    logging.info(f"fetch data list")
    zip_files = list_all_files('/app/rag_doc/parsed_results')
    logging.info(f"File source : {len(zip_files)}")
    # 便歷zip_files，並將zip_files中的檔案解壓縮到/app/rag_doc/extracted中 並將解壓縮後的檔案進行處理
    if 0:
        for file_idx, file_path in enumerate(zip_files):
            extract_zip(file_path)
            logging.info(f"File processing : {round(100*(file_idx+1)/len(zip_files), 2)} %")
    # 處理extracted中的檔案，並將檔案進行處理
    all_files = list_all_files('/app/rag_doc/extracted')
    all_files = [file for file in all_files if file.endswith('.md')]
    refresh_record = []
    process_stats = {
        "total_files": len(all_files),
        "with_solution": 0,
        "fallback_attempted": 0,
        "fallback_hit": 0,
    }
    for file_idx, file_path in enumerate(all_files):
        logging.info(f"File processing : {round(100*(file_idx+1)/len(all_files), 2)} %")
        result = await rag_service.file_process_md(file_path)
        if result.get("fallback_attempted"):
            process_stats["fallback_attempted"] += 1
        if result.get("parse_strategy") == "content_json_fallback" and result.get("has_solution"):
            process_stats["fallback_hit"] += 1
        faca_items = result.get("faca_data") or []
        if not result.get("has_solution") or not faca_items:
            continue
        process_stats["with_solution"] += 1
        translator_service = translator if hasattr(translator, "translate") else rag_service.translator
        for lang_code, lang_name in target_languages.items():
            if refresh and lang_code not in refresh_record:
                table_manager.drop_table(table_name=f"file_record_log_{lang_code}")
                logging.info(f"Drop table file_record_log_{lang_code}")
                await rag_service.drop_index(index_name=f"file_{lang_code}")
                refresh_record.append(lang_code)
            for item_idx, item in enumerate(faca_items):
                issue = str(item.get("issue") or "").strip()
                reason = str(item.get("reason") or "").strip()
                solution = str(item.get("solution") or "").strip()
                if not issue or not solution:
                    continue
                source_file_name = str(result.get("source_file_name") or Path(file_path).parent.name).strip() or Path(file_path).name
                raw_content = "\n".join(
                    line for line in [
                        f"[Issue] {issue}",
                        f"[Reason] {reason}" if reason and reason.lower() != "null" else "",
                        f"[Solution] {solution}",
                    ] if line
                )
                metadata = {
                    "file_path": file_path,
                    "file_name": source_file_name,
                    "page_label": item.get("source_page", -1),
                    "page_snapshot": str(item.get("source_snapshot") or ""),
                    "faca_relevant": 1,
                    "content_type": "manual_faca",
                    "page_type": "manual_faca_extract",
                    "has_solution": 1,
                    "has_action": 1,
                    "has_root_cause": 1 if (reason and reason.lower() != "null") else 0,
                    "source_item_idx": item_idx,
                }
                embedding_content = raw_content
                if lang_code != "zh":
                    translated = await translator_service.translate(
                        texts=[raw_content],
                        source_lang="auto",
                        target_lang=lang_code,
                    )
                    if translated[0].get("success") == "Y":
                        embedding_content = translated[0]["translated"]
                        metadata["translated_record"] = translated[0]
                    else:
                        logging.info(f"翻譯失敗，保留原文索引: {translated}")
                await rag_service.build_nodes(
                    index_name=f"file_{lang_code}",
                    text_content=embedding_content,
                    metadata=metadata,
                    node_type="file",
                )
    logging.info(
        "file_etl stats | total=%s with_solution=%s fallback_attempted=%s fallback_hit=%s",
        process_stats["total_files"],
        process_stats["with_solution"],
        process_stats["fallback_attempted"],
        process_stats["fallback_hit"],
    )
        # for lang_code, lang_name in target_languages.items():
        #     log_table_name = f"file_record_log_{lang_code}"
        #     if refresh and lang_code not in refresh_record:
        #         table_manager.drop_table(table_name=log_table_name)
        #         logging.info(f"Drop table {log_table_name}")
        #         suceed_flag = await rag_service.drop_index(index_name=f'file_{lang_code}')
        #         refresh_record.append(lang_code) # 避免重複刪除
        #         pass
        #     if ".pdf" in file_path:
        #         ImagePath = build_pdf_image(file_path=file_path)
        #     else:
        #         pass
        #         pdf_path = file2pdf(file_path)
        #         ImagePath = build_pdf_image(file_path=pdf_path)
        #         file_path = pdf_path # replace
        #     pdf_file_texts = read_pdf(file_path) # 讀取pdf
        #     single_dile_progress_bar = tqdm.tqdm(total=len(pdf_file_texts),
        #                                          desc=f"{file_path} 處理{lang_code}中...")
        #     for pdf_file_page in pdf_file_texts:
        #         single_dile_progress_bar.update(n=1)
        #         embedding_content = pdf_file_page['content']
        #         # 檢查是否亂碼多, 亂碼多的去除 or 有出現一些關鍵字的去除
        #         garbage, garbage_score = check_garbage_text(embedding_content, threshold=0.1)
        #         if garbage or '目录' in embedding_content:
        #             assistant_response = "N"
        #         else:
        #             assistant_response = "Y"
                
        #         if assistant_response == "Y":
        #             filename = Path(file_path).name
        #             # 建立snapshot 存放位置
        #             target = filename.replace(".pdf", "") + f"/{pdf_file_page['page']}.jpg"
        #             snapshot_path = next((k for k in ImagePath if target in k), "")
        #             pdf_metadata = {"file_path":file_path, 'page_label':pdf_file_page['page'], 'file_name':filename, 'page_snapshot':snapshot_path}
        #             rewrite_system_prompt = f"""
        #                 請對內容進行摘要
        #                 請遵循以下原則
        #                 1. 不可產生幻想內容, 須以實際內容為根據
        #                 2. 用一句話回覆, 不超過50字回覆
        #                 請務必以【{lang_name}】自然語言的方式輸出，不需標示欄位名稱。 /no_think
        #             """
        #             rewrite_system_prompt = None
        #             result_chunks = split_text_into_chunks(embedding_content, chunk_size=50, chunk_overlap=10)
                    
                    
        #             chunck_progress = tqdm.tqdm(total=len(result_chunks),
        #                                          desc=f"{file_path} embedding...")
        #             for idx, chunk in enumerate(result_chunks):
        #                 chunck_progress.update(n=1)
        #                 if lang_code != "zh":
        #                     translated_chunk = await translator.translate_batch(
        #                         texts=[chunk], 
        #                         source_lang="auto", 
        #                         target_lang=lang_code
        #                     )
        #                     if translated_chunk[0]['success'] != 'Y':
        #                         logging.info(f"翻譯失敗:{translated_chunk}")
        #                         translated_chunk = await translator.translate_batch(
        #                             texts=[chunk], 
        #                             source_lang="auto", 
        #                             target_lang=lang_code,
        #                             think=True
        #                         )
        #                         if translated_chunk[0]['success'] != 'Y':
        #                             logging.info(f"翻譯失敗:{translated_chunk}")
        #                     pdf_metadata.update({"translated_record":translated_chunk[0]})
        #                     embedding_chunck = translated_chunk[0]['translated']
        #                     pass
        #                 else:
        #                     pdf_metadata.update({"translated_record":""})
        #                     embedding_chunck = chunk
        #                 chunck_id = generate_chunk_id(embedding_chunck)
        #                 exist_df = table_manager.query_table(log_table_name)
        #                 if exist_df.empty:
        #                     exist_chunck = []
        #                 else:
        #                     exist_chunck = list(exist_df['chunck_id'].values)
        #                 if chunck_id in exist_chunck:
        #                     continue
        #                 build_flag = await rag_service.build_nodes(index_name=f'file_{lang_code}',
        #                     text_content=embedding_chunck,
        #                     metadata=pdf_metadata,
        #                     rewrite_system_prompt=rewrite_system_prompt,
        #                     node_type="file"
        #                     )
        #                 if build_flag:
        #                     insert_record_ok = pd.DataFrame({"chunck_id":[chunck_id], "datetime":[datetime.datetime.now()], "log":["ok"]})
        #                     insert_flag = table_manager.insert_dataframe(insert_record_ok, table_name=log_table_name)
        #             chunck_progress.close()
        #         else:
        #             continue
        #     time.sleep(0.2)
if __name__ == "__main__":
    asyncio.run(build_index_task(1))
