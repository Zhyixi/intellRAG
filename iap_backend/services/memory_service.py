"""Long-term user memory (summaries, preferences, facts)."""
from __future__ import annotations

import datetime
import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)

NB_MEMORY = "nb_memory_long"
MAX_MEMORIES = 20


class MemoryService:
    def __init__(self, mongo_service):
        self._mongo = mongo_service

    def list_memories(self, user_id: int, limit: int = 8) -> list[dict]:
        df = self._mongo.find_df(
            query={"user_id": user_id},
            collection_name=NB_MEMORY,
            sort=[("timestamp", -1)],
            limit=limit,
        )
        if df.empty:
            return []
        return df.to_dict(orient="records")

    def format_for_prompt(self, user_id: int) -> str:
        rows = self.list_memories(user_id, limit=8)
        if not rows:
            return ""
        lines = [f"- {r.get('content', '')}" for r in rows if r.get("content")]
        return "用户长期记忆（请参考但勿重复赘述）：\n" + "\n".join(lines)

    async def maybe_extract_memory(
        self,
        *,
        user_id: int,
        session_id: str,
        user_input: str,
        assistant_reply: str,
        llm: ChatOpenAI,
        turn_count: int,
    ) -> None:
        if turn_count % 5 != 0:
            return
        prompt = [
            SystemMessage(
                content=(
                    "从以下对话中提取 1-3 条值得长期记住的信息（用户偏好、项目背景、关键事实）。"
                    "若无值得记住的内容，只回复 NONE。每条一行，不要编号。"
                )
            ),
            HumanMessage(content=f"用户：{user_input}\n助手：{assistant_reply}"),
        ]
        try:
            resp = await llm.ainvoke(prompt)
            text = (resp.content or "").strip()
            if not text or text.upper() == "NONE":
                return
            for line in text.splitlines():
                content = line.strip().lstrip("-•0123456789. ")
                if len(content) < 4:
                    continue
                self._mongo.insert_many(
                    insert_data={
                        "user_id": user_id,
                        "session_id": session_id,
                        "content": content,
                        "timestamp": datetime.datetime.utcnow(),
                    },
                    collection_name=NB_MEMORY,
                )
            self._trim_old(user_id)
        except Exception as exc:
            logger.warning("memory extract failed: %s", exc)

    def _trim_old(self, user_id: int) -> None:
        df = self._mongo.find_df(
            query={"user_id": user_id},
            collection_name=NB_MEMORY,
            sort=[("timestamp", -1)],
        )
        if len(df) <= MAX_MEMORIES:
            return
        cutoff_row = df.iloc[MAX_MEMORIES - 1]
        cutoff = cutoff_row.get("timestamp")
        if cutoff is not None:
            self._mongo.delete_many(
                query={"user_id": user_id, "timestamp": {"$lt": cutoff}},
                collection_name=NB_MEMORY,
            )
