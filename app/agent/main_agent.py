from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.constants import END
from langgraph.graph import StateGraph

from app.graph.node.main_agent.before_call_llm_node import before_call_llm_node
from app.graph.node.main_agent.call_llm_node import call_llm_node
from app.graph.node.main_agent.call_tool_node import call_tool_node
from app.graph.node.main_agent.entrance_node import entrance_node
from app.graph.node.main_agent.finalize_node import finalize_node
from app.graph.state.main_agent.input_state import InputMainAgentState
from app.graph.state.main_agent.main_agent_state import MainAgentState
from app.graph.state.main_agent.output_state import OutputMainAgentState


def route_after_one_act(state: MainAgentState):
    if state.get("finalize", False):
        return END
    return "before_call_llm_node"


def build_main_agent(checkpointer: BaseCheckpointSaver | None = None):
    """编译主图。checkpointer 由 lifespan 注入，未传则纯内存、不落库。"""
    agent = StateGraph(
        MainAgentState,
        input_schema=InputMainAgentState,
        output_schema=OutputMainAgentState,
    )

    agent.add_node("entrance_node", entrance_node)
    agent.add_node("before_call_llm_node", before_call_llm_node)
    agent.add_node("call_llm_node", call_llm_node)
    agent.add_node("call_tool_node", call_tool_node)
    agent.add_node("finalize_node", finalize_node)

    agent.set_entry_point("entrance_node")

    agent.add_edge("entrance_node", "before_call_llm_node")
    agent.add_edge("before_call_llm_node", "call_llm_node")
    agent.add_edge("call_llm_node", "call_tool_node")
    agent.add_edge("call_tool_node", "finalize_node")
    agent.add_conditional_edges(
        "finalize_node",
        route_after_one_act,
        {
            "before_call_llm_node": "before_call_llm_node",
            END:END
        }
    )

    return agent.compile(checkpointer=checkpointer)


# 未登录用户：不落库、不共享历史
main_agent_no_checkpointer = build_main_agent()

if __name__ == '__main__':
    mermaid_code = main_agent_no_checkpointer.get_graph().draw_mermaid()
    print(mermaid_code)