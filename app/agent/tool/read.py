from pathlib import Path

import aiofiles
from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from pydantic import BaseModel, Field

from app.agent.core.agent_exceptions import ToolException, ToolExceptionType
from app.graph.context.main_agent_context import MainAgentContext
from app.util.agent_util import verify_file_path_async

PAGE_SIZE = 5000

class ReadInput(BaseModel):
    file_path: str = Field(description="文件路径")
    page: int = Field(
        default=1,
        description="页码数，基准值为1"
    )

@tool(args_schema=ReadInput)
async def read(runtime: ToolRuntime[MainAgentContext],
               file_path: str,
               page: int = 1):
    """读取文件内容。返回带全局行号的文本，按页返回。"""
    agent_work_space: Path = runtime.context.agent_work_space

    page = max(1, page)

    # 校验
    accept, reason = await verify_file_path_async(file_path, agent_work_space, True, False)
    if not accept:
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            reason,
            True,
            "file_path 只支持相对路径且必须为文件，修改file_path"
        )

    # 读取
    try:
        async with aiofiles.open(agent_work_space / file_path, "r",encoding="utf-8") as f:
            content = await f.read()
    except UnicodeDecodeError as e:
        raise ToolException(
            ToolExceptionType.TOOL_RUNNING_ERROR,
            f"不是 UTF-8 文本，（{e.reason}，字节位 {e.start}）",
            is_retryable=False,
            suggestion=f"{file_path} 是二进制文件或用了其他编码，read工具不支持打开",
        ) from e
    except OSError as e:
        raise ToolException(
            ToolExceptionType.TOOL_RUNNING_ERROR,
            f"读取失败：{e}",
            is_retryable=True,
            suggestion="确认权限；持续失败就换文件",
        ) from e

    if not content:
        return "( 空文件，此文件为空 )"

    # 分页位置：按字符定位，但回退到行首、前进到行尾，保证每页首尾都是完整行
    start = (page - 1) * PAGE_SIZE
    if start > 0:
        prev_nl = content.rfind("\n", 0, start)
        if prev_nl != -1:
            start = prev_nl + 1

    end = start + PAGE_SIZE
    if end < len(content):
        next_nl = content.find("\n", end)
        if next_nl != -1:
            end = next_nl + 1

    # 行号
    start_line_no = content.count("\n", 0, start) + 1

    total_page = (len(content) + PAGE_SIZE - 1) // PAGE_SIZE

    # 显示
    if start >= len(content):
        return f"( 空内容 )\n\n[ 第 {page}/{total_page} 页 ]"

    # 格式化输出：行号用文件里的真实行号，不是每页从 1 数
    content_with_line = content[start:end].splitlines()
    format_content = "\n".join(
        f"{start_line_no + i}| {line}" for i, line in enumerate(content_with_line)
    )

    return f"{format_content}\n\n[ 第 {page}/{total_page} 页 ]"
