from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from app.graph.context.main_agent_context import MainAgentContext
from app.graph.state.main_agent.main_agent_state import MainAgentState


async def entrance_node(state: MainAgentState, runtime: Runtime[MainAgentContext]):
    init_state = {
        "messages": [HumanMessage(state["user_input"])],
        "finalize": False
    }

    return init_state