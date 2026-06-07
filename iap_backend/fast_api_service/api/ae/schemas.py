from pydantic import BaseModel, Field
from datetime import datetime
from typing import List, Optional

# message content
class MessageSchema(BaseModel):
    role: str = Field(...,example="10038437", description="使用者員工編號, 須由前端傳入")
    content: str = Field(...,example="螺絲鎖附異常/帮我查下汇川机械手急停报警原因", description="檢索問題")
    session_id:str = Field(...,example="b0ee604b-08e9-4241-94ec-2f5e70f627e0",
                           description="session_id, 須由前端以uuid4規範生成後傳入, 作為榜定對話用id")
    syslang: str = Field(default="ZH", examples=["ZH"], description="對話語言")
    test_flag: Optional[bool] = Field(default=False, example=True, description="是否為測試模式，預設為 False，可不傳")


# invoke v1 用
class MessageSchemaDirectV1(BaseModel):
    text_input: str = Field(
        ..., 
        example="螺絲鎖附異常", 
        description="檢索問題 (必填)"
    )
    role: Optional[str] = Field(
        default="10038437", 
        example="10038437", 
        description="使用者員工編號, 前端可選擇性傳入 (非必要)"
    )
    
    syslang: Optional[str] = Field(default="ZH", examples=["ZH"], description="對話語言")
    # 將 session_id 改為非必要參數，預設值為 None
    session_id: Optional[str] = Field(
        default="b0ee604b-08e9-4241-94ec-2f5e70f627e0", 
        example="b0ee604b-08e9-4241-94ec-2f5e70f627e0",
        description="session_id, 須由前端以uuid4規範生成後傳入。若未傳送可由後端自動生成 (非必要)"
    )

class MessageSchemaDirect(BaseModel):
    role: Optional[str] = Field(
        default=None, 
        example="10038437", 
        description="使用者員工編號, 前端可選擇性傳入 (非必要)"
    )
    # content 通常是必要的檢索問題，所以保留 ...
    content: str = Field(
        ..., 
        example="螺絲鎖附異常/帮我查下汇川机械手急停报警原因", 
        description="檢索問題 (必填)"
    )
    syslang: str = Field(default="ZH", examples=["ZH"], description="對話語言")

    # 將 session_id 改為非必要參數，預設值為 None
    session_id: Optional[str] = Field(
        default=None, 
        example="b0ee604b-08e9-4241-94ec-2f5e70f627e0",
        description="session_id, 須由前端以uuid4規範生成後傳入。若未傳送可由後端自動生成 (非必要)"
    )

    
class ImagePathSchema(BaseModel):
    filename:str
    page_number:str | float | int
    
class ResponsesModel(BaseModel):
    role: str
    results: List[dict] | List[str]  # 需 Python 3.10+ 才支援 | 語法
    chat_response: str
    ret_type: str = Field(default="ae_sop", examples=["ae_sop"], description="project tag")
    source: List[str] = Field(default_factory=list)
    suggested_questions: List[str] = Field(default_factory=list)
    device_ids: List[str] = Field(default_factory=list, examples=[["1103017020"]], description="從使用者輸入擷取出的 10 位設備 ID 清單")
