import itertools
import sys, os
import numpy as np
import csv
sys.path.extend(['.','..'])
# Load model directly
from transformers import AutoTokenizer, AutoModelForCausalLM
import logging
import pandas as pd
import torch
import tqdm
from  .core.query_pipeline import QueryPipeline as QP, Link, InputComponent
from common.utils import make_arrow_compatible
from  .llms.huggingface import HuggingFaceLLM
from transformers import BitsAndBytesConfig
from  .core import PromptTemplate
from  .llms.openai import OpenAI
import torch
import logging
from  .core import Settings
from  .embeddings.huggingface import HuggingFaceEmbedding
from  .core.prompts.base import PromptTemplate
from datetime import datetime, timedelta
from LLM.ChatEngine.chat_engine import set_date_variables
import yaml
from configs.config import project_root

ENV = os.getenv('ENV', 'prod')
output_folder = 'results_folder'
# Initialize the embedding model and LLM
date_str = '2024-05-07' # datetime.now()
current_datetime = datetime.strptime(date_str, '%Y-%m-%d')
current_datetime = current_datetime.date()
today, yesterday, current_month, last_month, current_year, last_year, this_week_start, this_week_end, last_week_start, last_week_end, this_week, last_week = set_date_variables(current_datetime)
with open(f"{project_root}/config.yml", "r", encoding="utf-8") as ymlfile:
    cfg = yaml.safe_load(ymlfile)
        
def list_files(directory):
    file_paths = []  # 用來儲存檔案路徑
    for root, dirs, files in os.walk(directory):
        for file in files:
            file_path = os.path.join(root, file)
            file_paths.append(file_path)
    return file_paths

# 使用方式：
directory = '你的目錄路徑'


if __name__ == '__main__':
    all_files = list_files("/app/results_folder")
    base_data = pd.read_csv("/app/dataSet/smt/TW01_A31_SOURCE_DATA_2024-05-07.csv")
    base_data = make_arrow_compatible(base_data)
    key_cols = ['Question', 'Response','Syntax', 'Answer']
    progress_bar = tqdm.tqdm(desc="testing...",total=len(all_files))
    total_res = pd.DataFrame({"file":[], "single":[], "dual":[], "overall":[]})
    for file_name in all_files:
        progress_bar.update(1)
        evaluate_tb = pd.read_csv(file_name)
        evaluate_tb.dropna(subset=["Question"],inplace=True)
        evaluate_tb= evaluate_tb[key_cols]
        test_score1 = []
        tmp = []
        for idx, row in evaluate_tb.iterrows():
            outputs = str(row.Syntax).rstrip(";").split(";")
            tmp.append(len(outputs))
        max_output = max(tmp)
        for idx, row in evaluate_tb.iterrows():
            outputs = str(row.Syntax).rstrip(";").split(";")
            outputs = [s.replace('python\n', '') if s.startswith('python\n') else s for s in outputs]
            answers = row.Answer.split(";")
            question = row.Question
            test_score2 = [] # 語法錯誤 0 語法可執行但與答案不一樣0 語法 結果一樣1
            for idx1 in range(max_output):
                correctness = 0
                try:
                    pandas_instr = outputs[idx1]
                except IndexError:
                    pandas_instr = ""
                # 先比對語法是否正確
                if pandas_instr.strip() in [ans.strip() for ans in answers]:
                    correctness = 1
                # 假如語法不匹配，再比對DataFrame是否正確
                else:
                    try:
                        print(f"Evaluating syntax: {answers[0]}")
                        safe_env = {"df": base_data}
                        exec(f"result = {answers[0]}", {}, safe_env)
                        expected_df = safe_env["result"]
                        # 執行生成的pandas_instr並獲取結果
                        try:
                            exec(f"result = {pandas_instr}", {}, safe_env)
                            generated_df = safe_env["result"]
                        except Exception as e:
                            print(f"Error executing pandas_instr: {e}")
                            generated_df = None
                        #先判斷是否為Series，是的話轉成DataFrame
                        try:
                            if isinstance(generated_df, pd.Series):
                                generated_df = generated_df.to_frame().reset_index()
                            if isinstance(expected_df, pd.Series):
                                expected_df = expected_df.to_frame().reset_index()
                        except Exception as e:
                            print(f"Error converting Series to DataFrame: {e}")
                            pass
                        # 如果是 DataFrame
                        if isinstance(generated_df, pd.DataFrame) and isinstance(expected_df, pd.DataFrame):
                            if generated_df.equals(expected_df):
                                correctness = 1
                            elif generated_df.index.equals(expected_df.index): # 比對索引是否相同
                                correctness = 1
                            else:
                                correctness = 0
                        # 如果不是 DataFrame
                        else:
                            if (generated_df == expected_df).all().all():
                                correctness = 1
                            else:
                                correctness = 0
                    except Exception as e:
                        print(f"Error executing answer: {e}")
                        correctness = 0
                test_score2.append(correctness)
            test_score1.append(test_score2)
        test_result = pd.DataFrame(data=test_score1, columns=[f"score_{i}" for i in range(len(test_score2))]) # {:test_score}        
        test_result["Consistency Ratio"] = test_result.apply(lambda row: len(test_score2)-len(np.unique(row[[f"score_{i}" for i in range(len(test_score2))]].values))+1, axis=1) / len(test_score2)
        # 正確率
        test_result["Acc"] = test_result[[f"score_{i}" for i in range(len(test_score2))]].sum(axis=1) / len(test_score2)
        evaluate_tb = pd.concat(objs=[evaluate_tb, test_result], axis=1)
        evaluate_tb.to_csv(file_name, index=False)
        total_questions = len(evaluate_tb)
        accuracy = test_result["Acc"].sum() / total_questions * 100
        accuracy_single = test_result["Acc"][:50].sum() / len(test_result["Acc"][:50]) * 100
        accuracy_dual = test_result["Acc"][50:].sum() / len(test_result["Acc"][50:]) * 100
        Consistency_Ratio = test_result["Consistency Ratio"].sum() / total_questions * 100
        print(f"正確率: {accuracy:.2f}%")
        print(f"Single正確率: {accuracy_single:.2f}%")
        print(f"Dual正確率: {accuracy_dual:.2f}%")
        print(f"一致性: {Consistency_Ratio:.2f}%")
        total_res = pd.concat([total_res, pd.DataFrame({"file":[file_name], "Consistency_Ratio":[Consistency_Ratio], "single":[accuracy_single], "dual":[accuracy_dual], "overall":[accuracy]})])
    total_res.to_csv("evaluation_total.csv")
    progress_bar.close()
    pass
    