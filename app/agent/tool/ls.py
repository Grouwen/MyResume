"""列出目录：可按后缀过滤文件，目录始终保留，方便模型继续往下走。"""

from pathlib import Path

from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from pydantic import BaseModel, Field

from app.agent.core.agent_exceptions import ToolException, ToolExceptionType
from app.graph.context.main_agent_context import MainAgentContext
from app.util.agent_util import verify_file_path

# 单次最多列这么多，防止大目录一次性灌进上下文
MAX_LIMIT = 2000


def _parse_suffixes(raw: str) -> tuple[str, ...]:
    """把 "py" / ".py" / "*.py" / "py,txt" 统一成 (".py", ".txt")。

    返回空元组表示不过滤（列出所有文件）。
    """
    suffixes: list[str] = []
    for part in raw.replace(";", ",").replace("；", ",").replace("，", ",").split(","):
        item = part.strip().lower().lstrip("*")
        if not item or item == ".":
            continue
        if not item.startswith("."):
            item = "." + item
        if item not in suffixes:
            suffixes.append(item)
    return tuple(suffixes)


class LsInput(BaseModel):
    dir_path: str = Field(default="", description="目录路径。为空时，列出当前目录。")
    suffix: str = Field(
        default="",
        description="只列出指定后缀的文件，例如 .py 或 py；多个用逗号分隔，如 py,txt。为空时列出所有文件。目录始终会列出。",
    )
    limit: int = Field(default=500, description="最多列出多少项，默认 500，上限 2000。")


@tool(args_schema=LsInput)
async def ls(
        runtime: ToolRuntime[MainAgentContext],
        dir_path: str = "",
        suffix: str = "",
        limit: int = 500,
):
    """列出指定目录下的文件与目录。可用 suffix 只看某类后缀的文件。"""
    agent_work_space: Path = runtime.context.agent_work_space

    # 赋初始值
    dir_path = dir_path.strip() or "."

    # 兼容
    dir_path = dir_path.lstrip("/\\") or "."

    # 校验合法性
    accept, reason = verify_file_path(dir_path, agent_work_space, True, True)
    if not accept:
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            reason,
            True,
            "修改 dir_path，dir_path 只支持相对路径且必须为目录"
        )

    suffixes = _parse_suffixes(suffix)
    limit = max(1, min(int(limit), MAX_LIMIT))

    # 列出文件与目录
    full_path = agent_work_space / dir_path
    try:
        items = list(full_path.iterdir())
    except OSError as e:
        raise ToolException(
            ToolExceptionType.TOOL_RUNNING_ERROR,
            f"读取目录失败：{e}",
            is_retryable=True,
            suggestion="确认目录权限；持续失败就换目录",
        ) from e

    # 后缀只过滤文件；目录保留，否则模型没法继续往下走
    if suffixes:
        items = [p for p in items if p.is_dir() or p.suffix.lower() in suffixes]

    items.sort(key=lambda p: (not p.is_dir(), p.name.lower()))
    names = [f"{p.name}/" if p.is_dir() else p.name for p in items]

    if not names:
        if suffixes:
            return f"[ 目录里没有后缀为 {', '.join(suffixes)} 的文件 ]"
        return "[ 目录为空 ]"

    truncated = len(names) > limit
    result = "\n".join(names[:limit])
    if truncated:
        filtered = f"，已按后缀 {', '.join(suffixes)} 过滤" if suffixes else ""
        result += f"\n···\n[ 共 {len(names)} 项{filtered}，已达 limit={limit} ]"

    return result