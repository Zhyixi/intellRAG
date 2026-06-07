from pydantic import BaseModel, Field
from datetime import datetime
from typing import List, Optional


class ChatHistorySchema(BaseModel):
    session_id:str
    role: str
    messages: str
    response: str
    timestamp: datetime
    
class ImagePathSchema(BaseModel):
    filename:str
    page_number:str | float | int

# user feedback
class UserFeedbackSchema(BaseModel):
    usr_id: str 
    bot_msg: str | None
    usr_msg: str | None
    feedback: bool
    suggestion: str | None
    date_time: datetime



# --- 這是給前端送過來的請求格式 (Request Body) ---
class TranslateRequest(BaseModel):
    texts: List[str] = Field(
        ..., 
        description="待翻譯的文字列表", 
    )
    source_lang: str = Field("auto", description="來源語言代碼 (預設為 auto)")
    target_lang: str = Field(..., description="目標語言代碼 (如: vi, en, pt)")
    # Pydantic V2 設定寫法
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "texts": [
                        "FATP Galio RAM A 2Y4W 问题，更换MB、CPU及RAM后部分复现，需进一步分析。",
                        "KB排线TP连接不良，因PPID贴错致组装失败。"
                    ],
                    "source_lang": "zh",
                    "target_lang": "en",
                    
                }
            ]
        }
    }
    

# --- 這是回傳給前端的格式 (Response Body) ---
# 定義單筆翻譯結果
class TranslationItem(BaseModel):
    original: str
    translated: str
    src: str
    tgt: str

# 定義整體回傳結構
class TranslateResponse(BaseModel):
    results: List[TranslationItem]

# --- (原本的 class 若是要存進 DB 的紀錄，可以保留並獨立出來) ---
class TranslationLog(BaseModel):
    texts: List[str]
    bot_msg: str | None
    usr_msg: str | None
    feedback: bool
    suggestion: str | None
    date_time: datetime


"FATP Galio RAM A 2Y4W 问题，更换MB、CPU及RAM后部分复现，需进一步分析。"
