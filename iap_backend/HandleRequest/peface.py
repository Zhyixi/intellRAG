import os, sys
sys.path.extend(['.', '..'])
import requests, json
import pandas as pd
import re
import os
from configs.config import iap_url, rag_url, common_url

def iap_report(date:str):
    url = iap_url.report(date)
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
    else:
        data = None
    return {"res":data, "status_code":response.status_code}

def iap_report_v2(strat_date:str, end_date:str=None, syslang:str="ZH"):
    url = "http://10.129.137.36:3001/api/reports"
    

    # 1. 整理查詢條件 (參數化，不用在 URL 裡自己串接跟編碼)
    # params = {
    #     "EMP_NO": "20675832",
    #     "TYPE": "Auto",
    #     "SHIFT": "",
    #     "UPLOAD_DATE[]": ["2025/01/01", "2026/02/26"],
    #     "isMaintain": "Y",
    #     "limit": "10",
    #     "page": "1",
    #     "search_type": "writer",
    #     "syslang": syslang
    # }
    
    date_range = [strat_date]
    if end_date:
        date_range.append(end_date)
    params = {
        "UPLOAD_DATE[]": date_range,
        "isMaintain": "Y",
        "syslang": syslang}
    # 1. 偽裝成正常瀏覽器
    if 0:
        headers = None
        proxies = None
        cookies = None
    else:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36 Edg/145.0.0.0",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6"
        }
        # 2. 強制忽略系統 Proxy，確保直連內部 IP
        proxies = {
        "http": None,
        "https": None
        }
        # 2. 帶入登入狀態的 Cookie
        cookies = {
            "faca_account": r"gi\Nora_ruan" # 使用 raw string 確保 \ 不被轉義
        }
        
    # 5. 發送請求
    try:
        response = requests.get(
            url, 
            params=params, 
            cookies=cookies, 
            headers=headers, 
            proxies=proxies, 
            timeout=10
        )
        response.raise_for_status() # 檢查是否有 HTTP 錯誤
        data = response.json()
        return {"res":data, "status_code":response.status_code}
    except Exception as e:
        print(f"Error: {e}")
        return {"res":[], "status_code":response.status_code}

def get_node_info():
    url = iap_url.get_node_info
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
    else:
        data = None
    return {"res":data, "status_code":response.status_code}


def build_image(input_para):
    url = rag_url.build_image
    response = requests.post(url, data = json.dumps(input_para))
    if response.status_code == 200:
        return True
    else:
        return False
    


if __name__ == '__main__':
    # data=iap_report(date="2026/02/25")
    target_languages = {
        "en": "英文",
        "vi": "越南文",
        "pt": "葡萄牙文",
        "es": "西班牙文",
        "zh": "繁體中文"
    }
    for lang_code, lang_name in target_languages.items():
        data = iap_report_v2(strat_date="2025/01/01", end_date="2026/02/26", syslang=lang_code.upper())
        for n in data['res']['data']:
            raw_str = ""
            FUNCTION_DESC = n['basic_information']['FUNCTION_DESC']
            raw_str += FUNCTION_DESC
            for att in n['attachment']:
                ATT_DESCRIPTION = att['DESCRIPTION']
                raw_str += ATT_DESCRIPTION
            print(raw_str)
        pass
    pass
    