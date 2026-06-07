import datetime
import logging
from typing import Annotated, List, Literal

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict
from langchain_core.messages import AnyMessage, AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

from fast_api_service.api.pe.schemas import MessageSchema, ResponseModel
from langgraph.checkpoint.mongodb import MongoDBSaver
from common.langfuse_tracing import propagate_run_attributes, resolve_trace_id


# 1. 建立安全的訊息合併器，過濾掉因為前次崩潰而存在資料庫的 None (髒資料)
def safe_add_messages(left: list | AnyMessage | None, right: list | AnyMessage | None) -> list[AnyMessage]:
    if isinstance(left, list):
        left = [m for m in left if m is not None]
    if isinstance(right, list):
        right = [m for m in right if m is not None]
    return add_messages(left, right)


# 2. 狀態定義：使用 TypedDict 與自訂的安全合併器
class AgentState(TypedDict, total=False):
    messages: Annotated[List[AnyMessage], safe_add_messages]
    refined_query: str
    intent: Literal["chat", "pe_faca", "direct_analysis", ""]
    version: Literal["v1", "v2", "v3"]
    syslang: Literal["zh", "en", "vi", "pt", "es"]
    input_lang: Literal["zh-cn", "zh", "en", "vi", "pt", "es"]
    session_id: str
    results: list
    final_response: str
    source: list
    suggested_questions: list



async def zh_cn_determin_node(state: AgentState, config: RunnableConfig):
    retrice_service = config["configurable"]["retrice_service"]
    user_input = state["messages"][-1].content
    syslang = config["configurable"].get("syslang", "zh")
    
    if syslang == "zh":
        zh_type = await retrice_service.detect_zh_type(user_input)
        if zh_type == "zh-cn":
            final_lang = "cn"
        elif zh_type == "zh-tw":
            final_lang = "zh"
        else:
            final_lang = "zh"  # 預設
            
        # 輸入翻譯
        if final_lang == "cn":
            user_input = retrice_service.t2s.convert(str(user_input))
        else:
            user_input = retrice_service.s2t.convert(str(user_input))
    else:
        final_lang = syslang
        
    return {"refined_query": user_input, "input_lang": final_lang}


async def check_intent_node(state: AgentState, config: RunnableConfig):
    retrice_service = config["configurable"]["retrice_service"]
    user_input = state.get("refined_query", "")
    
    spec_intent = config["configurable"].get("intent", None)
    
    
    judge = await retrice_service.check_is_chitchat(user_input)
    if judge:
        return {'intent': 'chat'}
    else:
        if spec_intent:
            return {'intent': spec_intent}
        else:
            return {'intent': 'pe_faca'}






async def translate_node(state: AgentState, config: RunnableConfig):
    translator = config["configurable"].get("translator")
    syslang = config["configurable"].get("syslang", "zh")
    input_lang = state.get("input_lang", "zh")
    retrice_service = config["configurable"]["retrice_service"]
    
    chat_response = state.get("final_response", "請詢問相關專業問題, 勿閒聊浪費資源")
    
    if syslang.lower() != 'zh' and translator:
        translated_chat_response = await translator.translate(
                                    texts=[chat_response], 
                                    source_lang="auto", 
                                    target_lang=syslang.lower()
                                )
        chat_response = translated_chat_response[0]['translated']
    elif syslang == 'zh' and input_lang == 'zh':
        chat_response = retrice_service.s2t.convert(str(chat_response))
    elif syslang == 'zh' and input_lang == 'cn':
        chat_response = retrice_service.t2s.convert(str(chat_response))
        
    return {'final_response': chat_response}


async def chat_node(state: AgentState, config: RunnableConfig):
    return {
        "messages": [AIMessage(content=str("請詢問專業問題"))], 
        "final_response": str("請詢問專業問題"), 
        "results":[] 
    }


async def direct_analysis_node(state: AgentState, config: RunnableConfig):
    retrice_service = config["configurable"]["retrice_service"]
    user_input = state.get("refined_query", "")
    
    current_trace_id = resolve_trace_id(config)
    response = await retrice_service.llm_direct_analysis(
        query=user_input, 
        trace_id=current_trace_id
    )
    
    return {
        "messages":[AIMessage(content=str(response))], 
        "final_response": str(response), 
        "results":[] 
    }
    
async def retrieve_node(state: AgentState, config: RunnableConfig):
    retrice_service = config["configurable"]["retrice_service"]
    user_input = state.get("refined_query", "")
    syslang = config["configurable"].get("syslang", "zh")
    version = config["configurable"].get("version", "v3")
    session_id = config["configurable"].get("session_id", "default")
    
    results_issue, chat_response_issue = await retrice_service.faca_retrieve(
        prompt=user_input, index_name=f"iap_issue_{syslang.lower()}"
    )
    try:
        result_file, chat_response_file = await retrice_service.faca_retrieve(
            prompt=user_input, index_name=f"iap_file_{syslang.lower()}"
        )
    except Exception as e:
        result_file =[]
        
    results = results_issue + result_file 
    chat_response = ""
    source =[]
    suggested_questions = []

    if version == 'v2':
        detail_lines =[]
        for row in results:
            for ttt in row.get('Text',[]):
                detail_lines.append(f"{ttt.replace('passage: ', '')}({row.get('ISSUE_ID', '')})")
        raw_str = "[詳情]\n\n" + "\n".join(detail_lines)
        chat_response_part2 = await retrice_service.llm_combination(query=user_input, raw_str=raw_str)
        chat_response_part1 = await retrice_service.llm_summary(query=user_input, raw_str=raw_str)
        chat_response = f"{chat_response_part1}\n[詳情]\n{chat_response_part2}"
    elif version == 'v3':
        ######################
        import tiktoken
        MODEL_CONTEXT_WINDOW = 16384
        MAX_OUTPUT_TOKENS = 2048
        encoding = tiktoken.get_encoding("cl100k_base")
        def count_tokens(text: str) -> int:
            return len(encoding.encode(text))
        def is_context_exceeded(prompt: str) -> tuple[bool, int]:
            input_tokens = count_tokens(prompt)
            total = input_tokens + MAX_OUTPUT_TOKENS
            exceeded = total >= MODEL_CONTEXT_WINDOW
            return exceeded, total
        ######################
        detail_lines =[]
        for row in results:
            detail_str = ""
            for ttt in row.get('Text',[]):
                judge_token, token_count = is_context_exceeded(ttt)
                if not judge_token and 'think' not in ttt:
                    detail_str += f"{row.get('ISSUE_ID', '')}\n{ttt.replace('passage: ', '')}"
                else:
                    logging.error(f"摘要文件超出範圍:{row.get('ISSUE_ID', '')}\n{ttt[:1500]}")
                    pass
            # for k, v in row.get('relatedFile', {}).items():
            #     detail_str += f"\n{k} : {v.get('DESCRIPTION', 'Empty') if v.get('DESCRIPTION') != '' else 'Empty'}"
            detail_lines.append(detail_str)
            
        raw_str = "\n\n" + "\n".join(detail_lines)
        response = await retrice_service.llm_total_summary(
            query=user_input,
            raw_str=raw_str,
            syslang=syslang,
            session_id=session_id,
            trace_id=resolve_trace_id(config),
            parent_node_name="retrieve_node",
            run_config=config,
        )
        response_text = response.answer
        source = response.source
        suggested_questions = response.suggested_questions
        chat_response = retrice_service.extract_deepseek_outputs(response_text)
        
        if len(source) != 0:
            source_section = "\n\n"
            for index, src in enumerate(source, start=1):
                source_section += f"[{index}] {src.replace('[','').replace(']','')}\n"
            chat_response += source_section
            
    return {
        "messages":[AIMessage(content=chat_response)], 
        "final_response": chat_response, 
        "results": results,
        "source": source,
        "suggested_questions": suggested_questions
    }

def route_logic_node(state: AgentState):
    intent = state.get("intent", "pe_faca")
    if intent == "pe_faca": 
        return "retrieve_node"
    elif intent == "direct_analysis": 
        return "direct_analysis_node"
    elif intent == "chat": 
        return "chat_node"
    else:
        return "chat_node"

def creat_graph(checkpointer: MongoDBSaver):
    pe_graph = StateGraph(AgentState)
    pe_graph.add_node("zh_cn_determin_node", zh_cn_determin_node)
    pe_graph.add_node("check_intent_node", check_intent_node)
    pe_graph.add_node("chat_node", chat_node)
    pe_graph.add_node("direct_analysis_node", direct_analysis_node)
    pe_graph.add_node("translate_node", translate_node)
    pe_graph.add_node("retrieve_node", retrieve_node)
    
    pe_graph.add_edge(START, "zh_cn_determin_node")
    pe_graph.add_edge("zh_cn_determin_node", "check_intent_node")
    pe_graph.add_conditional_edges(
        "check_intent_node",
        route_logic_node,
        {
            "chat_node": "chat_node",
            "direct_analysis_node": "direct_analysis_node",
            "retrieve_node": "retrieve_node"
        }
    )
    pe_graph.add_edge("chat_node", "translate_node")
    pe_graph.add_edge("direct_analysis_node", "translate_node")
    pe_graph.add_edge("retrieve_node", "translate_node")
    pe_graph.add_edge("translate_node", END)
    
    return pe_graph.compile(checkpointer=checkpointer)

async def process_agent_retrieve(user_input,
                                 run_config,
                                 checkpointer) -> ResponseModel:
    agent = creat_graph(checkpointer)
    metadata = run_config.get("metadata") or {}
    with propagate_run_attributes(
        session_id=metadata.get("session_id"),
        trace_name=metadata.get("langfuse_trace_name"),
        trace_id=metadata.get("trace_id"),
        environment=metadata.get("app_env"),
        domain=metadata.get("domain"),
    ):
        final_state = await agent.ainvoke(
            {"messages": [HumanMessage(content=user_input)]},
            config=run_config,
        )
    return final_state


