import logging
from typing import Dict, List

from jinja2 import Template
from langchain_core.messages import AIMessage, SystemMessage, HumanMessage, BaseMessage
from langchain_core.tools import BaseTool
from langgraph.runtime import Runtime

from app.agent.core.plug_regis import PlugRegis
from app.agent.core.skill_regis import SkillRegis
from app.core.loger import get_logger
from app.graph.context.main_agent_context import MainAgentContext
from app.graph.state.main_agent.main_agent_state import MainAgentState
from app.util.agent_util import get_active_tools
from app.util.log_util import debug_body

logger = get_logger(__name__)


MAX_TURNS = 8

def combin_system_prompt(agent_md_content:str,system_prompt_template:str,
                         skill_regis: SkillRegis,plug_regis: PlugRegis,
                         loaded_plugs: Dict[str,str]):
    """
    组装system_prompt，agent.md+system_prompt
    """

    system_prompt = Template(system_prompt_template).render(
        skills=list(skill_regis.load().values()),
        plugs=list(plug_regis.load().values()),
        loaded_plugs=loaded_plugs
    )

    return agent_md_content+"\n"+system_prompt

def trim_by_turns(messages: list, max_turns: int = MAX_TURNS) -> list:
    """只在轮边界裁剪，保证不会把 AIMessage(tool_calls) 和 ToolMessage 切开"""
    human_idx = [i for i, m in enumerate(messages) if isinstance(m, HumanMessage)]
    if len(human_idx) <= max_turns:
        return messages
    return messages[human_idx[-max_turns]:]

def summarize_ai_msg(resp: AIMessage) -> str:
    """INFO 里只放排障要的字段，正文一律不进日志。"""
    meta = resp.response_metadata or {}
    usage = resp.usage_metadata or {}
    calls = ", ".join(
        f"{c.get('name')}#{c.get('id')}" for c in (resp.tool_calls or [])
    ) or "-"
    return (
        f"model={meta.get('model_name')} finish={meta.get('finish_reason')} "
        f"in={usage.get('input_tokens')} out={usage.get('output_tokens')} "
        f"len={len(resp.content or '')} tool_calls=[{calls}]"
    )


async def call_llm_node(state:MainAgentState,
                        runtime:Runtime[MainAgentContext]):
    # state取值
    user_input = state["user_input"]
    messages = state.get("messages",[])
    loaded_plugs = state["loaded_plugs"]

    # context取值
    llm_model = runtime.context.llm_model
    tool_regis = runtime.context.tool_regis
    plug_regis = runtime.context.plug_regis
    agent_md_content = runtime.context.agent_md_content
    skill_regis = runtime.context.skill_regis
    system_prompt_template = runtime.context.system_prompt_template

    # ------ 业务逻辑 ------
    # 拼接系统提示词
    system_prompt = combin_system_prompt(agent_md_content,system_prompt_template,
                                         skill_regis,plug_regis,loaded_plugs)


    # 获取可用tools，裁剪历史
    tools:List[BaseTool] = get_active_tools(tool_regis,plug_regis, loaded_plugs)
    history = trim_by_turns(messages)

    # 调用llm
    llm_model_with_tool = llm_model.bind_tools(tools)
    send_messages = [
        SystemMessage(system_prompt),
        *history
    ]

    resp:AIMessage = await llm_model_with_tool.ainvoke(send_messages)

    logger.info("ai回复 %s", summarize_ai_msg(resp))
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("ai回复全文：\n%s", debug_body(resp.content))

    return {
        "messages": [resp]
    }