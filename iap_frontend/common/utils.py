# coding: utf-8
import base64
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
import numpy as np
S_URL_OTHERS = 'https://hooks.slack.com/services/REDACTED'
S_URL_PROD_ERROR ='https://hooks.slack.com/services/REDACTED'
PROJECT_NAME = 'smt_LFI_API'


def need_proxy(is_online):
    CONFIG_PATH = get_config_dir()
    config = configparser.ConfigParser()
    config.read(CONFIG_PATH)
    config_name = 'proxy'
    bool_proxy = 'prod' if is_online else 'dev'
    proxy = config.get(config_name, bool_proxy)
    os.environ['http_proxy'] = f"{proxy}"
    os.environ['https_proxy'] = f"{proxy}"


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
                send_py_error_msg_to_slack(sys.argv[0],msg,is_online)
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
    need_proxy(is_online)
    rtn = requests.post(S_URL_OTHERS, data=json_payload, headers=dict_headers)
    os.environ['http_proxy'] = '' 
    os.environ['https_proxy'] = ''
    
def send_py_error_msg_to_slack(py_name, error_msg,is_online):
    root = get_project_root()
    slack_template_path = os.path.join(root,'common','slack_message_layouts','slack_errorMsg_template.json')
    dict_headers = {'Content-type': 'application/json'}
    
    with open(slack_template_path) as json_file:
        dict_payload = json.load(json_file)
    dict_payload['blocks'][0]['text']['text'] += py_name
    dict_payload['blocks'][1]['text']['text'] = f"```{error_msg}```"
    json_payload = json.dumps(dict_payload)
    S_URL = S_URL_PROD_ERROR if is_online else S_URL_OTHERS
    need_proxy(is_online)
    rtn = requests.post(S_URL, data=json_payload, headers=dict_headers)
    os.environ['http_proxy'] = '' 
    os.environ['https_proxy'] = ''
    return rtn
    
def send_closedloop_msg_to_slack(py_name, msg_incons_count, msg_incons_err, msg_incons_sql, is_online):
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
    need_proxy(is_online)
    rtn = requests.post(S_URL_OTHERS, data=json_payload, headers=dict_headers)
    os.environ['http_proxy'] = '' 
    os.environ['https_proxy'] = ''
    return rtn
        
def get_project_root():
    return Path(__file__).parent.parent

def get_config_dir():
    root = get_project_root()
    config_path = os.path.join(root,'configs','config.ini')
    return config_path

def get_data_dir():
    current_directory = os.getcwd()
    current_directory = current_directory.split('/')
    data_dir = f'/mnt/hdd1/Data/{PROJECT_NAME}_prod' if 'smt_LFI_API_prod' in current_directory else f'/mnt/hdd1/Data/{PROJECT_NAME}_dev'
    return data_dir

        
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

def clear_redis(redis_client, key):
    redis_client.delete(key)
    
def make_arrow_compatible(df):
    def clean_name(name):
            left_paren_index = name.find('(')
            if left_paren_index != -1:
                return name[:left_paren_index].strip()
            return name.strip()
    for col in df.columns:
        if col == "REPAIR_TIME":
            df[col] = df[col].str.split().str[0]
            df[col] = pd.to_datetime(df[col])
        elif col == "REPAIRERNAME":
            df[col] = df[col].apply(clean_name)
        if df[col].dtype == 'object':
            # 嘗試將列轉換為 string 類型
            try:
                df[col] = df[col].astype('string')
            except Exception as e:
                print(f"Failed to convert column {col} to string: {e}")
        elif df[col].dtype == 'float64':
            # 嘗試將列轉換為 float32 類型
            try:
                df[col] = df[col].astype('str')
            except Exception as e:
                print(f"Failed to convert column {col} to float32: {e}")
        elif df[col].dtype == 'int64':
            # 嘗試將列轉換為 int32 類型
            try:
                df[col] = df[col].astype('str')
            except Exception as e:
                print(f"Failed to convert column {col} to int32: {e}")
    return df

# 判斷字串是否包含中文
def contains_chinese(text):
    pattern = re.compile(r'[\u4e00-\u9fff]')
    return bool(pattern.search(text))



def replace_invalid_values(df):
    df.replace([np.inf, -np.inf], np.nan, inplace=True)
    float64_columns = df.select_dtypes(include=['float64']).columns
    df[float64_columns] = df[float64_columns].astype(object)
    df.fillna("", inplace=True)
    return df


def get_config():
    CONFIG_PATH = get_config_dir()
    config = configparser.ConfigParser()
    config.read(CONFIG_PATH, encoding='utf-8')
    return config

# load local image and encoding to Base64
def load_image_as_base64(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode()