import os
import re
import json
import requests
import numpy as np
import pandas as pd
import hdbscan

from sqlalchemy import create_engine
from dependency_injector import providers
from containers import Container
from langchain_huggingface import HuggingFaceEmbeddings

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from configs.config import MYSQL_URL
# =========================================================
# 全域設定與資料庫連線
# =========================================================
BASE_URL = "https://faca.compal.com/api/aereports"
MODEL_PATH = "/app/models/hub/models--BAAI--bge-m3/snapshots/5617a9f61b028005a4858fdac845db406aefb181"
DEBUG = True

# 資料庫連線字串
engine = create_engine(MYSQL_URL)

# =========================================================
# 基礎工具函式
# =========================================================
def debug_print(title, value=None):
    if DEBUG:
        print(f"\n========== {title} ==========")
        if value is not None: 
            print(value)

def vllm_langchain_general_llm():
    container = Container()
    return container.vllm_langchain_general_llm()

def clean_text(value):
    if pd.isna(value): 
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\n", " ").replace("\r", " ")).strip()

def is_valid_text(series):
    return ~series.astype(str).str.strip().str.lower().isin(['', 'null', 'none', 'nan'])

# =========================================================
# API 呼叫與資料攤平
# =========================================================
def call_iap_ae_api(params, timeout=30):
    try:
        resp = requests.get(
            BASE_URL, 
            params=params, 
            headers={"User-Agent": "Mozilla/5.0"}, 
            timeout=timeout, 
            verify=False
        )
        resp.raise_for_status()
        return {"res": resp.json(), "status_code": resp.status_code}
    except Exception as e:
        print(f"API Error: {e}")
        return {"res": [], "error": str(e)}

def flatten_iap_ae_data(api_result):
    res = api_result.get("res", [])
    if isinstance(res, str):
        try: 
            res = json.loads(res)
        except: 
            return pd.DataFrame()
        
    res = res.get("data") or res.get("result") or res.get("rows") or res if isinstance(res, dict) else res
    if not isinstance(res, list): 
        return pd.DataFrame()

    rows = []
    for item in res:
        item = json.loads(item) if isinstance(item, str) else item
        if not isinstance(item, dict): 
            continue
        
        basic = item.get("basic_information", {})
        atts = {att.get("FIELD_NAME"): att.get("DESCRIPTION", "") for att in item.get("attachment", []) if isinstance(att, dict)}

        rows.append({
            "ISSUE_ID": basic.get("ISSUE_ID", ""),
            "PLANT": basic.get("PLANT", ""),
            "DEVICE_ID": basic.get("DEVICE_ID", ""),
            "DEVICE_NAME": basic.get("DEVICE_NAME", ""),
            "FUNCTION_DESC": basic.get("FUNCTION_DESC", ""),
            "ABNORMAL_DATE": basic.get("ABNORMAL_DATE", ""),
            "ROOT_CAUSE": atts.get("rootCause_description", ""),
            "EXPLAIN_CAUSE": atts.get("explanCause_description", ""),
            "ACTION": atts.get("actionCause_description", "")
        })
    return pd.DataFrame(rows)

def get_reports_by_device_ids(device_ids, syslang="ZH", drop_duplicate=True, duplicate_text_col="ROOT_CAUSE"):
    dfs = [flatten_iap_ae_data(call_iap_ae_api({"isMaintain": "Y", "DEVICE_ID": d_id, "syslang": syslang})) for d_id in device_ids]
    df = pd.concat([d for d in dfs if not d.empty], ignore_index=True) if dfs else pd.DataFrame()
    
    if df.empty: 
        return df

    dup_subset = ["ISSUE_ID", "DEVICE_ID", duplicate_text_col]
    if drop_duplicate and all(c in df.columns for c in dup_subset):
        df = df.drop_duplicates(subset=dup_subset)
    return df

# =========================================================
# 模型分群與 LLM 命名工具
# =========================================================
def get_text_embeddings(texts):
    embedding_model_provider = providers.Singleton(
        HuggingFaceEmbeddings, 
        model_name=MODEL_PATH, 
        model_kwargs={"device": "cpu"}
    )
    model = embedding_model_provider()
    embeddings = np.array(model.embed_documents(texts), dtype=np.float32)
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    return embeddings / np.where(norms == 0, 1, norms)

def generate_category_name_by_llm(root_causes, text_col="ROOT_CAUSE", max_samples=20):
    valid_texts = [clean_text(t) for t in root_causes if is_valid_text(pd.Series([t])).iloc[0]]
    unique_texts = list(dict.fromkeys(valid_texts))
    if not unique_texts: 
        return "未填寫原因"
    
    if text_col == "FUNCTION_DESC":
        task_desc = "請根據以下同一群「設備異常現象 (FUNCTION_DESC)」，歸納出一個最適合的現象分類名稱。"
        list_title = "異常現象清單："
    else:
        task_desc = "請根據以下同一群「異常根本原因 (ROOT_CAUSE)」，歸納出一個最適合的異常分類名稱。"
        list_title = "ROOT_CAUSE 清單："

    prompt = f"""你是一位製造業設備異常分析專家。
        {task_desc}
        命名規則：1. 繁體中文 2. 6-16個字 3. 勿用「問題/異常類/分類」等空泛字 4. 僅輸出名稱。
        {list_title}\n""" + "\n".join([f"{i+1}. {t}" for i, t in enumerate(unique_texts[:max_samples])]) + "\n/nothink"

    try:
        resp = vllm_langchain_general_llm().invoke(prompt)
        cat_name = getattr(resp, "content", str(resp))
        cat_name = clean_text(re.sub(r"<think>.*?</think>", "", cat_name, flags=re.DOTALL|re.IGNORECASE))
        return cat_name[:50] or unique_texts[0]
    except Exception as e:
        print(f"LLM 命名失敗: {e}")
        return unique_texts[0]

def cluster_one_device_text(df, text_col="ROOT_CAUSE", min_cluster_size=2, min_samples=1):
    df = df.copy()
    df[text_col] = df[text_col].apply(clean_text)
    df = df[is_valid_text(df[text_col])].copy()

    if df.empty: 
        return df
    
    unique_texts = df[text_col].drop_duplicates().tolist()

    if len(unique_texts) < min_cluster_size:
        df["LOCAL_CLUSTER_ID"] = df[text_col].astype("category").cat.codes
        df["CATEGORY"] = df[text_col]
        return df

    labels = hdbscan.HDBSCAN(min_cluster_size=min_cluster_size, min_samples=min_samples, metric="euclidean").fit_predict(get_text_embeddings(unique_texts))
    
    next_id = max([l for l in labels if l != -1], default=-1) + 1
    text_to_cluster = {}
    for text, label in zip(unique_texts, labels):
        if label == -1:
            label, next_id = next_id, next_id + 1
        text_to_cluster[text] = label

    df["LOCAL_CLUSTER_ID"] = df[text_col].map(text_to_cluster)
    
    cluster_name_map = {}
    for c_id, group in df.groupby("LOCAL_CLUSTER_ID"):
        if text_col in ["ROOT_CAUSE", "FUNCTION_DESC"]: 
            cluster_name_map[c_id] = generate_category_name_by_llm(group[text_col].dropna().tolist(), text_col)
        else:
            cluster_name_map[c_id] = group[text_col].value_counts().index[0]
            
    df["CATEGORY"] = df["LOCAL_CLUSTER_ID"].map(cluster_name_map)
    return df

def merge_similar_categories(df, threshold=0.85):
    if df.empty or "CATEGORY" not in df.columns: 
        return df
    df = df.copy()
    
    for device_id, device_df in df.groupby("DEVICE_ID"):
        cats = device_df["CATEGORY"].dropna().unique().tolist()
        if len(cats) < 2: 
            continue
        
        emb = get_text_embeddings(cats)
        sim_matrix = np.dot(emb, emb.T) 
        cat_map = {c: c for c in cats}
        counts = device_df["CATEGORY"].value_counts()
        
        for i in range(len(cats)):
            for j in range(i + 1, len(cats)):
                if sim_matrix[i, j] >= threshold:
                    root_i, root_j = cat_map[cats[i]], cat_map[cats[j]]
                    if root_i != root_j:
                        target, source = (root_i, root_j) if counts[root_i] >= counts[root_j] else (root_j, root_i)
                        for k, v in cat_map.items():
                            if v == source:
                                cat_map[k] = target
                                
        df.loc[df["DEVICE_ID"] == device_id, "CATEGORY"] = device_df["CATEGORY"].map(cat_map)
    return df

# =========================================================
# ETL 核心流程 (對外提供的介面)
# =========================================================
def run_iap_ae_etl_pipeline(embedding_text_col="FUNCTION_DESC"):
    """執行完整 ETL：抓取資料、模型分群、寫入資料庫"""
    print("========== 啟動 iap_ae ETL 任務 ==========")
    
    # 步驟 1: 取得所有 Device_ID
    all_data_res = call_iap_ae_api({"isMaintain": "Y", "syslang": "ZH"})
    all_data_df = flatten_iap_ae_data(all_data_res)
    
    if all_data_df.empty or "DEVICE_ID" not in all_data_df.columns:
        print("無法從 API 取得資料。")
        return

    all_device_ids = all_data_df["DEVICE_ID"].dropna().astype(str).str.strip()
    all_device_ids = all_device_ids[all_device_ids != ""].drop_duplicates().tolist()
    print(f"共找到 {len(all_device_ids)} 台設備，開始處理...")

    # 步驟 2: 批次處理、分群、合併
    all_clustered_dfs = []
    for d_id in all_device_ids:
        print(f"處理設備: {d_id} ...")
        df = get_reports_by_device_ids([d_id], drop_duplicate=True, duplicate_text_col=embedding_text_col)
        if df.empty: continue
        
        clustered_df = cluster_one_device_text(df, text_col=embedding_text_col)
        if not clustered_df.empty:
            clustered_df["CLUSTER_ID"] = str(d_id) + "_" + clustered_df["LOCAL_CLUSTER_ID"].astype(str)
            clustered_df = merge_similar_categories(clustered_df, threshold=0.85)
            all_clustered_dfs.append(clustered_df)

    if not all_clustered_dfs:
        print("沒有成功解析的資料。")
        return

    # 步驟 3: 合併為總明細表
    final_detail_df = pd.concat(all_clustered_dfs, ignore_index=True)
    
    # 步驟 4: 計算總統計表
    summary_rows = []
    final_detail_df["ABNORMAL_DATE"] = pd.to_numeric(final_detail_df["ABNORMAL_DATE"], errors="coerce").fillna(0)
    
    for d_id, d_df in final_detail_df.groupby("DEVICE_ID"):
        total = len(d_df)
        d_name_series = d_df["DEVICE_NAME"].replace("", pd.NA).dropna()
        d_name = d_name_series.iloc[0] if not d_name_series.empty else ""
        
        for cat, c_df in d_df.groupby("CATEGORY"):
            summary_rows.append({
                "DEVICE_ID": str(d_id),
                "DEVICE_NAME": str(d_name),
                "ISSUE_TOTAL_COUNT": int(total),
                "CATEGORY": str(cat),
                "CATEGORY_COUNT": int(len(c_df)),
                "PERCENTAGE": round(len(c_df) / total * 100, 1),
                "AVERAGE_ABNORMAL_TIME": round(float(c_df["ABNORMAL_DATE"].mean()), 1)
            })
            
    final_summary_df = pd.DataFrame(summary_rows).sort_values(["ISSUE_TOTAL_COUNT", "CATEGORY_COUNT"], ascending=[False, False])

    # 步驟 5: 寫入 MySQL
    try:
        print("開始寫入資料庫...")
        final_detail_df.to_sql("iap_ae_device_detail", con=engine, if_exists="replace", index=False)
        final_summary_df.to_sql("iap_ae_device_summary", con=engine, if_exists="replace", index=False)
        print("資料庫寫入完成！")
    except Exception as e:
        print(f"寫入資料庫失敗: {e}")