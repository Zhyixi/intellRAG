import os, sys
sys.path.extend(['.', '..'])
import uuid
import pandas as pd
from common.utils import list_all_files
from fast_api_service import mongo_db
# 上傳歷史紀錄到mongo
def test_upload_history():
    files = list_all_files("/app/tmp_data/smt_history")
    for file in files:
        df = pd.read_csv(file)
        for idx, row in df.iterrows():
            try:
                user_id = row.Role
            except:
                user_id = "unknown"
            session_id = uuid.uuid3(uuid.NAMESPACE_DNS, str(user_id))
            mongo_db.insert_many(database_name="LLM",
                                 collection_name="smt_chat_history",
                                 data=[{"session_id":str(session_id),"timestamp":row.timestamp,
                                   "Question":row.Question ,
                                   "Role": user_id,
                                   "Response": row.Response,
                                   "Syntax":row.Syntax ,
                                   "sys":"smt",'Time-Consuming':str(row['Time-Consuming'])}])
            pass



if __name__ == '__main__':
    test_upload_history()