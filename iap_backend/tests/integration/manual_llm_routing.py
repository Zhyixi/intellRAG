import os, sys, asyncio, uuid, json, subprocess
sys.path.extend(['.', '..'])
from typing import TypedDict, List
from langchain_core.runnables import RunnableConfig
from langgraph.graph import StateGraph, END
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from containers import Container

from langfuse import propagate_attributes, get_client

container = Container()
llm = container.vllm_general_llm()
langfuse_client = container.langfuse_client()
langfuse_handler = container.langfuse_handler()
# 🌟 紀錄這回合發出的 Request ID
tracked_requests = {}

class GraphState(TypedDict):
    messages: List[BaseMessage]
    intent: str

# ==========================================
# 節點 1：意圖判斷
# ==========================================
async def intent_node(state: GraphState, config: RunnableConfig):
    trace_id = config.get("metadata", {}).get("trace_id")
    session_id = config.get("metadata", {}).get("session_id")
    
    # 🌟 產生這個節點專屬的 Request ID
    req_id = f"Req-Intent-{uuid.uuid4().hex[:6]}"
    tracked_requests[req_id] = "意圖判斷"
    
    # 透過 extra_body 的 metadata 傳給 LiteLLM 攔截器
    bound_llm = llm.bind(
        extra_body={"metadata": {"request_id": req_id, "session_id": session_id}}
    )
    
    sys_msg = SystemMessage(content="你是一個意圖分析師。根據用戶輸入，只能回答 'weather' 或 'math'。")
    response = await bound_llm.ainvoke([sys_msg] + state["messages"], config=config)
    
    intent = response.content.strip().lower()
    if "weather" not in intent and "math" not in intent: intent = "weather"
    print(f"🧠 [意圖判斷] 判斷為: {intent}")
    return {"intent": intent, "messages":[response]}

# ==========================================
# 節點 2a：查天氣
# ==========================================
async def weather_node(state: GraphState, config: RunnableConfig):
    session_id = config.get("metadata", {}).get("session_id")
    req_id = f"Req-Weather-{uuid.uuid4().hex[:6]}"
    tracked_requests[req_id] = "查詢天氣"
    
    bound_llm = llm.bind(extra_body={"metadata": {"request_id": req_id, "session_id": session_id}})
    
    print(f"☀️[天氣節點] 正在查詢天氣...")
    sys_msg = SystemMessage(content="你是一位氣象專家。請模擬台灣今天天氣。")
    response = await bound_llm.ainvoke([sys_msg] + state["messages"], config=config)
    return {"messages": [response]}

# ==========================================
# 節點 2b：算數學
# ==========================================
async def math_node(state: GraphState, config: RunnableConfig):
    session_id = config.get("metadata", {}).get("session_id")
    req_id = f"Req-Math-{uuid.uuid4().hex[:6]}"
    tracked_requests[req_id] = "數學計算"

    bound_llm = llm.bind(extra_body={"metadata": {"request_id": req_id, "session_id": session_id}})
    
    print(f"🧮 [數學節點] 正在計算數學...")
    sys_msg = SystemMessage(content="你是一位數學家。請幫使用者解決數學問題。")
    response = await bound_llm.ainvoke([sys_msg] + state["messages"], config=config)
    return {"messages": [response]}

# ==========================================
# 節點 3：揉合回應
# ==========================================
async def synthesize_node(state: GraphState, config: RunnableConfig):
    session_id = config.get("metadata", {}).get("session_id")
    req_id = f"Req-Synth-{uuid.uuid4().hex[:6]}"
    tracked_requests[req_id] = "總結回應"
    
    bound_llm = llm.bind(extra_body={"metadata": {"request_id": req_id, "session_id": session_id}})
    
    print(f"✍️ [總結節點] 正在生成最終回應...")
    sys_msg = SystemMessage(content="請綜合以上對話給出回答。")
    response = await bound_llm.ainvoke([sys_msg] + state["messages"], config=config)
    return {"messages": [response]}

def route_intent(state: GraphState): return state["intent"]

workflow = StateGraph(GraphState)
workflow.add_node("intent_classifier", intent_node)
workflow.add_node("weather", weather_node)
workflow.add_node("math", math_node)
workflow.add_node("synthesize", synthesize_node)
workflow.set_entry_point("intent_classifier")
workflow.add_conditional_edges("intent_classifier", route_intent, {"weather": "weather", "math": "math"})
workflow.add_edge("weather", "synthesize")
workflow.add_edge("math", "synthesize")
workflow.add_edge("synthesize", END)
app = workflow.compile()


# ==========================================
# 🌟 自動從 Docker 抓取 LiteLLM 分配紀錄分析
# ==========================================
def analyze_docker_logs():
    print("\n" + "="*50)
    print("📊 正在從 LiteLLM 抓取機器分配日誌...")
    print("="*50)
    
    try:
        # 自動執行 docker logs 抓取紀錄 (請確保你的容器名稱是 litellm-gateway)
        # 如果你 docker-compose 裡的服務名稱不同，請修改下方的 "litellm-gateway"
        result = subprocess.run(["docker", "logs", "litellm-gateway"], capture_output=True, text=True)
        logs = result.stdout + "\n" + result.stderr
    except Exception as e:
        print(f"⚠️ 無法抓取 Docker 日誌: {e}")
        return
        
    found_count = 0
    for line in logs.split('\n'):
        if "ROUTING_LOG::" in line:
            try:
                json_str = line.split("ROUTING_LOG::")[1].strip()
                log_data = json.loads(json_str)
                req_id = log_data.get("req_id")
                
                # 如果這個 Log 剛好是我們這次推論發出的 ID
                if req_id in tracked_requests:
                    node_name = tracked_requests[req_id]
                    api_base = log_data.get("api_base", "未知 IP")
                    
                    machine = "未知伺服器"
                    if "10.129.128.71" in api_base: machine = "🖥️ L20 伺服器"
                    elif "10.110.209.16" in api_base: machine = "🖥️ RTX5090 伺服器"
                        
                    print(f"📌 {node_name: <10} | 分配至 👉 {machine} ({api_base})")
                    found_count += 1
            except:
                continue
                
    if found_count == 0:
        print("⚠️ 未找到匹配的路由紀錄，請確認 custom_logger.py 有掛載成功。")
    print("="*50 + "\n")

# --- 執行測試主程式 ---
async def run_test():
    shared_trace_id = uuid.uuid4().hex  
    shared_session_id = f"Session_id-{shared_trace_id[:8]}"
    print(f"🚀 啟動測試 | Trace ID: {shared_trace_id}")
    
    #########################
    inputs1 = {"messages":[HumanMessage(content="1000 加上 27 等於多少？")]}
    
    with langfuse_client.start_as_current_observation(as_type="span", name="langchain-call"):
        # Propagate metadata to all child observations
        with propagate_attributes(
            metadata={"foo": "bar", "baz": "qux"}
        ):
            await app.ainvoke(
            inputs1, 
            config={
                "callbacks":[langfuse_handler], 
                "metadata": {
                    "trace_id": shared_trace_id, 
                    "session_id": shared_session_id
                }
            }
        )
    
    ########################
    
    with propagate_attributes(session_id=shared_session_id):
        inputs2 = {"messages":[HumanMessage(content="15 加上 27 等於多少？")]}
        await app.ainvoke(
            inputs2, 
            config={
                "callbacks":[langfuse_handler], 
                "run_id": shared_trace_id, 
                "metadata": {
                    "trace_id": shared_trace_id, 
                    "session_id": shared_session_id
                }
            }
        )
        
    langfuse_client.flush()
    print(f"\n✅ 推論完成！等待 LiteLLM 寫入紀錄 (2秒)...")
    await asyncio.sleep(2)
    
    # 🌟 神奇魔法：自動解析剛剛發生的事情！
    # analyze_docker_logs()

if __name__ == "__main__":
    asyncio.run(run_test())