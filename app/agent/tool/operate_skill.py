import asyncio
from pathlib import Path
from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool, InjectedToolCallId
from langgraph.prebuilt import ToolRuntime
from langgraph.types import Command
from pydantic import BaseModel, Field

from app.agent.core.agent_exceptions import ToolException, ToolExceptionType
from app.graph.context.main_agent_context import MainAgentContext

# 单个资源文件的读取上限，避免把超大文件一次性灌进上下文
MAX_RESOURCE_BYTES = 128_000


class OperateSkillInput(BaseModel):
    name: str = Field(description="skill的name")
    resource_path: str = Field(
        default="",
        description=(
            "可选：skill目录下的相对路径，例如 references/revision.md。"
            "为空时加载技能正文；填写时读取对应资源文件。"
        ),
    )


def _resolve_skill_resource(skill_dir: Path, resource_path: str) -> Path:
    """把 resource_path 校验并解析成 skill 目录内的真实文件路径。"""
    raw = (resource_path or "").strip().replace("\\", "/")
    if not raw:
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            "resource_path 不能为空",
            is_retryable=True,
            suggestion="不读取资源时不要传 resource_path；读取时填 references/xxx.md",
        )

    relative = Path(raw)
    if relative.is_absolute() or relative.drive or relative.root:
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            "resource_path 不允许使用绝对路径",
            is_retryable=True,
            suggestion="只填 skill 目录内的相对路径，例如 references/revision.md",
        )

    if ".." in relative.parts:
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            "resource_path 不允许包含 ..",
            is_retryable=True,
            suggestion="只填 skill 目录内的相对路径，例如 references/revision.md",
        )

    base = skill_dir.resolve()
    target = (base / relative).resolve()

    if not target.is_relative_to(base):
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            "resource_path 超出 skill 目录",
            is_retryable=True,
            suggestion="只填 skill 目录内的相对路径，例如 references/revision.md",
        )

    if not target.is_file():
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            f"资源文件不存在：{raw}",
            is_retryable=True,
            suggestion="核对文件名；可以先用不带 resource_path 的调用查看可用资源列表",
        )

    size = target.stat().st_size
    if size > MAX_RESOURCE_BYTES:
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            f"资源文件过大：{raw}（{size} 字节，上限 {MAX_RESOURCE_BYTES}）",
            is_retryable=False,
            suggestion="换一个小一点的资源文件，或先把内容拆分/摘要",
        )

    return target


async def _read_skill_resource(skill_dir: Path, resource_path: str) -> tuple[str, str]:
    """返回 (规范化后的相对路径, 文本内容)。阻塞操作丢线程池。"""
    target = await asyncio.to_thread(_resolve_skill_resource, skill_dir, resource_path)
    try:
        text = await asyncio.to_thread(target.read_text, encoding="utf-8")
    except UnicodeDecodeError as e:
        raise ToolException(
            ToolExceptionType.TOOL_RUNNING_ERROR,
            f"资源文件不是 UTF-8 文本：{resource_path}",
            is_retryable=False,
            suggestion="read_skill_resource 只支持 UTF-8 文本文件",
        ) from e
    except OSError as e:
        raise ToolException(
            ToolExceptionType.TOOL_RUNNING_ERROR,
            f"读取资源失败：{e}",
            is_retryable=True,
            suggestion="确认文件权限后重试",
        ) from e

    rel = target.relative_to(skill_dir.resolve()).as_posix()
    return rel, text


@tool(args_schema=OperateSkillInput)
async def operate_skill(name: str,
                        runtime: ToolRuntime[MainAgentContext],
                        tool_call_id: Annotated[str, InjectedToolCallId],
                        resource_path: str = "") -> Command:
    """加载技能正文；传入 resource_path 时读取技能目录下的资源文件。"""
    skills = runtime.context.skill_regis.load()

    if name not in skills:
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            f"skill: {name} 不存在",
            is_retryable=True,
            suggestion=f"技能只能从已有的里面选，当前可用：{sorted(skills)}",
        )

    skill = skills[name]

    # 不传 resource_path：保持原行为，加载 SKILL.md 正文
    if not (resource_path or "").strip():
        content = f"skill {skill.name} 已加载，正文如下：\n\n{skill.body}"

        if skill.resources:
            content += (
                "\n\n可用资源（需要时用 resource_path 读取，例如 "
                f'operate_skill(name="{skill.name}", resource_path="references/xxx.md")）：\n'
                + "\n".join(f"- {path}" for path in skill.resources)
            )

        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=content,
                        tool_call_id=tool_call_id,
                    ),
                ]
            }
        )

    # 传 resource_path：读取技能目录内的资源
    rel, text = await _read_skill_resource(skill.path, resource_path)
    content = f"skill {skill.name} 资源 {rel} 内容如下：\n\n{text or '(空文件)'}"

    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=content,
                    tool_call_id=tool_call_id,
                )
            ]
        }
    )