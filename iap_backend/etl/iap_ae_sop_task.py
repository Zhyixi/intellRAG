import logging
from pathlib import Path
import sys, os
import pickle

from services.services import TranslatorService
sys.path.extend(['.', '..'])
from configs.config import ENV, rag_url, rag_folder_path, rag_index_dir, rag_input_dir, \
ES_PORT, ES_IP, rag_dim, rag_start_time, rag_end_time
import datetime, inspect
import fitz  # PyMuPDF
from common.utils import list_all_files, sync_directories
from HandleRequest.aeface import iap_ae_report
import pandas as pd
import tqdm
import re
import asyncio
from LLM.RagEngine.build_image import build_pdf_image
from common.utils import generate_chunk_id
from pathlib import Path
import shutil
import uuid
from repositories.repositories import ElasticsearchManagerRepository, TableManagerRepository
from containers import Container
from services.rag_service import RAGService

def clean_directory(path: str):
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
        return True, 1.0
    
    # 1. 匹配常见的乱码特征：大量的控制字元和非标准 Unicode
    # 这里的正则匹配了非打印字符和一些偏僻的扩展区字符
    garbage_pattern = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\u07b0-\u0fff\u1700-\u18ff]')
    garbage_chars = garbage_pattern.findall(text)
    
    # 2. 计算乱码比例
    garbage_ratio = len(garbage_chars) / len(text)
    
    # 3. 结果判定
    is_garbage = garbage_ratio > threshold
    return is_garbage, garbage_ratio


def classify_faca_manual_page(text: str) -> dict:
    text = text or ""
    lowered = text.lower()
    toc_keywords = ("目录", "目錄", "table of contents", "一览表", "一覽表")
    issue_keywords = ("故障", "異常", "异常", "报警", "警報", "alarm", "error", "fault", "不良")
    action_keywords = ("原因", "處置", "处理", "措施", "排除", "解決", "解决", "solution", "troubleshooting")
    page_type = "manual_faca" if any(k in lowered for k in issue_keywords) else "manual_general"
    if any(k in lowered for k in toc_keywords):
        page_type = "toc"
    has_solution = any(k in lowered for k in action_keywords)
    return {
        "faca_relevant": page_type == "manual_faca" and has_solution,
        "content_type": "manual_faca" if page_type == "manual_faca" and has_solution else "manual_general",
        "page_type": page_type,
        "has_solution": has_solution,
        "has_action": has_solution,
        "has_root_cause": "原因" in lowered or "cause" in lowered,
    }

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

container = Container()
container.wire(modules=[__name__])


async def build_index_task(refresh:bool,
                           table_manager: TableManagerRepository,
                           iap_ae_table_manager: TableManagerRepository,
                           rag_service: RAGService):
    current_function_name = inspect.currentframe().f_code.co_name
    input_dir = f"/app/rag_doc/ae_sop"
    
    if refresh:
        drop_flag = table_manager.drop_table(table_name="iap_ae_sop_record_log")
        logging.info(f"Drop table iap_ae_sop_record_log")
        index_dir = rag_index_dir+"/ae_sop"
        clean_directory(index_dir)
        logging.info(f"Refresh {index_dir}")
        snapshot_dir = "/app/db/images/ae_sop"
        clean_directory(snapshot_dir)
        logging.info(f"Refresh {snapshot_dir}")
    translator = TranslatorService(llm=rag_service.ollama_general_llm)
    logging.info(f"fetch data list")
    all_files = list_all_files(input_dir) # 取得所有檔案路徑
    target_languages = {
            "en": "英文",
            "zh": "繁體中文",
            "vi": "越南文",
            "pt": "葡萄牙文",
            "es": "西班牙文"
        }
    progress_bar = tqdm.tqdm(total=len(all_files)*len(target_languages), desc=f"AE SOP mbedding 整體進度")
    refresh_record = []
    # 全部檔案
    for file_idx, file in enumerate(all_files):
        logging.info(f"File source : {round(100*(file_idx+1)/len(all_files), 3)} %")
        pdf_file_texts = read_pdf(file) # 讀取pdf
        snapshot_paths = build_pdf_image(file) # 建立截圖
        # 五種語言 
        for lang_code, lang_name in target_languages.items():
            progress_bar.update(1)
            
            # await rag_service._filter_existing_date(index_name=f'ae_sop_file_{lang_code}')
            if refresh and lang_code not in refresh_record:
                # suceed_flag = await rag_service.drop_index(index_name='ae_sop_file')
                suceed_flag = await rag_service.drop_index(index_name=f'ae_sop_file_{lang_code}')
                refresh_record.append(lang_code) # 避免重複刪除
            # 檔案依據頁面處理
            for pdf_file_page in pdf_file_texts:
                embedding_content = pdf_file_page['content']
                # 檢查是否亂碼多, 亂碼多的去除 or 有出現一些關鍵字的去除
                garbage, garbage_score = check_garbage_text(embedding_content, threshold=0.1)
                if garbage or '目录' in embedding_content or '一览表' in embedding_content:
                    assistant_response = "N"
                else:
                    assistant_response = "Y"
                if "SV660P" in file and (pdf_file_page['page'] >= 539) and (pdf_file_page['page'] <= 572):
                    pass
                elif "SV630A" in file and (pdf_file_page['page'] >= 520) and (pdf_file_page['page'] <= 542):
                    pass
                elif "SV630N" in file and (pdf_file_page['page'] >= 527) and (pdf_file_page['page'] <= 534):
                    pass
                elif "SV660A" in file and (pdf_file_page['page'] >= 539) and (pdf_file_page['page'] <= 566):
                    pass
                elif "SV660N" in file and (pdf_file_page['page'] >= 591) and (pdf_file_page['page'] <= 627):
                    pass
                elif "SV660P" in file and (pdf_file_page['page'] >= 543) and (pdf_file_page['page'] <= 571):
                    pass
                elif "LD2-RS" in file and (pdf_file_page['page'] >= 207) and (pdf_file_page['page'] <= 215):
                    pass
                elif "LD2-CAN" in file and (pdf_file_page['page'] >= 160) and (pdf_file_page['page'] <= 168):
                    pass
                elif "LD5" in file and (pdf_file_page['page'] >= 51) and (pdf_file_page['page'] <= 57):
                    pass
                elif "L8EC" in file and (pdf_file_page['page'] >= 411) and (pdf_file_page['page'] <= 429):
                    pass
                elif "L8P" in file and (pdf_file_page['page'] >= 452) and (pdf_file_page['page'] <= 465):
                    pass
                elif "MR-J4伺服放大器" in file and (pdf_file_page['page'] >= 19) and (pdf_file_page['page'] <= 100):
                    pass
                elif "MR-JET用户手册" in file and (pdf_file_page['page'] >= 16) and (pdf_file_page['page'] <= 112):
                    pass
                elif "DELTA_IA-MDS_VFD-E_UM_TC_20160516" in file and (pdf_file_page['page'] >= 213) and (pdf_file_page['page'] <= 217):
                    pass
                elif "DELTA_IA-ROBOT_DRAStudio_Alarm_UM_TC_20240828" in file and (pdf_file_page['page'] >= 9) and (pdf_file_page['page'] <= 22):
                    pass
                elif "FR-E800"  in file and (pdf_file_page['page'] >= 17) and (pdf_file_page['page'] <= 33):
                    pass
                elif "L7RS系列"  in file and (pdf_file_page['page'] >= 329) and (pdf_file_page['page'] <= 337):
                    pass
                elif "L7系列"  in file and (pdf_file_page['page'] >= 329) and (pdf_file_page['page'] <= 337):
                    pass
                else:
                    assistant_response = "N"
                if assistant_response == "Y":
                    page_metadata = classify_faca_manual_page(embedding_content)
                    if not page_metadata["faca_relevant"]:
                        continue
                    filename = Path(file).name
                    # 建立snapshot 存放位置
                    target = filename.replace(".pdf", "") + f"/{pdf_file_page['page']}.jpg"
                    snapshot_path = next((k for k in snapshot_paths if target in k), "")
                    pdf_metadata = {"file_path":file, 'page_label':pdf_file_page['page'], 'file_name':filename, 'page_snapshot':snapshot_path}
                    pdf_metadata.update(page_metadata)
                    # 翻譯文本
                    translated_content = await translator.translate(
                        texts=[embedding_content], 
                        source_lang="auto", 
                        target_lang=lang_code
                    )
                    if translated_content[0]['success'] != 'Y':
                        logging.info(f"翻譯失敗二次嘗試: {translated_content}")
                        translated_content = await translator.translate(
                            texts=[embedding_content], 
                            source_lang="auto", 
                            target_lang=lang_code,
                            think=True
                        )
                    pdf_metadata.update({"translated_record":translated_content[0]})
                    build_flag = await rag_service.build_nodes(index_name=f"ae_sop_file_{lang_code}",
                        text_content=translated_content[0]['translated'],
                        metadata=pdf_metadata,
                        node_type="file"
                        )
                else:
                    continue
    progress_bar.close()
    logging.info(f"AE sop etl over!!")
    exit()
if __name__ == "__main__":
    asyncio.run(build_index_task(1))