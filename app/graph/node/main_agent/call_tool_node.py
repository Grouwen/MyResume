from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime
from langgraph.types import Command

from app.util.log_util import preview
from app.agent.core.agent_exceptions import ToolException
from app.core.loger import get_logger
from app.graph.context.main_agent_context import MainAgentContext
from app.graph.state.main_agent.main_agent_state import MainAgentState
from app.util.agent_util import get_active_tools

logger = get_logger(__name__)

MESSAGES_KEY = "messages"


def handle_tool_error(e: Exception) -> str:
    if isinstance(e, ToolException):
        return f"{e.error_message}\n建议：{e.suggestion}"
    return f"工具执行失败：{type(e).__name__}: {e}"


def _iter_tool_messages(result) -> list:
    """从 ToolNode 的两种返回形态里取出 ToolMessage，只用于日志。"""
    if isinstance(result, dict):
        return list(result.get(MESSAGES_KEY, []))

    msgs = []
    for item in result:
        part = (item.update or {}) if isinstance(item, Command) else item
        msgs.extend(part.get(MESSAGES_KEY, []))
    return msgs


def _merge_commands(result) -> Command:
    update: dict = {}
    for item in result:
        part = (item.update or {}) if isinstance(item, Command) else item
        for k, v in part.items():
            if k == MESSAGES_KEY:
                update[k] = list(update.get(k) or []) + list(v)
            elif k == "loaded_plugs":
                update[k] = {**(update.get(k) or {}), **(v or {})}
            else:
                update[k] = v
    return Command(update=update)


async def call_tool_node(state: MainAgentState,
                         runtime: Runtime[MainAgentContext],
                         config: RunnableConfig):
    loaded_plugs = state.get("loaded_plugs",{})

    tool_regis = runtime.context.tool_regis
    plug_regis = runtime.context.plug_regis

    # 模型这轮没调工具 → 直接收尾
    last_ai = next(
        (m for m in reversed(state["messages"]) if isinstance(m, AIMessage)),
        None,
    )
    if last_ai is None or not last_ai.tool_calls:
        return {"finalize": True}

    # 工具集随 loaded_plugs 变，只能运行时拼，编译期固定不了
    tools = get_active_tools(tool_regis,plug_regis,loaded_plugs)
    node = ToolNode(tools, handle_tool_errors=handle_tool_error)
    result = await node.ainvoke(state, config)

    for msg in _iter_tool_messages(result):
        logger.info(
            "tool结果 tool=%s len=%d preview=%s",
            getattr(msg, "name", "?"),
            len(str(msg.content)),
            preview(msg.content),
        )

    # 普通工具：dict，直接写回
    if isinstance(result, dict):
        return result

    # 有 Command：list；带跳转的 Command 不能合并（本项目的工具都不带，留个保险）
    if any(isinstance(item, Command) and (item.goto or item.graph or item.resume)
           for item in result):
        return result

    return _merge_commands(result)