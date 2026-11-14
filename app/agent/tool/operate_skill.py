from typing import Annotated

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import tool, InjectedToolCallId
from langgraph.prebuilt import ToolRuntime
from langgraph.types import Command
from pydantic import BaseModel, Field

from app.agent.core.agent_exceptions import ToolException, ToolExceptionType
from app.graph.context.main_agent_context import MainAgentContext


class OperateSkillInput(BaseModel):
    name: str = Field(description="skill的name")


@tool(args_schema=OperateSkillInput)
def operate_skill(name: str,
                  runtime: ToolRuntime[MainAgentContext],
                  tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
    """加载技能，把技能正文交给模型"""
    skills = runtime.context.skill_regis.load()

    if name not in skills:
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            f"skill: {name} 不存在",
            is_retryable=True,
            suggestion=f"技能只能从已有的里面选，当前可用：{sorted(skills)}",
        )

    skill = skills[name]

    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=f"skill {skill.name} 已加载，正文如下：\n\n{skill.body}",
                    tool_call_id=tool_call_id,
                ),
            ]
        }
    )