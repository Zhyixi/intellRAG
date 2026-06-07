# coding: utf-8
import os, sys, re
from pathlib import Path
import glob
import json
import requests
from functools import wraps
import time
import logging
import traceback
import configparser
import pandas as pd
from io import StringIO
from fastapi.encoders import jsonable_encoder
import numpy as np
from urllib.parse import urlparse
import httpx
import subprocess # 執行外部腳本
import uuid
import hashlib


def generate_chunk_id(content: str):
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def get_url_path(url: str) -> str:
    """Return path suffix for APIRouter (router already has prefix=/api/v1/...)."""
    path = urlparse(url).path
    # e.g. /api/v1/common/check_backend -> /check_backend (avoid /api/v1/common/api/v1/...)
    if path.startswith("/api/v1/") and path.count("/") >= 4:
        return "/" + path.split("/", 4)[-1]
    return path

def get_project_root():
    return Path(__file__).parent.parent

def get_config_dir():
    root = get_project_root()
    config_path = os.path.join(root,'configs','config.ini')
    return config_path

def get_config():
    CONFIG_PATH = get_config_dir()
    config = configparser.ConfigParser()
    config.read(CONFIG_PATH, encoding='utf-8')
    return config

config = get_config()
S_URL_OTHERS = config.get('slack', 'S_URL_OTHERS')
S_URL_PROD_ERROR = config.get('slack', 'S_URL_PROD_ERROR')



def error_handler(*args, **kw):
    f = None
    if len(args) == 1 and __builtins__.callable(args[0]):
        f = args[0]
    if f:
        extra_msg = None # default value
    if not f:
        extra_msg = kw.get('extra_msg')
    
    def callable(f):
        @wraps(f)
        def wrap(*args, **kw):
            try:
                result = f(*args, **kw)
                return result
            except Exception as e:
                msg = traceback.format_exc()
                if extra_msg != None: msg += msg + 'extra_msg: ' + extra_msg
                py_name = sys.argv[0]
                logging.error(msg)
                saved_args = locals()
                is_online = saved_args['kw']['is_online']
                # #send_py_error_msg_to_slack(sys.argv[0],msg,is_online)
                raise
        return wrap
    return callable(f) if f else callable

def timing(f):
    @wraps(f)
    def wrap(*args, **kw):
        ts = time.time()
        result = f(*args, **kw)
        t = time.time()-ts
        logging.info(f'func:{f.__name__}: {t:.2f} sec')
        return result
    return wrap

def send_inference_msg_to_slack(msg , is_online):
    
    dict_headers = {'Content-type': 'application/json'}
    dict_payload = {"text": msg}
    json_payload = json.dumps(dict_payload)
    rtn = requests.post(S_URL_OTHERS, data=json_payload, headers=dict_headers)
    
def send_py_error_msg_to_slack(py_name, error_msg):
    root = get_project_root()
    slack_template_path = os.path.join(root,'common','slack_message_layouts','slack_errorMsg_template.json')
    dict_headers = {'Content-type': 'application/json'}
    
    with open(slack_template_path) as json_file:
        dict_payload = json.load(json_file)
    dict_payload['blocks'][0]['text']['text'] += py_name
    dict_payload['blocks'][1]['text']['text'] = f"```{error_msg}```"
    json_payload = json.dumps(dict_payload)
    
    S_URL = S_URL_PROD_ERROR if 1 else S_URL_OTHERS # TEST!
    rtn = requests.post(S_URL, data=json_payload, headers=dict_headers)
    return rtn
    
def send_closedloop_msg_to_slack(py_name, msg_incons_count, msg_incons_err, msg_incons_sql):
    root = get_project_root()
    slack_template_path = os.path.join(root,'common','slack_message_layouts','slack_closedLoopMsg_template.json')
    dict_headers = {'Content-type': 'application/json'}
    with open(slack_template_path) as json_file:
        dict_payload = json.load(json_file)
    dict_payload['blocks'][0]['text']['text'] += py_name
    dict_payload['blocks'][1]['text']['text'] = f"```{msg_incons_count}```"
    dict_payload['blocks'][2]['text']['text'] = f"```{msg_incons_err}```"
    dict_payload['blocks'][3]['text']['text'] += f"```{msg_incons_sql}```"
    json_payload = json.dumps(dict_payload)
    rtn = requests.post(S_URL_OTHERS, data=json_payload, headers=dict_headers)
    os.environ['http_proxy'] = '' 
    os.environ['https_proxy'] = ''
    return rtn
        
def list_all_files(directory: str, recursive: bool = True):
    files_list = []
    if recursive:
        # 遞迴搜尋子目錄中的所有檔案
        for root, dirs, files in os.walk(directory):
            for file in files:
                file_path = os.path.join(root, file)
                files_list.append(file_path)
    else:
        # 不遞迴，僅搜尋當前目錄中的檔案
        for file in os.listdir(directory):
            file_path = os.path.join(directory, file)
            if os.path.isfile(file_path):
                files_list.append(file_path)

    return files_list
        
def clean_folder(path):
    if not os.path.exists(path):
        os.makedirs(path)
    else:
        files = glob.glob(f'{path}/*')
        for file in files:
            os.remove(file)


def df2redis(redis_client, df, key):
    json_data = df.to_json()
    redis_client.set(key, json_data)

def redis2df(redis_client, key):
    json_data = redis_client.get(key)
    if json_data:
        json_str = json_data.decode('utf-8')
        df = pd.read_json(StringIO(json_str))
        return df
    else:
        return None

def redis_delete(redis_client, key):
    redis_client.delete(key)


def redis_clear_all(redis_client):
    # 清空所有緩存
    redis_client.flushall()

def redis_keys_list_all(redis_client):
    # 查看 Redis 中有哪些鍵，並且返回每個鍵的大小、數據類型及其占用的緩存容量比例
    keys = redis_client.keys('*')
    if not keys:
        return {}

    total_memory = redis_client.info('memory')['used_memory']  # 獲取 Redis 緩存總容量
    key_info = {}

    for key in keys:
        key_str = key.decode('utf-8')
        key_type = redis_client.type(key_str).decode('utf-8')
        key_size = redis_client.memory_usage(key_str)  # 以字節為單位

        if key_size is not None:
            # 計算占用總內存的比例
            memory_percentage = (key_size / total_memory) * 100
        else:
            memory_percentage = 0

        key_info[key_str] = {
            'type': key_type,
            'size_in_bytes': key_size,
            'memory_percentage': memory_percentage
        }

    return key_info


def check_gpu():
    print(f"Allocated memory: {torch.cuda.memory_allocated()} bytes")
    print(f"Reserved memory: {torch.cuda.memory_reserved()} bytes")

def make_arrow_compatible(df):
    def clean_name(name):
        left_paren_index = name.find('(')
        if left_paren_index != -1:
            return name[:left_paren_index].strip()
        return name.strip()
    
    for col in df.columns:
        if col == "REPAIR_TIME":
            try:
                df[col] = df[col].str.split().str[0]
                df[col] = pd.to_datetime(df[col])
                df[col] = df[col].astype("string")
            except AttributeError as e:
                print(f"{e}")
                df[col] = df[col].astype("string")
                pass
        data_type = df[col].dtype
        if data_type == 'object':
            # 嘗試將列轉換為 string 類型
            try:
                df[col] = df[col].astype('string')
            except Exception as e:
                print(f"Failed to convert column {col} to string: {e}")
        elif data_type == 'float64':
            try:
                df[col] = df[col].astype('float')
            except Exception as e:
                print(f"Failed to convert column {col} to float32: {e}")
        elif df[col].dtype == 'int64':
            # 嘗試將列轉換為 int32 類型
            pass
    return df

# 判斷字串是否包含中文
def contains_chinese(text):
    pattern = re.compile(r'[\u4e00-\u9fff]')
    return bool(pattern.search(text))

def custom_jsonable_encoder(obj):
    if isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    else:
        pass
    try:
        result = jsonable_encoder(obj)
    except Exception as e:
        result = ""
    return result 

def replace_invalid_values(df):
    df.replace([np.inf, -np.inf], np.nan, inplace=True)
    float64_columns = df.select_dtypes(include=['float64']).columns
    df[float64_columns] = df[float64_columns].astype(object)
    df.fillna("", inplace=True)
    return df

def sync_directories(source_directory, destination_directory):
    try:
        password = "aiserver"
        command = [
            'sshpass', '-p', password,
            'rsync', '-azxvP', 
            '-e', 'ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null', # 強制略過金鑰檢查
            '--exclude', '*.zip',
            source_directory, destination_directory
        ]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        while True:
            output = process.stdout.readline()
            if output == '' and process.poll() is not None:
                break
            if output:
                print(output.strip())  # 即時顯示 rsync 輸出
        # 檢查是否有錯誤輸出
        stderr = process.stderr.read()
        if stderr:
            print("錯誤:", stderr)
            # 如果同步成功，返回標準輸出
        return True
    except subprocess.CalledProcessError as e:
        # 如果同步失敗，輸出錯誤訊息
        logging.info("同步失敗: ", e.stderr)
        return False
