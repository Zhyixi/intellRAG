import os
import sys
sys.path.extend(['.', '..'])
from  .core.llms import ChatMessage
from  .core.tools import ToolSelection, ToolOutput
from  .core.workflow import Event, Workflow, StartEvent, StopEvent, step
from  .core.tools import FunctionTool
from  .core.llms.function_calling import FunctionCallingLLM
from  .core.memory import ChatMemoryBuffer
from  .core.tools.types import BaseTool
from typing import Any, List
from LLM.llm import chat_model
# db數據
from DataBase.database import get_db
import datetime
import pandas as pd
from common.utils import make_arrow_compatible
from LLM.PandasEngine.pandas_instr_engine import initialize_df_engine, process_query
from fast_api_service.power_arena.crud import get_alert_record
import logging
    

class InputEvent(Event):
    input: list[ChatMessage]

class ToolCallEvent(Event):
    tool_calls: list[ToolSelection]

class FunctionOutputEvent(Event):
    output: ToolOutput

class FuncationCallingAgent(Workflow):
    def __init__(
        self,
        *args: Any,
        llm: FunctionCallingLLM | None = None,
        tools: List[BaseTool] | None = None,
        base_data: pd.DataFrame | None = pd.DataFrame(),
        **kwargs: Any,) -> None:
        super().__init__(*args, **kwargs)
        self.tools = tools or []
        self.llm = llm 
        assert self.llm.metadata.is_function_calling_model
        self.base_data = base_data
        self.memory = ChatMemoryBuffer.from_defaults(llm=llm)
        self.memory.put(ChatMessage(role="user", content=f"請遵循以下幾點原則:"))
        self.memory.put(ChatMessage(role="user", content=f"1. 若歷史對話中有回答過則回覆相同答案"))
        self.memory.put(ChatMessage(role="user", content=f"2. 須判斷是否是與數據相關的問題,部份數據如下:\n{self.base_data.head()}"))
        self.sources = []

    @step
    async def prepare_chat_history(self, ev: StartEvent) -> InputEvent:
        # clear sources
        self.sources = []
        # get user input
        user_input = ev.input
        user_msg = ChatMessage(role="user", content=user_input)
        self.memory.put(user_msg)
        # get chat history
        chat_history = self.memory.get()
        logging.info("Chat history:")
        for chat in chat_history:
            logging.info(chat)
        return InputEvent(input=chat_history)

    @step
    async def handle_llm_input(self, ev: InputEvent) -> ToolCallEvent | StopEvent:
        # Predict and call the tool.
        response = await self.llm.achat_with_tools(self.tools, chat_history=ev.input, verbose=True)
        logging.info(f"模型回應:\n {response.message}\n\n")
        self.memory.put(response.message)
        tool_calls = self.llm.get_tool_calls_from_response(response, error_on_no_tool_call=False)
        logging.info(f"模型判斷執行函數:")
        for tool_call in tool_calls:
            logging.info(f"{tool_call.tool_name}")
        # 依據事情做完沒有來決定要不要進入Stop Event
        if not tool_calls:
            return StopEvent(result={"response": response, "sources": self.sources})
        else:
            return ToolCallEvent(tool_calls=tool_calls)

    @step
    async def handle_tool_calls(self, ev: ToolCallEvent) -> InputEvent:
        tool_calls = ev.tool_calls
        tools_by_name = {tool.metadata.get_name(): tool for tool in self.tools}
        tool_msgs = []
        # call tools -- safely!
        for tool_call in tool_calls:
            logging.info(f"執行工具:{tool_call.tool_name}")
            tool = tools_by_name.get(tool_call.tool_name)
            additional_kwargs = {
                "tool_call_id": tool_call.tool_id,
                "name": tool.metadata.get_name()}
            if not tool:
                tool_msgs.append(ChatMessage(role="tool", content=f"Tool {tool_call.tool_name} does not exist", additional_kwargs=additional_kwargs))
                continue
            try:
                if "base_data" in tool.metadata.description and "instruct_with_dataframe" not in tool.metadata.description:
                    tool_output = tool(prompt=tool_call.tool_kwargs['prompt'], base_data=self.base_data)
                elif "instruct_with_dataframe" in tool.metadata.description:
                    try:
                        # 檢查llm有沒有抓到pandas語法
                        pandas_instr = tool_call.tool_kwargs['pandas_instr']
                    except Exception as e:
                        raise AssertionError("pandas_instr is empty ")
                    tool_output = tool(prompt=tool_call.tool_kwargs['prompt'], base_data=self.base_data, pandas_instr=pandas_instr)
                else:
                    tool_output = tool(**tool_call.tool_kwargs)
                pass
                tool_msgs.append(ChatMessage(
                    role="tool",
                    content=tool_output.content,
                    additional_kwargs=additional_kwargs,))
                
                self.sources.append(tool_output)
            except Exception as e:
                tool_msgs.append(
                    ChatMessage(
                        role="tool",
                        content=f"Encountered error in tool call: {e}",
                        additional_kwargs=additional_kwargs,
                    )
                )
        for msg in tool_msgs:
            self.memory.put(msg)
        chat_history = self.memory.get()
        return InputEvent(input=chat_history)

    
def add(x: int, y: int) -> int:
    """用於兩個數字相加的函數。"""
    return x + y

def multiply(x: int, y: int) -> int:
    """用於兩個數字相乘的函數。"""
    return x * y

def substract(x: int, y: int) -> int:
    """用於兩個數字相減的函數。"""
    return x - y

def get_power_arena_source_data(prompt:str) -> dict:
    """用於取得power_arena的數據, 以便於後須分析語回答問題"""
    db = next(get_db())
    start_time = datetime.datetime(2024, 4, 1, 0, 0, 0)
    end_time = datetime.datetime(2024, 6, 1, 0, 0, 0)
    base_data = get_alert_record(db, start_time, end_time)
    return base_data


def analysis_dataframe(prompt:str, base_data:dict) -> str:
    """當問到關於數據時,用於輸入數據並回答問題的函數。"""
    base_data = pd.DataFrame(base_data)
    base_data = make_arrow_compatible(base_data)
    query_engine = initialize_df_engine(df=base_data)
    response, result, pandas_instr = process_query(query_engine, prompt, df=base_data)
    res = {"response":response, "result":result, "pandas_instr":pandas_instr}
    return response

tools = [
    FunctionTool.from_defaults(add),
    FunctionTool.from_defaults(multiply),
    FunctionTool.from_defaults(substract),
    FunctionTool.from_defaults(get_power_arena_source_data),
    FunctionTool.from_defaults(analysis_dataframe)
]



async def main():
    # OpenAI(model="gpt-4o-mini")
    agent = FuncationCallingAgent(llm=chat_model, tools=tools, timeout=9999999, verbose=True)
    ret = await agent.run(input="請問 (100+400)*2=?")
    print(ret["response"])
    pass


if __name__ == "__main__":
    import asyncio
    asyncio.run(main(), debug=True)