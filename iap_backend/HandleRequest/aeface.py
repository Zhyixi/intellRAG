import os, sys
sys.path.extend(['.', '..'])
import requests, json
import pandas as pd
import re
import os
from configs.config import iap_ae_url

def iap_ae_report(date:str):
    # 'https://faca.compal.com/api/aereports?isMaintain=Y&UPLOAD_DATE=2024/10/03'
    url = iap_ae_url.report(date)
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
    else:
        data = None
    return {"res":data, "status_code":response.status_code}

def iap_ae_report_0(date:str):
    url = f'https://faca.compal.com/api/aereports?isMaintain=Y&UPLOAD_DATE={date}'
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
    else:
        data = None
    return {"res":data, "status_code":response.status_code}


def iap_ae_report_v1(strat_date:str, end_date:str=None):
    # url = f'https://faca.compal.com/api/aereports?isMaintain=Y&UPLOAD_DATE={strat_date}'
    url = "https://faca.compal.com/api/aereports"
    date_range = [strat_date]
    if end_date:
        date_range.append(end_date)
    params = {
        "UPLOAD_DATE[]": date_range,
        "isMaintain": "Y"}
    # 1. 偽裝成正常瀏覽器
    headers = None
    proxies = None
    cookies = None
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
        pass


def iap_ae_report_v2(strat_date:str, end_date:str=None, syslang:str="ZH"):
    url = "http://10.129.137.36:3001/api/aereports"
    # url = "http://10.129.137.36:3001/api/login/permission?emp_no=20880663"
    date_range = [strat_date]
    if end_date:
        date_range.append(end_date)
    params = {
        "UPLOAD_DATE[]": date_range,
        "isMaintain": "Y",
        "syslang": syslang}
    # 1. 偽裝成正常瀏覽器
    headers = None
    proxies = None
    cookies = None
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

if __name__ == '__main__':
    # data = iap_ae_report(date="2025/01/01")
    # data1 = iap_ae_report_v2(strat_date="2025/01/01", end_date="2026/02/26", syslang="EN")
    data1 = iap_ae_report_v2(strat_date="2026/02/26", end_date="2026/03/03", syslang="VI")
    pass
    for n in data1['res']['data']:
        print(f"FUNCTION_DESC: {n['basic_information']['FUNCTION_DESC']}")
        pass
    pass
    