from typing import Literal, Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId
from langgraph.prebuilt import ToolRuntime
from langgraph.types import Command
from pydantic import BaseModel, Field
from langchain.tools import tool

from app.agent.core.agent_exceptions import ToolException, ToolExceptionType
from app.graph.context.main_agent_context import MainAgentContext


class OperatePlugInput(BaseModel):
    operation: Literal["add", "rm"] = Field(
        description="操作类型：add 表示加入，rm 表示删除"
    )
    name: str = Field(description="plug的name")


@tool(args_schema=OperatePlugInput)
def operate_plug(operation: str,name: str,
                 runtime: ToolRuntime[MainAgentContext],
                 tool_call_id: Annotated[str, InjectedToolCallId]):
    """操作 plug"""
    loaded_plugs = runtime.state.get("loaded_plugs",{})

    all_plugs = runtime.context.plug_regis.load()

    # 添加
    if operation == "add":

        if name not in all_plugs:
            raise ToolException(
                ToolExceptionType.INVALID_ARGUMENTS,
                f"plug: {name} 不存在",
                is_retryable=True,
                suggestion="核对所有plug，不能编造name"
            )
        if name in loaded_plugs:
            raise ToolException(
                ToolExceptionType.INVALID_ARGUMENTS,
                f"plug: {name} 已加载",
                is_retryable=False,
                suggestion="添加plug之前，先想是否已添加过"
            )

        plug = all_plugs[name]
        new_loaded = {**loaded_plugs, name: plug.description}
        content = f"plug {name} 已加载：{plug.description}"

    # 删除
    elif operation == "rm":
        if name not in loaded_plugs:
            raise ToolException(
                ToolExceptionType.INVALID_ARGUMENTS,
                f"plug: {name} 未加载",
                is_retryable=False,
                suggestion="删除plug之前，先想是否加载了"
            )

        new_loaded = {k: v for k, v in loaded_plugs.items() if k != name}
        content = f"plug {name} 已卸载"

    else:
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            f"不存在的operation: {operation}",
            is_retryable=True,
            suggestion="允许的操作类型只有add或rm"
        )

    return Command(
        update={
            "loaded_plugs": new_loaded,
            "messages": [ToolMessage(
                content=content,
                tool_call_id=tool_call_id,
            )]
        }
    )