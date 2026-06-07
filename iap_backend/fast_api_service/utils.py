import csv
import datetime
import sys, os

import yaml

from LLM.ChatEngine.chat_engine import syn_response
sys.path.extend(['.', '..'])
from fastapi.encoders import jsonable_encoder
import numpy as np
import pandas as pd
from common.utils import get_config
from configs.config import project_root


def replace_invalid_values(df):
    df.replace([np.inf, -np.inf], np.nan, inplace=True)
    float64_columns = df.select_dtypes(include=['float64']).columns
    df[float64_columns] = df[float64_columns].astype(object)
    df.fillna("", inplace=True)
    return df

def custom_jsonable_encoder(obj):
    if isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    return jsonable_encoder(obj)

def response_post_process(result):
    if isinstance(result, pd.DataFrame):
        result = result.head(1000) # TMP! 
        result = result.map(custom_jsonable_encoder)
        result = replace_invalid_values(result)
        result = result.to_dict(orient="records")
    elif isinstance(result, str):
        pass
    elif isinstance(result, np.int64) or isinstance(result, int):
        result = int(result)
    elif isinstance(result, np.float64) or isinstance(result, float):
        result = float(result)
    elif isinstance(result, pd.Series):
        result = result.to_frame()
        result.reset_index(drop=False, inplace=True)
        result = result.map(custom_jsonable_encoder)
        result = replace_invalid_values(result)
        result = result.to_dict(orient="records")
    elif isinstance(result, bool):
        result = f"{result}"
    elif isinstance(result, pd.core.arrays.string_.StringArray):
        result = list(result)
    else:
        try:
            result = f"{result}"
        except Exception as e:
            raise AssertionError(f"error:\n{e}\nresult:\n{result}")
    return result
# 以下為測試用
def csv_log(data:str|list):
    config = get_config()
    LOG_PATH = config.get('log_setting','LOG_PATH')
    if not os.path.exists(LOG_PATH):
        os.makedirs(LOG_PATH)
    filename = os.path.join(LOG_PATH, f"chat_log_{datetime.datetime.now().strftime('%Y-%m-%d')}.csv")
    file_exists = os.path.isfile(filename)
    with open(filename, mode='a', encoding='utf-8', newline='') as file:
        writer = csv.writer(file)
        if not file_exists:
            writer.writerow(["timestamp", "Question", "Response", "Syntax", "Time-Consuming"])
        writer.writerow([datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')]+data)

def get_previous_question():
    from configs.config import LOG_PATH
    filename = os.path.join(LOG_PATH, f"chat_log_{datetime.datetime.now().strftime('%Y-%m-%d')}.csv")
    record = pd.read_csv(filename)
    res = {"q":[], "a":[]}
    for item in range(1, len(record)+1): # 
        record_row = record.iloc[-item,:]
        question = record_row.Question
        response = record_row.Response
        res["q"].append(question)
        res["a"].append(response)
        if item == 1 or "解決" in question:
            break
    return res
