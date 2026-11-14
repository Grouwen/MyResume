from typing import TypedDict

from app.graph.state.common.statistics_state import StatisticsState


class OutputMainAgentState(TypedDict,StatisticsState):
    ai_reply:str