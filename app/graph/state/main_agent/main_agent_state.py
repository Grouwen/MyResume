from typing import TypedDict, Dict, Annotated, List

from langchain_core.messages import BaseMessage
from langgraph.graph import add_messages

from app.graph.state.common.statistics_state import StatisticsState


class MainAgentState(TypedDict,StatisticsState):
    user_input:str # 用户输入
    messages: Annotated[List[BaseMessage], add_messages] # 历史记录

    loaded_plugs: Dict[str, str] # 已加载的plugs name: description

    finalize: bool # 是否结束