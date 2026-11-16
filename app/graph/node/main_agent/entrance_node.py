from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from app.graph.context.main_agent_context import MainAgentContext
from app.graph.state.main_agent.main_agent_state import MainAgentState


async def entrance_node(state: MainAgentState, runtime: Runtime[MainAgentContext]):
    init_state = {
        "messages": [HumanMessage(state["user_input"])],
        "finalize": False,
        # 必须每轮重置：state 跨轮持久化，上一轮置 True 不重置会一直停在"询问"状态
        "budget_stop": False,
        # 预算按轮计：这里清零，用户回「继续」才拿得到全新额度
        "turn_llm_steps": 0,
    }

    return init_state