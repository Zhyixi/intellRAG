import logging
from pathlib import Path
import sys, os
import pickle

from services.services import TranslatorService
sys.path.extend(['.', '..'])
from configs.config import ENV, rag_url, rag_folder_path, rag_index_dir, rag_input_dir, \
ES_PORT, ES_IP, rag_dim, rag_start_time, rag_end_time
import datetime, inspect
from common.utils import list_all_files, sync_directories
from HandleRequest.aeface import iap_ae_report, iap_ae_report_0, iap_ae_report_v2
import pandas as pd
import tqdm
import re
import asyncio
from common.utils import generate_chunk_id
from pathlib import Path
import shutil
from repositories.repositories import ElasticsearchManagerRepository, TableManagerRepository
from containers import Container
from services.rag_service import RAGService

def extract_and_format_date(file_path):
    match = re.search(r'/(\d{4})(\d{2})(\d{2})/', file_path)
    if match:
        year, month, day = match.groups()
        formatted_date = f"{year}/{month}/{day}"
        return formatted_date
    else:
        return None

def process_end_date(end_date):
    if end_date == "":
        return datetime.datetime.now()
    elif isinstance(end_date, str):
        date_parts = end_date.split('/')
        return datetime.datetime(int(date_parts[0]), int(date_parts[1]), int(date_parts[2]))
    elif isinstance(end_date, datetime.datetime):
        return end_date
    else:
        raise ValueError(f"Unsupported end_date type: {type(end_date)}")

def get_date_list(start_date: str | datetime.datetime, end_date: str | datetime.datetime):
    end_date = process_end_date(end_date)

    if start_date == "":
        start_date = datetime.datetime(2024, 10, 1).strftime("%Y/%m/%d")
    else:
        start_date = start_date.split('/')
        start_date = datetime.datetime(int(start_date[0]), int(start_date[1]), int(start_date[2]))
    date_list = []
    current_date = start_date
    while current_date <= end_date:
        date_string = current_date.strftime("%Y/%m/%d")
        date_list.append(date_string)
        current_date += datetime.timedelta(days=1)

    return date_list


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

container = Container()
container.wire(modules=[__name__])



def dict_to_chunk_text(data: dict, key_map: dict = None) -> str:
    lines = []
    for k, v in data.items():
        if not v:
            continue
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

        description = str(att.get("DESCRIPTION") or "").strip()
        if "description" in FIELD_NAME.lower() and description:
            att_text_content += f"[{base_name}] {description}\n"

    return att_text_content


def get_attachment_relevance_flags(attachments) -> dict:
    flags = {
        "has_action": False,
        "has_solution": False,
        "has_root_cause": False,
        "has_poor_description": False,
    }
    for att in attachments:
        field_name = str(att.get("FIELD_NAME") or "").lower()
        description = str(att.get("DESCRIPTION") or "").strip()
        if not description or "description" not in field_name:
            continue
        if "action" in field_name:
            flags["has_action"] = True
            flags["has_solution"] = True
        elif "root" in field_name:
            flags["has_root_cause"] = True
        elif "poor" in field_name:
            flags["has_poor_description"] = True
    return flags



async def build_index_task(refresh:bool,
                           table_manager: TableManagerRepository,
                           iap_ae_table_manager: TableManagerRepository,
                           rag_service: RAGService):
    current_function_name = inspect.currentframe().f_code.co_name
    input_dir = f"/app/rag_doc/iap_ae"
    #TEST! 沒有檔案？
    if 0:
        source_directory = 'faca@10.129.128.25:/home/dtduser/faca_server_bruno_version/public/reports/'
        flag = sync_directories(source_directory, input_dir)
        if not flag:
            logging.info("Data of AE that sync meet error.")
            raise AssertionError("Data sync meet error")
        else:
            logging.info("Data of AE that sync completed.")
    if refresh:
        table_manager.drop_table(table_name="iap_ae_record_log")
        logging.info(f"Drop table iap_ae_record_log")
        index_dir = rag_index_dir+"/iap_ae"
        clean_directory(index_dir)
        logging.info(f"Refresh {index_dir}")
        snapshot_dir = "/app/db/images/iap_ae"
        clean_directory(snapshot_dir)
        logging.info(f"Refresh {snapshot_dir}")
    # 1. 定義你要支援的五種語言字典 (代碼: 語言名稱)
    target_languages = {
        "en": "英文",
        "vi": "越南文",
        "zh": "繁體中文",
        "pt": "葡萄牙文",
        "es": "西班牙文"
    }
    translator = TranslatorService(llm=rag_service.ollama_general_llm) #TEST! 保留翻譯
    logging.info(f"fetch data list")
    all_files = list_all_files(input_dir)  #TEST! 保留 未來需要檔案處理
    logging.info(f"File source : {len(all_files)}")
    
    rag_start_time = "2026/03/01"
    rag_end_time = "2026/03/20"
    date_lst = get_date_list(start_date=rag_start_time, end_date=rag_end_time)
    
    
    
    
    refresh_record = [] # 紀錄刪除的index
    progress_index = 0
    for date_idx, date in enumerate(date_lst):
        for lang_code, lang_name in target_languages.items():
            #%% 刪除index
            if refresh and lang_code not in refresh_record:
                suceed_flag = await rag_service.drop_index(index_name=f'iap_ae_issue_{lang_code}')
            progress_index += 1
            response = iap_ae_report_v2(date, syslang=lang_code.upper())
            if response['status_code'] != 200:
                logging.error(f"date:{date} data api error")
                continue
            today_datas = response['res']['data']
            if len(today_datas) == 0:
                logging.error(f"date:{date} data empty")
                continue
            pass
            for i in range(len(today_datas)):
                logging.info(f"ETL, {lang_code}_整體進度{date}~{date_lst[-1]} {round(progress_index / (len(date_lst) * len(target_languages)), 4) * 100}% \n當日進度:{i}/{len(today_datas)} {round(i/len(today_datas), 2)*100}%")
                pass
                data = today_datas[i]
                basic_information = data['basic_information']
                ITEM_NO = ""
                ITEM_NO = basic_information['ITEM_NO']
                ISSUE_ID = basic_information['ISSUE_ID']
                attachments = data['attachment']
                #%% 處理FACA 資料
                if 1:
                    attachments_str = attach_to_chunk_text(attachments=attachments, lang_code=lang_code)
                    keys_to_keep = ['FUNCTION_DESC', 'ISSUE_DESC']
                    filtered_info = {k: basic_information[k] for k in keys_to_keep if k in basic_information}
                    if lang_code not in BASIC_KEY_MAPS:
                        raise AssertionError(f"{lang_code} not support")
                    basic_content = dict_to_chunk_text(
                        data=filtered_info,
                        key_map=BASIC_KEY_MAPS[lang_code]
                    )
                    raw_content = f"""{basic_content}\n{attachments_str}"""
                    relevance_flags = get_attachment_relevance_flags(attachments)
                    metadata = dict(data)
                    metadata.update({
                        "faca_relevant": bool(basic_content.strip() and (attachments_str.strip() or relevance_flags["has_poor_description"])),
                        "content_type": "faca_case",
                        **relevance_flags,
                    })
                    # 動態生成 Prompt，明確告知 LLM 目標語言，並修正資料來源為翻譯後的文本
                    rewrite_prompt = f"""
                    你是一位熟悉電子產品維修與故障分析的技術助理。

                    請根據以下內容，整合並改寫成1~2句語意清楚、自然通順的總結，不要超過20個字，用來讓 AI 進行語意檢索分析。
                    請盡可能的讓關鍵字被保留，例如型號、LED、代碼等專有名詞或具體症狀，並且保留解決辦法，不要過度簡化或刪除資訊。

                    資料如下：
                    {raw_content}

                    請務必以【{lang_name}】自然語言的方式輸出，不需標示欄位名稱。
                    """
                    # 動態決定 index 名稱，例如 iap_ae_issue_vi
                    index_name = f"iap_ae_issue_{lang_code.lower()}"
                    # 建立節點
                    await rag_service.build_nodes(
                        index_name=index_name,
                        input_path=None,
                        rewrite_system_prompt=rewrite_prompt,
                        use_rewrited=False,
                        text_content=raw_content,
                        metadata=metadata,
                        node_type="issue"
                    )

if __name__ == "__main__":
    asyncio.run(build_index_task(1))