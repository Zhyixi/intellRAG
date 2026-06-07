import sys, os
import csv
sys.path.extend(['.','..'])
from  .core import Settings
from  .core.prompts.base import PromptTemplate
import datetime
import yaml
from configs.config import project_root
import logging
import pandas as pd
import torch
from  .core.query_pipeline import QueryPipeline as QP, Link, InputComponent
from common.utils import make_arrow_compatible
from LLM.utils import set_date_variables, preprocess_df_instr
from  .core.selectors import LLMSingleSelector, LLMMultiSelector
from  .core.tools import ToolMetadata
from  .core.tools.query_engine import QueryEngineTool
from  .core.query_engine.router_query_engine import RouterQueryEngine


current_datetime = datetime.datetime.now()
today_start,today_end, yesterday_start,yesterday_end, current_month_start,current_month_end, last_month_start, last_month_end , current_year_start, current_year_end , last_year_start, last_year_end , this_week_start, this_week_end, last_week_start, last_week_end = set_date_variables(current_datetime)
with open(f"{project_root}/config.yml", "r", encoding="utf-8") as ymlfile:
    cfg = yaml.safe_load(ymlfile)



with open(f"{project_root}/config.yml", "r", encoding="utf-8") as ymlfile:
    cfg = yaml.safe_load(ymlfile)

# Generating prompt for pandas instruction
def gen_df_instr_prompt(df, prompt_key):
    instruction_str = cfg[prompt_key]
    if 'smt' == prompt_key:
        instruction_str = instruction_str.format(today_start=today_start,
        today_end = today_end,
        yesterday_start=yesterday_start,
        yesterday_end=yesterday_end,
        this_week_start=this_week_start,
        this_week_end=this_week_end,
        last_week_start=last_week_start,
        last_week_end=last_week_end,
        current_month_start=current_month_start,
        current_month_end = current_month_end,
        last_month_start=last_month_start,
        last_month_end= last_month_end,
        current_year_start=current_year_start,
        current_year_end=current_year_end,
        last_year_start=last_month_start,
        last_year_end=last_year_end)

    pandas_prompt_str = (
        "You are working with a pandas dataframe in Python.\n"
        "The name of the dataframe is `df`.\n"
        "This is the result of `print(df.head())`:{df_str} \n\n"
        "Must follow these instructions:\n"
        "{instruction_str}\n"
        "Query: {query_str}\n\n"
        "Expression:")
    if isinstance(df, pd.DataFrame):
        pandas_prompt = PromptTemplate(pandas_prompt_str).partial_format(instruction_str=instruction_str, df_str=df.head())
    else:
        pandas_prompt = PromptTemplate(pandas_prompt_str).partial_format(instruction_str=instruction_str, df_str=str(df))
    return pandas_prompt

# Create pipeline for instr
def gen_df_inst_q(llm, pandas_prompt):
    qp = QP(
        modules={
            "input": InputComponent(),
            "pandas_prompt": pandas_prompt,
            "llm": llm,
        },
        verbose=True,
    )
    # 定義模塊的執行順序
    qp.add_chain(["input", "pandas_prompt", "llm"])
    # 定義模塊之間的連接,數據流轉的路徑
    qp.add_links(
        [
            Link("input", "pandas_prompt", dest_key="query_str"),
            Link("pandas_prompt", "llm")
        ]
    )
    return qp

# Create pipeline for pandas synthesis
def gen_df_response_q(llm):
    response_synthesis_prompt_str = (
    "你現在是一個在工廠工作的主管, 需要對停線時間進行分析\n"
    "依據`問題`與`查詢結果`整合一個回應\n"
    "問題: {query_str}\n\n"
    "查詢結果: {pandas_output}\n\n"
    "回應: "
    )
    response_synthesis_prompt = PromptTemplate(response_synthesis_prompt_str)
    qp = QP(
    modules={
        "response_synthesis_prompt": response_synthesis_prompt,
        "llm": llm,
    },verbose=True,)
    qp.add_chain(["response_synthesis_prompt", "llm"])
    qp.add_links(
        [
            Link("response_synthesis_prompt", "llm"),
        ]
    )
    return qp

# 初始化兩個管道
def initialize_df_engine(df, prompt_key):
    # 產生prompt
    pandas_prompt = gen_df_instr_prompt(df, prompt_key=prompt_key)
    qp1 = gen_df_inst_q(pd_instr_model, pandas_prompt)
    qp2 = gen_df_response_q(chat_model)
    return {'qp1':qp1, 'qp2':qp2}
# 語法生成與合成回應
def process_query(query_engine, prompt, df):
    """Process the query and return the response and result."""
    # 生成語法
    qp1, qp2 = query_engine['qp1'], query_engine['qp2']
    pandas_code_output, _ = qp1.run_with_intermediates(query_str=prompt)
    try:
        pandas_instructions = pandas_code_output.text
    except:
        pandas_instructions = str(pandas_code_output).replace('assistant: ', '')
    pandas_instr = preprocess_df_instr(df_instr=pandas_instructions)
    a = df[(df['REPAIR_TIME'].between('2024-11-25 08:00:00', '2024-12-02 07:59:59'))]
    print(a[['SERIAL_NUMBER']])
    # 執行語法
    response = None
    try:
        df["SERIAL_NUMBER"] = df["SERIAL_NUMBER"].astype(str)
        df = make_arrow_compatible(df)
        safe_env = {"df": df}
        exec(f"result = {pandas_instr}", {}, safe_env)
        result = safe_env["result"]
        ###########################################3
        single_selector = LLMSingleSelector.from_defaults(llm=chat_model)
        prompt_template = """
            請根據以下問題的描述，判斷該問題應該使用哪種工具來解決:
            - 如果問題包含「清單」「列表」等需要大量表單，則使用工具:list
            問題描述: {user_question}
            請選擇一個最適合的工具並給出其名稱。
        """
        formatted_prompt = prompt_template.format(user_question=prompt)
        # 定義選擇器的選項，其中包含工具的描述
        top_choices = [
            ToolMetadata(description="查詢維修清單或列表", name="list"),
            ToolMetadata(description="其他", name="other"),
        ]
        top_layer_selection = single_selector.select(top_choices, query=formatted_prompt)
        top_layer_selection=top_layer_selection.selections[0]
        top_layer_selection_index=top_layer_selection.index
        if top_layer_selection_index == 0:
            response = "清單如下:"
        elif top_layer_selection_index == 1:
            response, _ = qp2.run_with_intermediates(query_str=prompt,pandas_output=f"{result}")
        ################################
        
        if not isinstance(response, str):
            try:
                response = response.text
            except:
                response = str(response).replace('assistant: ', '')
    except Exception as error_gen_pd:
        if isinstance(error_gen_pd, torch.cuda.OutOfMemoryError):
            result = f"Error:Out of Memory in generating pandas_instr"
            pandas_instr = ""
        else:
            result = f"******\nError:\n{str(error_gen_pd)[:255]}\npandas_instr:\n{pandas_instr}\n******"
    logging.info(f"response:{response}\npandas_instruction:{pandas_instr}")
    return response, result, pandas_instr

# 執行語法後揉
def process_query_instr(query_engine, pandas_instr, prompt, df):
    """Process the query and return the response and result."""
    # 生成語法
    qp2 = query_engine['qp2']
    # 執行語法
    response = None # 
    try:
        df = make_arrow_compatible(df)
        safe_env = {"df": df}
        exec(f"result = {pandas_instr}", {}, safe_env)
        result = safe_env["result"]
        result = result.drop_duplicates() 
        if len(result) > 100:
            response, _ = qp2.run_with_intermediates(query_str=prompt,pandas_output=f"{result.head()}") # 
        else:
            response, _ = qp2.run_with_intermediates(query_str=prompt,pandas_output=f"{result}") # 
        if not isinstance(response, str):
            try:
                response = response.text
            except:
                response = str(response).replace('assistant: ', '')
    except Exception as error_gen_pd:
        if isinstance(error_gen_pd, torch.cuda.OutOfMemoryError):
            result = f"Error:Out of Memory in generating pandas_instr"
            pandas_instr = ""
        else:
            result = f"******\nError:\n{str(error_gen_pd)[:255]}\npandas_instr:\n{pandas_instr}\n******"
    logging.info(f"response:{response}\npandas_instruction:{pandas_instr}")
    return response, result, pandas_instr

# 執行語法後揉
def syn_dataframe(query_engine, prompt, df):
    """Process the query and return the response and result."""
    # 生成語法
    qp2 = query_engine['qp2']
    # 執行語法
    response = None # 
    try:
        response, _ = qp2.run_with_intermediates(query_str=prompt,pandas_output=f"{df}")
        if not isinstance(response, str):
            try:
                response = response.text
            except:
                response = str(response).replace('assistant: ', '')
    except Exception as error_gen_pd:
        pass
    return response

# 生成回應用的管道
def gen_common_response_q(llm):
    response_synthesis_prompt_str = ("請用中文回答問題,{query_str}")
    response_synthesis_prompt = PromptTemplate(response_synthesis_prompt_str)
    qp = QP(
    modules={"response_synthesis_prompt": response_synthesis_prompt, "llm": llm},verbose=True,)
    qp.add_chain(["response_synthesis_prompt", "llm"])
    qp.add_links([Link("response_synthesis_prompt", "llm")])
    return qp

