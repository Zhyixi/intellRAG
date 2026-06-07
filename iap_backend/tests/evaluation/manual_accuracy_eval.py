import sys, os
sys.path.extend(['.', '..'])
import jieba, json
import pandas as pd
import jieba.analyse
import ast
import datetime
import requests
import tqdm
from containers import Container


OUTPUT_PATH = "/app/tests/reports/iap_dataset_query_result.csv"


def retrieve_for_eval(query):
    url = 'http://localhost:44000/api/v1/iap/retrieve'
    t0 = datetime.datetime.now()
    response = requests.post(url, json={
            "role": "10038437",
            "content": query,
            "session_id": "b0ee604b-08e9-4241-94ec-2f5e70f627e0",
            "test_flag": True
        }, headers={"Content-Type": "application/json"})
    t1 = datetime.datetime.now()
    # 檢查狀態碼
    if response.status_code == 200:
        return response.json()['results'], response.json()['chat_response'], (t1-t0).total_seconds()
    else:
        raise AssertionError(f"請求失敗，狀態碼: {response.status_code}", (t1-t0).total_seconds())

def convert_to_json(ans):
    try:
        ans_dict = ast.literal_eval(ans)
        return ans_dict
    except Exception as e:
        print(f"轉換失敗: {e}")
        return None

def extract_texts_from_ans(ans):
    try:
        ans_dict = json.loads(ans.replace("'", "\""))
        texts = [result.get("Text", "") for result in ans_dict.get("results", [])]
        return texts
    except Exception as e:
        return []

def jibt_tokenize(text, topK=None):
    tokens = jieba.analyse.extract_tags(text, topK=topK)
    return tokens

def calculate_overlap(query_tokens, text_tokens):
    query_set = set(query_tokens)
    text_set = set(text_tokens)
    overlap = query_set.intersection(text_set)
    return len(overlap)

container = Container()
container.wire(modules=[__name__])
es_client =  container.es_client()
llm =  container.ollama_general_llm()

container = Container()
container.init_resources()     
es_client = container.es_client()  

def generator_queries(content: str) -> list:
    """
    修改為一次產生三種不同品質的提問
    """
    prompt = f"""
    你是一位資深維修技術助理。請根據以下維修記錄，產生三句不同類型的使用者問句，用來測試檢索系統。

    【維修記錄內容】
    ---
    {content}
    ---

    請嚴格按照以下格式輸出三行文字，每行代表一種提問類型，不要加上任何編號、前綴或額外說明：
    (第一行) 精確提問：包含完整機型與錯誤代碼。例如「SV630A 出現 E150.0 錯誤代碼該如何處理？」
    (第二行) 口語提問：模擬現場人員急躁、隨意的提問，可省略主詞或使用簡稱。例如「匯川 E150 怎麼解？」
    (第三行) 描述提問：不寫出錯誤代碼，僅描述異常現象。例如「馬達鎖死不動，顯示保護狀態怎麼辦？」

    請確保只輸出三行問句。
    """
    for i in range(3):
        try:
            response_text = llm.chat(usr_input=prompt, system_prompt="")
            # 將結果依換行符號切割，並過濾掉空行
            queries = [q.strip() for q in response_text.split('\n') if q.strip()]
            # 確保至少取到前三個問句
            if len(queries) >= 3:
                return queries[:3]
        except Exception as e:
            continue
    # 若生成失敗，回傳一個基本的防呆列表
    return ["請幫我查詢此維修紀錄的問題點", "這個異常怎麼處理", "發生什麼事了"]

async def retrieve_evaluate(n: int = 1):
    query_body = {
        "query": {
            "match_all": {}
        },
        "size": n,
    }
    response = await es_client.search(
        index="iap",
        body=query_body
    )
    hits = response["hits"]["hits"]
    return hits

async def main():
    top_n = 5
    if 1:
        os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
        # 準備要收集的欄位
        columns = ["issue_id", "query_type", "gen_query", "content", "ISSUE match", "MRR_Hit", "cost_time", "chat_response"]
        for i in range(top_n):
            columns.extend([f"Match Score_{i}", f"evaluate_{i}"])
            
        eval_list = []
        evaluate_data = await retrieve_evaluate(n=50)
        
        # 預期總測試數為 n * 3 (三種問題)
        progress_bar = tqdm.tqdm("testing...", total=len(evaluate_data) * 3)
        
        for row in evaluate_data:
            hit = row['_source']
            issue_id = hit["metadata"]['basic_information']['ISSUE_ID']
            content = hit["content"]
            
            # 取得三種不同品質的提問
            gen_queries = generator_queries(content=content)
            query_types = ["精確提問", "口語提問", "描述提問"]
            
            for q_idx, gen_query_str in enumerate(gen_queries):
                row_data = {
                    "issue_id": issue_id,
                    "query_type": query_types[q_idx] if q_idx < 3 else "其他",
                    "gen_query": gen_query_str,
                    "content": content,
                    "ISSUE match": 0,
                    "MRR_Hit": -1
                }
                
                try:
                    ans, chat_response, cost_time = retrieve_for_eval(query=gen_query_str)
                    row_data["cost_time"] = cost_time
                    row_data["chat_response"] = chat_response
                    
                    ans_nodes = []
                    for i, n_res in enumerate(ans[:top_n]):
                        ans_nodes.append(f"{n_res['ISSUE_ID']}")
                        row_data[f"Match Score_{i}"] = n_res['Score']
                        row_data[f"evaluate_{i}"] = f"{n_res['ISSUE_ID']}"

                    if issue_id in ans_nodes:
                        row_data["ISSUE match"] = 1

                    for hit_n, n_res in enumerate(ans):
                        if n_res['ISSUE_ID'] == issue_id:
                            row_data["MRR_Hit"] = f"{hit_n}"
                            break

                except Exception as e:
                    row_data["chat_response"] = f"Error: {str(e)}"
                    row_data["cost_time"] = 0

                eval_list.append(row_data)
                progress_bar.update(1)
                
                # 即時寫入，避免中斷遺失資料
                eval_df = pd.DataFrame(eval_list)
                eval_df.to_csv(OUTPUT_PATH, index=False)

        progress_bar.close()
        eval_df.index += 1  
        eval_df.to_csv(OUTPUT_PATH, index=False)
        print(f"測試完成，結果已存入 {OUTPUT_PATH}")

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())