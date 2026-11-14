from app.graph.state.main_agent.main_agent_state import MainAgentState


async def finalize_node(state: MainAgentState):
    messages = state["messages"]

    return {
        "ai_reply":messages[-1],
    }