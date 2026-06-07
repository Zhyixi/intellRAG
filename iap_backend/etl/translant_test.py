import logging
from pathlib import Path
import sys, os
import pickle
sys.path.extend(['.', '..'])
from HandleRequest.aeface import iap_ae_report_v1
from configs.config import rag_start_time, rag_end_time
import datetime, inspect
from common.utils import list_all_files, sync_directories
from HandleRequest.peface import iap_report
import pandas as pd
import tqdm
import re
import asyncio
from pathlib import Path
import shutil
from services.services import TranslatorService
from containers import Container
    
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
    if end_date == "":
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

    
    
# TASK
async def main(translator:TranslatorService):
    current_function_name = inspect.currentframe().f_code.co_name
    input_dir = f"/app/rag_doc/iap"
    # fetch data to rag_doc TEST!
    if 1:
        source_directory = 'faca@10.129.128.25:/home/dtduser/faca_server/public/reports/'
        flag = sync_directories(source_directory, input_dir)
        if not flag:
            logging.info("數據同步有誤")
            raise AssertionError("數據同步有誤")    
    logging.info(f"fetch iap_record_log")
    # 取得文件路徑列表
    logging.info(f"fetch data list")
    all_files = list_all_files(input_dir)
    logging.info(f"File source : {len(all_files)}")
    date_lst = get_date_list(start_date=rag_start_time, end_date=rag_end_time) # 取得日期列表
   
    # 取得全部資料
    record_df = pd.DataFrame()
    record_datas = pd.read_csv('翻譯測試.csv')
    if 0:
        datas = record_datas[record_datas['success'] != "Y"]
        datas.reset_index(drop=True, inplace=True)
        progress_bar = tqdm.tqdm(desc="測試翻譯...", total=len(datas))
        for row_id, row in datas.iterrows():
            trans_results = translator.translate([row['original']], target_lang="zh")
            pass
            for result in trans_results:
                if (result['success'] == "N") or (result['success'] != "Y"):
                    print(result)
                    pass
                record_df = pd.concat(objs=[record_df, pd.DataFrame([result])], ignore_index=True)
                if len(record_df) % 10 == 0:
                    record_df.to_csv("翻譯測試.csv", index=False)
        record_df.to_csv("翻譯測試.csv", index=False)
    else:
        response = iap_ae_report_v1(strat_date=date_lst[0], end_date=date_lst[-1])
        total_data_count=len(response['res']['data'])
        progress_bar = tqdm.tqdm(desc="測試翻譯...", total=total_data_count)
        today_datas = response['res']['data']
        for i in range(len(today_datas)):
            data = today_datas[i]
            basic_information = data['basic_information']
            ITEM_NO = basic_information.get('ITEM_NO', "")
            ISSUE_ID = basic_information['ISSUE_ID']
            attachments = data['attachment']
            progress_bar.update(1)
            pass
            if ISSUE_ID in ignore_item_no:
                continue
            tb_translate = {}
            tb_translate["FUNCTION_DESC"] = basic_information["FUNCTION_DESC"]
            for att in attachments:
                if att["DESCRIPTION"] != "":
                    tb_translate[att["FIELD_NAME"]] = att["DESCRIPTION"]
            pass
            for k, v in tb_translate.items():
                if (v == "") or (any(record_datas['original'].str.contains(v))):
                    continue
                trans_results = translator.translate([v], target_lang="zh")
                for result in trans_results:
                    pass
                    if (result['success'] == "N") or (result['success'] != "Y"):
                        print(result)
                        pass
                    record_df = pd.concat(objs=[record_df, pd.DataFrame([result])], ignore_index=True)
                    if len(record_df) % 10 == 0:
                        record_df.to_csv("翻譯測試.csv", index=False)
        record_df.to_csv("翻譯測試.csv", index=False)
    progress_bar.close()
if __name__ == "__main__":
    
    container = Container()
    llm = container.ollama_general_llm()
    translator = TranslatorService(llm=llm)
    asyncio.run(main(translator))
