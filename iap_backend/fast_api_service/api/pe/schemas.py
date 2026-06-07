from pydantic import BaseModel, Field
from datetime import datetime
from typing import List, Optional

# message content
class MessageSchema(BaseModel):
    role: str = Field(...,example="10038437", description="使用者員工編號, 須由前端傳入")
    content: str = Field(...,example="V350螺絲浮鎖問題怎麼處理？", description="檢索問題")
    session_id:str = Field(...,example="b0ee604b-08e9-4241-94ec-2f5e70f627e0", description="session_id, 須由前端以uuid4規範生成後傳入, 作為榜定對話用id")
    syslang: str = Field(default="ZH", examples=["ZH"], description="對話語言")
    test_flag: Optional[bool] = Field(default=False, example=True, description="是否為測試模式，預設為 False，可不傳")

class MessageSchemaDirect(BaseModel):
    role: Optional[str] = Field(
        default=None, 
        example="10038437", 
        description="使用者員工編號, 前端可選擇性傳入 (非必要)"
    )
    # content 通常是必要的檢索問題，所以保留 ...
    content: str = Field(
        ..., 
        example="V350螺絲浮鎖問題怎麼處理？", 
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
    

    
# Response Model
class HitItem(BaseModel):
    snapshot: str
    page: str | int
    content: str

class FileData(BaseModel):
    path: str
    hits: List[HitItem]

class CauseDetail(BaseModel):
    DESCRIPTION: str
    # 定義固定的 ImagePath_0 ~ 2
    ImagePath_0: str
    ImagePath_1: str
    ImagePath_2: str
    # 定義固定的 file_0 ~ 2
    file_0: FileData
    file_1: FileData
    file_2: FileData

class RelatedFile(BaseModel):
    # 必須包含這五個特定的 Key
    rootCause: CauseDetail
    actionCause: CauseDetail
    explanCause: CauseDetail
    poorDescription: CauseDetail
    studyCause: CauseDetail

class ResultItem(BaseModel):
    ISSUE_ID: str
    Text: List[str]
    Score: float
    relatedFile: RelatedFile

class ResponseModel(BaseModel):
    role: str
    results: List[ResultItem] | List[dict] | List[str]  # 需 Python 3.10+ 才支援 | 語法
    chat_response: str
    ret_type: str = Field(default="ae_sop", examples=["ae_sop"], description="project tag")
    source: List[str] = Field(default_factory=list)
    suggested_questions: List[str] = Field(default_factory=list)
    
# =========================================================
# Request Schema：/analyst
# =========================================================

class iapAnalystRequest(BaseModel):
    IssueID: List[str] = Field(
        ...,
        description=(
            "異常事件 IssueID 清單。"
            "系統會根據輸入的 IssueID 查詢對應異常資料並進行統計分析。"
        ),
        examples=[
            [
                "SFCW50826012B",
                "SFCW50612015B",
                "SFCW51106031B",
                "SFCW60108005B",
                "SFCW41228009B"
            ]
        ]
    )


# =========================================================
# Request Schema：/analyst-detail
# =========================================================

class iapAnalystDetailRequest(BaseModel):
    ErrorCode: List[str] = Field(
        ...,
        description="ErrorCode 清單。",
        examples=[
            [
                "6A07",
                "6U99"
            ]
        ]
    )
