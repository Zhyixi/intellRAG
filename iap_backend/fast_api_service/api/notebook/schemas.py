from pydantic import BaseModel, Field
from typing import List, Optional


class NotebookChatRequest(BaseModel):
    content: str = Field(default="", description="用户消息")
    session_id: str = Field(..., description="对话 session UUID")
    syslang: str = Field(default="zh", description="回复语言")
    confirm_web_search: bool = Field(default=False, description="用户确认上网搜索")
    web_search_query: Optional[str] = Field(default=None, description="待搜索的原问题")
    langfuse_prompt_label: Optional[str] = Field(
        default=None,
        description="Langfuse prompt label (production / test); default production",
    )


class NotebookChatResponse(BaseModel):
    role: str = "assistant"
    chat_response: str
    results: List[dict] = Field(default_factory=list)
    ret_type: str = "notebook"
    source: List[str] = Field(default_factory=list)
    suggested_questions: List[str] = Field(default_factory=list)
    offer_web_search: bool = False
    pending_web_search_query: str = ""
    needs_clarification: bool = False
    answered_from_memory: bool = False
