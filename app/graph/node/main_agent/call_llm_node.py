import logging
from typing import Dict, List
import json

from jinja2 import Template
from langchain_core.messages import AIMessage, SystemMessage, HumanMessage, BaseMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.runtime import Runtime

from app.agent.core.plug_regis import PlugRegis
from app.agent.core.skill_regis import SkillRegis
from app.core.constants import MAX_LLM_STEPS_PER_TURN
from app.core.loger import get_logger
from app.graph.context.main_agent_context import MainAgentContext
from app.graph.state.main_agent.main_agent_state import MainAgentState
from app.util.agent_util import get_active_tools
from app.util.log_util import preview, debug_body

logger = get_logger(__name__)


MAX_TURNS = 8

# 软上限收尾指令：汇报成果 + 剩余事项 + 询问是否继续
BUDGET_STOP_PROMPT = (
    "【系统】本轮工具调用预算已用完，现在不能再调用工具。"
    "请用两三句话收尾：①已经完成了什么（只写确实做完的）；②还剩什么没做；"
    "最后明确问用户是否愿意继续（用户回复「继续」即接着做，并会重新分配额度）。"
    "不要重复计划，不要反问需求细节。"
)

BUDGET_STOP_FALLBACK = "本轮工具调用次数已到上限，我先停一下。要继续吗？回复「继续」我就接着做。"

KEEP_RECENT_TOOLS = 3

SLIM_MIN_CHARS = 1000

SLIM_MARK = "[结果已省略"


def _tool_call_args(messages: list) -> dict:
    """tool_call_id -> (工具名, 参数)。取件单要能照着重放这次调用。"""
    mapping: dict = {}
    for message in messages:
        if isinstance(message, AIMessage):
            for call in message.tool_calls or []:
                mapping[call.get("id")] = (call.get("name") or "?", call.get("args") or {})
    return mapping


def _render_args(args: dict) -> str:
    text = ", ".join(f"{k}={v!r}" for k, v in args.items())
    return text if len(text) <= 100 else text[:100] + "…"


def _placeholder(name: str, args: dict, size: int) -> str:
    return (
        f"{SLIM_MARK}：{name}({_render_args(args)}) 返回 {size} 字符。"
        f"需要时用同样参数再调一次。]"
    )


def slim_tool_messages(messages: list,
                       keep_recent: int = KEEP_RECENT_TOOLS,
                       min_chars: int = SLIM_MIN_CHARS) -> tuple[list, int]:
    """返回 (瘦身后的新列表, 省下的字符数)。

    用 model_copy 生成新消息，传进来的原消息（state 里的对象）不会有任何改动；
    id / tool_call_id / name / artifact 都原样保留，只是正文换掉了。
    """
    candidates = [
        message for message in messages
        if isinstance(message, ToolMessage)
        and isinstance(message.content, str)
        and len(message.content) >= min_chars
    ]
    if not candidates:
        return messages, 0

    # 最近 keep_recent 条大结果保持原文；tool_call_id 全局唯一，用它做标记
    keep_ids = {m.tool_call_id for m in candidates[-keep_recent:]} if keep_recent else set()
    args_map = _tool_call_args(messages)

    saved = 0
    result: list = []
    for message in messages:
        if (isinstance(message, ToolMessage)
                and isinstance(message.content, str)
                and len(message.content) >= min_chars
                and message.tool_call_id not in keep_ids):
            name, args = args_map.get(message.tool_call_id, (message.name or "?", {}))
            content = _placeholder(name, args, len(message.content))
            saved += len(message.content) - len(content)
            result.append(message.model_copy(update={"content": content}))
        else:
            result.append(message)

    return result, saved

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


def _args_preview(call: dict) -> str:
    return preview(json.dumps(call.get("args") or {}, ensure_ascii=False))



def summarize_ai_msg(resp: AIMessage) -> str:
    """INFO 里只放排障要的字段，正文一律不进日志（参数只留摘要）。"""
    meta = resp.response_metadata or {}
    usage = resp.usage_metadata or {}
    calls = ", ".join(
        f"{c.get('name')}#{c.get('id')} args={_args_preview(c)}"
        for c in (resp.tool_calls or [])
    ) or "-"

    bad = resp.invalid_tool_calls or []
    bad_str = " [" + "; ".join(
        f"{b.get('name')}#{b.get('id')} err={b.get('error')} "
        f"raw={preview(str(b.get('args')))}"
        for b in bad
    ) + "]" if bad else ""

    return (
        f"model={meta.get('model_name')} finish={meta.get('finish_reason')} "
        f"in={usage.get('input_tokens')} out={usage.get('output_tokens')} "
        f"len={len(resp.content or '')} tool_calls=[{calls}]{bad_str}"
    )


async def call_llm_node(state:MainAgentState,
                        runtime:Runtime[MainAgentContext]):
    # state取值
    messages = state.get("messages",[])
    loaded_plugs = state.get("loaded_plugs",{})

    # context取值
    llm_model = runtime.context.llm_model
    tool_regis = runtime.context.tool_regis
    plug_regis = runtime.context.plug_regis
    agent_md_content = runtime.context.agent_md_content
    skill_regis = runtime.context.skill_regis
    system_prompt_template = runtime.context.system_prompt_template

    # ------ 业务逻辑 ------
    # 清洗plug中已没有，但loaded_plug仍有的plug
    all_plugs_name = {p.name for p in plug_regis.load().values()}
    loaded_plugs = {n: d for n, d in loaded_plugs.items() if n in all_plugs_name}

    # 拼接系统提示词
    system_prompt = combin_system_prompt(agent_md_content,system_prompt_template,
                                         skill_regis,plug_regis,loaded_plugs)


    # 获取可用tools，裁剪历史
    tools:List[BaseTool] = get_active_tools(tool_regis,plug_regis, loaded_plugs)
    history, slim_saved = slim_tool_messages(trim_by_turns(messages))
    if slim_saved:
        logger.info(f"历史瘦身 saved_chars={slim_saved}")

    # 调用llm
    send_messages = [
        SystemMessage(system_prompt),
        *history
    ]

    # 按轮计数：langgraph_step 是 thread 累计值，跨轮不复位，不能用来判本轮预算
    turn_llm_steps = int(state.get("turn_llm_steps") or 0) + 1
    budget_stop = turn_llm_steps > MAX_LLM_STEPS_PER_TURN

    resp:AIMessage
    if budget_stop:
        # 不绑工具：模型调不了工具，只能汇报成果 + 剩余事项 + 问是否继续
        send_messages.append(HumanMessage(BUDGET_STOP_PROMPT))
        resp = await llm_model.ainvoke(send_messages)
        if not (resp.content or "").strip():
            resp = AIMessage(BUDGET_STOP_FALLBACK)
        logger.info("本轮已用 %d 次 LLM 决策（软上限 %d），不再给工具，转为询问用户是否继续",
                    turn_llm_steps, MAX_LLM_STEPS_PER_TURN)
    else:
        llm_model_with_tool = llm_model.bind_tools(tools)
        resp = await llm_model_with_tool.ainvoke(send_messages)

    logger.info("ai回复 %s", summarize_ai_msg(resp))
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("ai回复全文：\n%s", debug_body(resp.content))
        for c in resp.tool_calls or []:
            logger.debug(
                "tool参数全文 tool=%s id=%s args=%s",
                c.get("name"), c.get("id"),
                debug_body(json.dumps(c.get("args") or {}, ensure_ascii=False, indent=2)),
            )

    return {
        "messages": [resp],
        "budget_stop": budget_stop,
        "turn_llm_steps": turn_llm_steps,
    }