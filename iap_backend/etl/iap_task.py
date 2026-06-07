import logging
from pathlib import Path
import sys, os
import pickle

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


ignore_item_no = ["08adb530-2f6c-11ef-a924-1b3abe38165e",
                                    "1080bce0-3399-11ef-a4d5-3d74941074ac",
                                    "18134c00-339b-11ef-8971-752ce38f6d64",
                                    "2720f6b0-2893-11ef-825a-c72b809905bd",
                                    "29dda4f0-3434-11ef-94da-21ac7037bf80",
                                    "2ededa00-2d13-11ef-bedb-f314204b4187",
                                    "2b33dab0-3527-11ef-9d6c-c3f4e61156f4",
                                    "482073b0-2891-11ef-a4ae-bb362e2953f4",
                                    "4fcb48f0-2897-11ef-bb46-27b8c6a54fba",
                                    "53cf48e0-2ed5-11ef-91a3-5338688d71e0",
                                    "58143dc0-3434-11ef-94da-21ac7037bf80",
                                    "6cbaa7b0-2a10-11ef-b0d7-43ba968cdcba",
                                    "82c1b6a0-29fe-11ef-b0d7-43ba968cdcba",
                                    "84673f70-2e13-11ef-8af2-b7f1d9ac7266",
                                    "859e3bc0-3398-11ef-bc15-233557f961d8",
                                    "891571d0-31cf-11ef-8264-51d4dabf29fc",
                                    "8a75a0d0-2889-11ef-8b2a-8f6e00e9b4cc",
                                    "8aa0d980-2f6e-11ef-afe8-cd2f40bacd8d",
                                    "8dfb3b20-352c-11ef-bf6e-4198c859ce39",
                                    "a1a328a0-2f6d-11ef-afe8-cd2f40bacd8d",
                                    "a6393320-2ecb-11ef-81d5-c74719090c17",
                                    "b0b54510-3398-11ef-a386-b7786a2e529b",
                                    "b171c630-2ed5-11ef-91a3-5338688d71e0",
                                    "b268d810-288d-11ef-b8ff-7f9f6ad1b065",
                                    "c6cd6eb0-2888-11ef-bb64-33a308ee89c4",
                                    "e2ff0260-28a6-11ef-93c1-f1282dcf7ae4",
                                    "b1bf62b0-3a96-11ef-96e2-19d60c309823",
                                    "e9706f60-3a96-11ef-96e2-19d60c309823",
                                    "2e9475f0-3f5b-11ef-bc49-072d54efeff2",
                                    "93b49000-426c-11ef-a03c-61458b4e93d0",
                                    "2cd10a50-3a95-11ef-aa0f-61cb40a22ba5",
                                    "051a5820-3a97-11ef-96e2-19d60c309823"]


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
# TASK
async def build_index_task(refresh:bool,
                           table_manager: TableManagerRepository,
                           iap_table_manager: TableManagerRepository,
                           rag_service: RAGService,
                           translator = TranslatorService):
    log_table_name = "iap_record_log"
    project_name = "iap"
    current_function_name = inspect.currentframe().f_code.co_name
    input_dir = f"/app/rag_doc/{project_name}"
    snapshot_dir = f"/app/db/images/{project_name}"
    source_directory = 'faca@10.129.128.25:/home/dtduser/faca_server/public/reports/'
    target_languages = {
        "zh": "繁體中文",
        "en": "英文",
        "vi": "越南文",
        "pt": "葡萄牙文",
        "es": "西班牙文"
    }
    # 手動設定起始時間
    if 0:
        rag_start_time = "2026/01/01"
        rag_end_time = None
    else:
        # for file
        rag_start_time = "2024/06/29"
        rag_end_time = None
    # fetch data to rag_doc
    if 1:
        flag = sync_directories(source_directory, input_dir)
        if not flag:
            logging.info("數據同步有誤")
            raise AssertionError("數據同步有誤")    
    if refresh:
        table_manager.drop_table(table_name=log_table_name)
        logging.info(f"Drop table {log_table_name}")
        clean_directory(snapshot_dir)
        logging.info(f"Refresh {snapshot_dir}")
    
    
    logging.info(f"fetch data list")
    all_files = list_all_files(input_dir)
    logging.info(f"File source : {len(all_files)}")
    date_lst = get_date_list(start_date=rag_start_time, end_date=rag_end_time) # 取得日期列表
    refresh_record = []
    # 2. 跑迴圈依序處理五種語言
    # 取得全部資料
    for date_idx, date in enumerate(date_lst):
        for lang_code, lang_name in target_languages.items():
            if refresh and lang_code not in refresh_record:
                suceed_flag = await rag_service.drop_index(index_name=f'iap_issue_{lang_code}')
                suceed_flag = await rag_service.drop_index(index_name=f'iap_file_{lang_code}')
                refresh_record.append(lang_code) # 避免重複刪除
            #TEST!
            if 1:
                response = iap_report_v2(date, syslang=lang_code.upper())
            else:
                response = iap_report(date)
            if response['status_code'] != 200:
                logging.error(f"date:{date} data api error")
                continue
            today_datas = response['res']['data']
            for i in range(len(today_datas)):
                data = today_datas[i]
                basic_information = data['basic_information']
                ITEM_NO = basic_information.get('ITEM_NO', "")
                ISSUE_ID = basic_information['ISSUE_ID']
                attachments = data['attachment']
                if ISSUE_ID in ignore_item_no:
                    continue
                logging.info(f"ETL, 整體進度{date}~{date_lst[-1]} {round(date_idx/len(date_lst), 2)*100}% \n當日進度:{i}/{len(today_datas)} {round(i/len(today_datas), 2)*100}% 處理 {lang_code}")
                exist_table = table_manager.query_table(table_name=log_table_name)
                pass
                if 1:
                    attachments_str = attach_to_chunk_text(attachments=attachments,lang_code=lang_code)
                    keys_to_keep = ["FUNCTION_DESC", "ISSUE_DESC"]
                    filtered_info = {k: basic_information[k] for k in keys_to_keep if k in basic_information}
                    basic_content = dict_to_chunk_text(
                    data=filtered_info,
                    key_map=BASIC_KEY_MAPS[lang_code]
                    )
                    raw_content = f"{basic_content} \n{attachments_str}"
                    
                    rewrite_system_prompt = f"""
                    你是一位熟悉電子產品維修與故障分析的技術助理。

                    請根據以下內容，整合並改寫成1~2句語意清楚、自然通順的總結，不要超過20個字，用來讓 AI 進行語意檢索分析。
                    請盡可能的讓關鍵字被保留，例如型號、LED、代碼等專有名詞或具體症狀，並且保留解決辦法，不要過度簡化或刪除資訊。

                    資料如下：
                    {raw_content}

                    請務必以【{lang_name}】自然語言的方式輸出，不需標示欄位名稱。
                    """
                    # 動態決定 index 名稱
                    index_name = f"iap_issue_{lang_code.lower()}"
                    pass
                    build_flag = await rag_service.build_nodes(index_name=index_name,
                        input_path = None,
                        text_content = raw_content,
                        metadata = data,
                        rewrite_system_prompt=rewrite_system_prompt,
                        node_type="issue")
                    pass                    
                    if build_flag:
                        insert_record_ok = pd.DataFrame({"date":[date],"issue_id":[ISSUE_ID],"item_id":[ITEM_NO] , "log":["ok"]})
                        insert_flag = table_manager.insert_dataframe(insert_record_ok, table_name=log_table_name)
                        pass
                # File
                if 1:
                    pass
                    relatedFile = [p for p in all_files if ITEM_NO.lower() in p.lower() and '.png' not in p.lower() and '.jpg' not in p.lower()]
                    if len(relatedFile) != 0:
                        pass
                        for file_idx, file in enumerate(relatedFile):
                            logging.info(f"File source : {round(100*(file_idx+1)/len(all_files), 3)} %")
                            if ".pdf" in file:
                                ImagePath = build_pdf_image(file_path=file)
                            else:
                                pass
                                pdf_path = file2pdf(file)
                                ImagePath = build_pdf_image(file_path=pdf_path)
                                file = pdf_path # replace
                            pdf_file_texts = read_pdf(file) # 讀取pdf
                            single_dile_progress_bar = tqdm.tqdm(total=len(pdf_file_texts), desc=f"{file} embedding...")
                            for pdf_file_page in pdf_file_texts:
                                single_dile_progress_bar.update(n=1)
                                embedding_content = pdf_file_page['content']
                                # 檢查是否亂碼多, 亂碼多的去除 or 有出現一些關鍵字的去除
                                garbage, garbage_score = check_garbage_text(embedding_content, threshold=0.1)
                                pass
                                if garbage or '目录' in embedding_content:
                                    assistant_response = "N"
                                else:
                                    assistant_response = "Y"
                                
                                if assistant_response == "Y":
                                    filename = Path(file).name
                                    # 建立snapshot 存放位置
                                    target = filename.replace(".pdf", "") + f"/{pdf_file_page['page']}.jpg"
                                    snapshot_path = next((k for k in ImagePath if target in k), "")
                                    
                                    pdf_metadata = {"file_path":file, 'page_label':pdf_file_page['page'], 'file_name':filename, 'page_snapshot':snapshot_path}
                                    rewrite_system_prompt = f"""
                                        請對內容進行摘要
                                        請遵循以下原則
                                        1. 不可產生幻想內容, 須以實際內容為根據
                                        2. 用一句話回覆, 不超過50字回覆
                                        請務必以【{lang_name}】自然語言的方式輸出，不需標示欄位名稱。
                                    """
                                    # 翻譯文本
                                    if 1:
                                        if lang_code != "zh":
                                            translated_content = await translator.translate(
                                                texts=[embedding_content], 
                                                source_lang="auto", 
                                                target_lang=lang_code
                                            )
                                            if translated_content[0]['success'] != 'Y':
                                                logging.info(f"翻譯失敗:{translated_content}")
                                                translated_content = await translator.translate(
                                                    texts=[embedding_content], 
                                                    source_lang="auto", 
                                                    target_lang=lang_code,
                                                    think=True
                                                )
                                                if translated_content[0]['success'] != 'Y':
                                                    logging.info(f"翻譯失敗:{translated_content}")
                                            pdf_metadata.update({"translated_record":translated_content[0]})
                                            embedding_content = translated_content[0]['translated']
                                        else:
                                            pdf_metadata.update({"translated_record":""})
                                            
                                    
                                    build_flag = await rag_service.build_nodes(index_name=f'iap_file_{lang_code}',
                                        text_content=embedding_content,
                                        metadata=pdf_metadata,
                                        rewrite_system_prompt=rewrite_system_prompt,
                                        node_type="file"
                                        )
                                else:
                                    continue
                time.sleep(0.2)
if __name__ == "__main__":
    asyncio.run(build_index_task(1))
#TEST! 資料來源改寫
    # if 0:
    #     REPORT_LIST = iap_table_manager.execute_raw_sql("""SELECT * FROM REPORT_LIST WHERE ITEM_NO IN ('3f023eb0-5c31-11f0-bc00-f3924594af0d','3f924d80-ae89-11ef-9f4d-71b368cac732')""")
    #     REPORT_LIST_ATTACHMENT = iap_table_manager.execute_raw_sql("""SELECT * FROM REPORT_LIST_ATTACHMENT WHERE ITEM_NO IN ('3f023eb0-5c31-11f0-bc00-f3924594af0d','3f924d80-ae89-11ef-9f4d-71b368cac732')""")
    #     pass
    #     REPORT_LIST.to_csv("REPORT_LIST.csv", index=False)
    #     REPORT_LIST_ATTACHMENT.to_csv("REPORT_LIST_ATTACHMENT.csv", index=False)
    #     pass
    #     data = iap_table_manager.execute_raw_sql("""
    #         SELECT
    #             *
    #         FROM
    #             (SELECT *
    #             FROM REPORT_LIST t1
    #             WHERE VERSION = (
    #                 SELECT MAX(t2.VERSION)
    #                 FROM REPORT_LIST t2
    #                 WHERE t2.ITEM_NO = t1.ITEM_NO
    #             )
    #             ) i
    #         LEFT JOIN
    #             (SELECT *
    #             FROM REPORT_LIST_ATTACHMENT t1
    #             WHERE VERSION = (
    #                 SELECT MAX(t2.VERSION)
    #                 FROM REPORT_LIST_ATTACHMENT t2
    #                 WHERE t2.ITEM_NO = t1.ITEM_NO
    #             )
    #             ) a
    #         ON i.ITEM_NO = a.ITEM_NO;
    #     """)
    #     data.to_csv("faca_source.csv")
    #     pass