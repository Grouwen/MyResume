from langgraph.runtime import Runtime

from app.graph.context.main_agent_context import MainAgentContext
from app.graph.state.main_agent.main_agent_state import MainAgentState


async def before_call_llm_node(state:MainAgentState,
                               runtime:Runtime[MainAgentContext]):
    loaded_plugs = state.get("loaded_plugs",{})
    plug_regis = runtime.context.plug_regis

    # 清洗plug中已没有，但loaded_plug仍有的plug
    all_plugs_name = {p.name for p in plug_regis.load().values()}
    loaded_plugs = {n: d for n, d in loaded_plugs.items() if n in all_plugs_name}

    return {
        "loaded_plugs":loaded_plugs,
    }