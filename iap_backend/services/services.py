# TEST! DI 商業邏輯
"""Services module."""
import sys, os, re, json
sys.path.extend(['.', '..'])
from repositories.repositories import MongoRepository
from typing import List, Dict
from pydantic import BaseModel, Field
from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.messages import SystemMessage, HumanMessage
import datetime
import logging
from langfuse import observe
import datetime
import asyncio
from typing import List, Dict, TypedDict, Optional
from langgraph.graph import StateGraph, END

class MongoService:
    def __init__(self, mongo_repo: MongoRepository) -> None:
        self._repository: MongoRepository = mongo_repo

    def insert_many(self, insert_data, collection_name: str = "iap_Chat_History"):
        # 這裡 insert_data 封裝成 list 是正確的，因為 repo 接收 list
        return self._repository.insert_many(data=[insert_data], collection_name=collection_name)

    def find_df(self, query: dict, collection_name: str = "iap_Chat_History", sort: list = None, limit: int = 0):
        return self._repository.find_df(
            collection_name=collection_name, 
            query=query, 
            sort=sort, 
            limit=limit
        )
    
    def find_list(self, session_id: str, collection_name: str = "iap_Chat_History"):
        return self._repository.find_list(query={'session_id': session_id}, collection_name=collection_name)
    
    # 修正後的刪除方法
    def delete_many(self, query: dict, collection_name: str = "iap_Chat_History"):
        """
        刪除指定條件的數據
        """
        return self._repository.delete_many(
            collection_name=collection_name, 
            query=query
        )

    def update_one(self, query: dict, update: dict, collection_name: str, upsert: bool = False):
        return self._repository.update_one(
            collection_name=collection_name,
            query=query,
            update=update,
            upsert=upsert,
        )

    def find_one(self, query: dict, collection_name: str):
        return self._repository.find_one(collection_name=collection_name, query=query)
    def aggregate_to_df(self, pipeline: list, collection_name: str = "iap_Chat_History"):
        """
        執行 MongoDB 聚合查詢並回傳 pandas DataFrame
        """
        # 直接呼叫 repository 層的實作
        return self._repository.aggregate_to_df(
            collection_name=collection_name, 
            pipeline=pipeline
        )

# 1. 定義結構化輸出的資料模型
class TranslationResult(BaseModel):
    translated_text: str = Field(description="翻譯後的純文字內容")
    is_successful: str = Field(description="翻譯是否成功，僅回傳 Y 或 N")


# 1. 定義 State
class TranslationState(TypedDict):
    content: str
    source_name: str
    target_name: str
    format_instructions: str
    raw_response: str
    thought_process: str
    translated_text: Optional[str]
    success: str
    cost_time: float


class TranslatorService:
    def __init__(self, llm:ChatOllama) -> None:
        self.llm = llm
        self.parser = PydanticOutputParser(pydantic_object=TranslationResult)
        self.graph = self._build_graph()
        
    def translate_zh2vi(self, texts:list[str]):
        from langfuse import get_client
        from common.langfuse_tracing import compile_langfuse_prompt_or_fallback, langfuse_configured

        lf_client = get_client() if langfuse_configured() else None
        system_prompt = compile_langfuse_prompt_or_fallback(
            lf_client,
            "translate_zh2vi",
            fallback="你是一位專業越南文筆譯專家，僅輸出最終譯文並保留 Markdown、程式碼與專有名詞。",
        )
        results = []
        for content in texts:
            user_payload = f"""模式：直譯
            風格：正式
            ———
            請將以下內容翻譯成越南文，保留原格式與程式碼：
            {content}
            /no_think"""
            _, resp = self.llm.chat(
                usr_input=user_payload, system_prompt=system_prompt, temperature=0.1,
                 max_tokens=1024, thinking=False)
            results.append({f"{content}":f"{resp}"})
        return results
    
    def translate_bak(self, texts: list[str], source_lang: str = "auto", target_lang: str = "vi"):
        """
        :param source_lang: 來源語言代碼 (預設為 auto)
        :param target_lang: 目標語言代碼
        """
        
        # 1. 定義語言代碼映射
        lang_map = {
            "vi": "越南文（Tiếng Việt）",
            "en": "英文（English）",
            "pt": "葡萄牙文（Português）",
            "sp": "西班牙文（Español）",
            "zh": "中文（Chinese）",
            # "auto" 不需要在這裡定義，因為我們會用邏輯處理
        }
        
        target_name = lang_map.get(target_lang, target_lang)

        # 2. 【關鍵修改】動態生成 System Prompt
        if source_lang == "auto":
            # 情境 A: 自動偵測 -> 設定為「多語言翻譯專家」
            role_description = f"你是一位擁有 20 年經驗的「電子製造與 電子零件硬體維修」技術翻譯專家，擅長將**任何語言**精準翻譯為「{target_name}」。"
        else:
            # 情境 B: 指定來源 -> 設定為「雙語對譯專家」
            source_name = lang_map.get(source_lang, source_lang)
            role_description = f"你是一位擁有 20 年經驗的「電子製造與 電子零件硬體維修」技術翻譯專家，擅長將「{source_name}」精準翻譯為「{target_name}」。"

        from langfuse import get_client
        from common.langfuse_tracing import compile_langfuse_prompt_or_fallback, langfuse_configured

        lf_client = get_client() if langfuse_configured() else None
        prompt_name = "translate_repair_auto" if source_lang == "auto" else "translate_repair"
        system_prompt = compile_langfuse_prompt_or_fallback(
            lf_client,
            prompt_name,
            fallback=f"{role_description}\n請將內容精準翻譯為{target_name}，保留硬體縮寫與 Markdown。",
            source_name=source_name if source_lang != "auto" else "任何語言",
            target_name=target_name,
        )
        
        results = []
        for content in texts:
            if source_lang == "auto":
                instruction = f"請將 <text> 標籤內的內容翻譯成 {target_name}："
            else:
                source_name = lang_map.get(source_lang, source_lang)
                instruction = f"請將 <text> 標籤內的 {source_name} 內容翻譯成 {target_name}："

            user_payload = f"""
            領域：電子硬體維修
            目標語言：{target_name}
            
            指令：
            1. {instruction}
            2. 注意：遇到「不良」或「失敗」請務必翻譯成對應的形容詞，不要直譯。
            3. **僅輸出翻譯結果**。

            <text>
            {content}
            </text>
            """

            _, resp = self.llm.chat(
                usr_input=user_payload, 
                system_prompt=system_prompt, 
                temperature=0.1,
                max_tokens=1024, 
                thinking=False
            )
            clean_resp = resp.replace("Translation:", "").replace("譯文：", "").strip()
            clean_resp = clean_resp.replace("<text>", "").replace("</text>", "").strip()
            
            results.append({
                "original": content,
                "translated": clean_resp.strip(),
                "src": source_lang,  # 這裡會回傳 "auto"，這是正確的，代表使用者當初是用自動模式
                "tgt": target_lang
            })
            
        return results
    
    def _extract_and_clean(self, content: str) -> str:
        """分離思考過程並提取翻譯結果"""
        # 移除 <think>...</think> 標籤及其內容
        clean_content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
        return clean_content

    @observe(as_type="generation")
    async def translate(self, texts: List[str], source_lang: str = "auto", target_lang: str = "VI", think=False) -> List[Dict]:
        lang_map = {
            "vi": "越南文（Tiếng Việt）", "en": "英文（English）",
            "pt": "葡萄牙文（Português）", "es": "西班牙文（Español）", "zh": "中文（Chinese）",
        }
        target_name = lang_map.get(target_lang, target_lang)
        source_name = lang_map.get(source_lang, "任何語言") if source_lang == "auto" else lang_map.get(source_lang, source_lang)
        from langfuse import get_client
        from common.langfuse_tracing import compile_langfuse_prompt_or_fallback, langfuse_configured

        lf_client = get_client() if langfuse_configured() else None
        system_template = compile_langfuse_prompt_or_fallback(
            lf_client,
            "translate_repair_json",
            fallback=(
                "你是一位維修翻譯專家，擅長將「{source_name}」翻譯為「{target_name}」。\n"
                "輸出格式必須嚴格遵守：\n{format_instructions}"
            ),
            source_name=source_name,
            target_name=target_name,
            format_instructions=self.parser.get_format_instructions(),
        )
        if think:
            # 建立 Prompt 物件
            prompt = ChatPromptTemplate.from_messages([
                ("system", system_template),
                ("user", "請翻譯以下維修內容：\n<text>{content}</text>")
            ])
        else:
            prompt = ChatPromptTemplate.from_messages([
                ("system", system_template),
                ("user", "請翻譯以下維修內容：\n<text>{content}</text> /no_think")
            ])
        # 建立 Chain
        chain = prompt | self.llm
        results = []
        for content in texts:
            t0 = datetime.datetime.now()
            try:
                raw_response = chain.invoke({"content": content})
                full_text = raw_response.content
                
                # 分離思考過程 (<think>...</think>)
                thought_process = ""
                think_match = re.search(r'<think>(.*?)</think>', full_text, re.DOTALL)
                if think_match:
                    thought_process = think_match.group(1).strip()
                    # 移除 think 部分，剩下的就是 JSON
                    clean_json = re.sub(r'<think>.*?</think>', '', full_text, flags=re.DOTALL).strip()
                else:
                    clean_json = full_text
                # 結構化解析結果
                parsed_data = self.parser.parse(clean_json)
                t1 = datetime.datetime.now()
                translated = parsed_data.translated_text
                translated = re.sub(r'</?text>', '', translated)
                success = parsed_data.is_successful
            except Exception as e:
                translated = f"Error: {str(e)}"
                success = "N"
                thought_process = ""
            t1 = datetime.datetime.now()
            cost_time = round((t1-t0).total_seconds(), 3)
            results.append({
                "original": content,
                "translated": translated,
                "success": success,
                "thought": thought_process,
                "src": source_lang,
                "tgt": target_lang,
                "cost_time":cost_time
            })
        return results

    def _build_graph(self):
        builder = StateGraph(TranslationState)
        
        # 節點 1: 負責與 LLM 互動
        async def translate_node(state: TranslationState):
            t0 = datetime.datetime.now()
            
            # 直接組合 Message List
            system_text = f"""你是一位擁有 20 年經驗的維修翻譯專家。
            擅長將「{state['source_name']}」翻譯為「{state['target_name']}」。

            核心規則：
            1. 絕對不保留「不良」、「損壞」等狀態詞的原文。
            2. 保留硬體縮寫（KB, TP, MB, CPU）。
            3. 輸出格式必須嚴格遵守下方的 JSON 結構。
            4. 若文字內容沒有意義, 並在 translated 欄位輸出原文, 算是翻譯成功
            5. 原文內容可能是混合的語言, 請將統一翻譯為「{state['target_name']}」。

            {state['format_instructions']}
            """
            
            user_text = f"請翻譯以下維修內容：\n<text>{state['content']}</text>"
            if not state.get("think", False):
                user_text += " /no_think"
            
            # 直接呼叫 LLM
            response = await self.llm.ainvoke([
                SystemMessage(content=system_text),
                HumanMessage(content=user_text)
            ])
            
            full_text = response.content
            think_match = re.search(r'<think>(.*?)</think>', full_text, re.DOTALL)
            thought = think_match.group(1).strip() if think_match else ""
            clean_json = re.sub(r'<think>.*?</think>', '', full_text, flags=re.DOTALL).strip()
            
            t1 = datetime.datetime.now()
            return {
                "raw_response": clean_json,
                "thought_process": thought,
                "cost_time": round((t1-t0).total_seconds(), 3)
            }

        # 節點 2: 負責解析與邏輯判斷
        async def parse_node(state: TranslationState):
            try:
                parsed_data = self.parser.parse(state["raw_response"])
                translated = re.sub(r'</?text>', '', parsed_data.translated_text)
                return {
                    "translated_text": translated, 
                    "success": parsed_data.is_successful
                }
            except Exception as e:
                return {
                    "translated_text": f"Error: {str(e)}", 
                    "success": "N"
                }

        builder.add_node("translate", translate_node)
        builder.add_node("parse", parse_node)
        builder.add_edge("translate", "parse")
        builder.add_edge("parse", END)
        builder.set_entry_point("translate")
        return builder.compile()

    @observe(name="",as_type="generation")
    async def translate_batch(self, texts: List[str], source_lang: str, target_lang: str, think=False) -> List[Dict]:
        # 準備批量資料
        lang_map = {"vi": "越南文", "en": "英文", "pt": "葡萄牙文", "es": "西班牙文", "zh": "中文"}
        target_name = lang_map.get(target_lang, target_lang)
        source_name = lang_map.get(source_lang, "任何語言") if source_lang == "auto" else lang_map.get(source_lang, source_lang)
        
        initial_states = [{
            "content": content,
            "source_name": source_name,
            "target_name": target_name,
            "format_instructions": self.parser.get_format_instructions()
        } for content in texts]

        # 使用 abatch 進行併行處理
        results = await self.graph.abatch(initial_states)
        # 格式化輸出
        final_output = []
        for i, res in enumerate(results):
            final_output.append({
                "original": texts[i],
                "translated": res.get("translated_text"),
                "success": res.get("success"),
                "thought": res.get("thought_process"),
                "src": source_lang,
                "tgt": target_lang,
                "cost_time": res.get("cost_time")
            })
        return final_output