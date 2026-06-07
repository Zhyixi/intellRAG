import sys
from pymongo import MongoClient
from pymongo.errors import BulkWriteError

# 確保能讀取到你專案的模組
sys.path.extend(['.', '..'])
from repositories.repositories import MongoRepository


def run_migration():
    # 1. === 設定來源 (Source) 與目標 (Target) 資料庫資訊 ===
    # 如果是同一個 MongoDB 伺服器，這兩個 URI 可以填一樣的
    SOURCE_MONGO_URI = "mongodb://root:123456@10.129.128.25:27017/?authSource=admin"
    SOURCE_DB_NAME = "LLM"
    SOURCE_COLLECTION = "iap_ae_Chat_History"
    

    TARGET_MONGO_URI = "mongodb://root:123456@10.129.128.25:37017/?authSource=admin"
    TARGET_DB_NAME = "LLM" # 換成你 B 資料庫的名字
    TARGET_COLLECTION = "iap_ae_Chat_History"

    # 2. === 實例化 Repository ===
    source_client = MongoClient(SOURCE_MONGO_URI)
    source_repo = MongoRepository(default_database=SOURCE_DB_NAME, mongo_client=source_client)

    target_client = MongoClient(TARGET_MONGO_URI)
    target_repo = MongoRepository(default_database=TARGET_DB_NAME, mongo_client=target_client)

    # 透過你寫好的 get_collection 方法取得底層集合物件
    source_coll = source_repo.get_collection(SOURCE_COLLECTION, SOURCE_DB_NAME)
    target_coll = target_repo.get_collection(TARGET_COLLECTION, TARGET_DB_NAME)

    # 3. === 使用 Cursor 取資料 (防止 OOM) ===
    # 不要用 find_list，因為那會把幾萬筆資料一次塞進 List
    cursor = source_coll.find({}) 
    
    batch_size = 1000  # 每 1000 筆寫入一次
    batch_data = []
    total_inserted = 0

    print(f"🚀 開始從 {SOURCE_DB_NAME}.{SOURCE_COLLECTION} 遷移資料至 {TARGET_DB_NAME}.{TARGET_COLLECTION}...")

    # 4. === 批次處理與 Append 寫入 ===
    for doc in cursor:
        batch_data.append(doc)
        
        # 累積滿 1000 筆就執行一次 bulk insert
        if len(batch_data) >= batch_size:
            try:
                # ordered=False 是 Append 的靈魂，遇到 _id 重複會直接跳過，繼續塞後面的資料
                result = target_coll.insert_many(batch_data, ordered=False)
                total_inserted += len(result.inserted_ids)
            except BulkWriteError as bwe:
                # 攔截 _id 重複的錯誤，nInserted 代表實際上成功塞進去的筆數
                inserted_count = bwe.details['nInserted']
                total_inserted += inserted_count
            
            # 清空 batch 準備裝下一批
            batch_data = [] 
            print(f"⏳ 目前已成功 Append {total_inserted} 筆資料...")

    # 5. === 處理最後剩餘 (不滿 1000 筆) 的資料 ===
    if batch_data:
        try:
            result = target_coll.insert_many(batch_data, ordered=False)
            total_inserted += len(result.inserted_ids)
        except BulkWriteError as bwe:
            total_inserted += bwe.details['nInserted']

    print(f"✅ 遷移完成！總共成功 Append 了 {total_inserted} 筆資料。")

if __name__ == "__main__":
    run_migration()