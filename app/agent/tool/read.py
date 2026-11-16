"""读取文件：按行号窗口（offset/limit）返回带全局行号的文本。"""

from pathlib import Path

import aiofiles
from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from pydantic import BaseModel, Field

from app.agent.core.agent_exceptions import ToolException, ToolExceptionType
from app.graph.context.main_agent_context import MainAgentContext
from app.util.agent_util import verify_file_path_async

# 一次最多带这么多行，超了不会报错，只按上限读
MAX_LIMIT = 2000
DEFAULT_LIMIT = 200

# 单次返回的字符上限：防止一页塞进几百 KB 把上下文撑爆（超长单行、压缩过的代码）
MAX_PAGE_CHARS = 20000

class ReadInput(BaseModel):
    file_path: str = Field(description="文件路径")
    offset: int = Field(
        default=1,
        description=(
            "起始行号，从 1 开始。默认从头读；"
            "继续读后面内容时，填上次返回里「用 offset=N 继续读」给的 N。"
        ),
    )
    limit: int = Field(
        default=DEFAULT_LIMIT,
        description=(
            f"最多读取多少行，默认 {DEFAULT_LIMIT}，上限 {MAX_LIMIT}。"
            f"超过上限不会报错，按 {MAX_LIMIT} 行读并在返回末尾说明；"
            f"单次返回还受 {MAX_PAGE_CHARS} 字符上限约束。"
        ),
    )

def _check_offset(offset: int) -> int:
    """offset 是行号，必须从 1 起；0 当成从头读会让模型以为读的是别的行。"""
    offset = int(offset)
    if offset < 1:
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            f"offset 是起始行号，必须从 1 开始，当前为 {offset}",
            is_retryable=True,
            suggestion="把 offset 改成 1 从头读，或填上次返回的「用 offset=N 继续读」里的 N",
        )
    return offset

def _clamp_limit(limit: int) -> tuple[int, str | None]:
    """limit 越界不报错，直接夹到 1–MAX_LIMIT，返回 (实际用的值, 需要说明的提示)。

    夹过就得在返回里说一句：模型要 5000 行只拿到 2000 行，
    不说清楚它会以为文件只有 2000 行，或者以为自己已经读完了。
    """
    limit = int(limit)
    if limit > MAX_LIMIT:
        return MAX_LIMIT, f"limit 超过上限，已按 {MAX_LIMIT} 行读"
    if limit < 1:
        return 1, "limit 小于 1，已按 1 行读"
    return limit, None

def _split_lines(content: str) -> list[str]:
    """按 \\n 切行，去掉行尾的 \\r（CRLF 文件不会多出空白行）。

    故意不用 str.splitlines：它还会在 \\x0b、\\x0c、\\u2028 这些字符处断行，
    行号就和编辑器里的对不上了。
    """
    raw = content.split("\n")
    # 文件以换行结尾时，split 会多出一个空串，那不是真正的一行
    if raw and raw[-1] == "":
        raw.pop()
    return [line[:-1] if line.endswith("\r") else line for line in raw]

def _pick_range(lines: list[str], offset: int, limit: int) -> tuple[int, int, bool]:
    """算出要展示的下标区间 [start, end)，返回 (start, end, 是否被字符上限截断)。

    截断只切在行边界上，保证不会把一行从中间劈开，模型续读时行号仍然对得上。
    """
    start = offset - 1
    if start >= len(lines):
        return start, start, False

    end = min(start + limit, len(lines))
    used = 0
    for index in range(start, end):
        used += len(lines[index]) + 1  # +1 是换行符
        # 第一行永远保留：否则单行超限的文件会一行都读不出来
        if used > MAX_PAGE_CHARS and index > start:
            return start, index, True
    return start, end, False

@tool(args_schema=ReadInput)
async def read(runtime: ToolRuntime[MainAgentContext],
               file_path: str,
               offset: int = 1,
               limit: int = DEFAULT_LIMIT):
    """读取文件内容。返回带全局行号的文本，可按 offset/limit 读指定行区间。"""
    agent_work_space: Path = runtime.context.agent_work_space

    # 参数校验先做，出错时不用碰磁盘
    offset = _check_offset(offset)
    limit, limit_note = _clamp_limit(limit)

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

    lines = _split_lines(content)
    total_lines = len(lines)

    start, end, truncated = _pick_range(lines, offset, limit)

    # offset 越界不是错误：模型读到末尾后再要下一段很常见，说清总数让它自己回头
    if start >= total_lines:
        return (
            f"( 已到文件末尾，没有更多内容 )\n\n"
            f"[ 文件共 {total_lines} 行，offset={offset} 已越过最后一行"
            f"（可填 1–{total_lines}）；要重头看就用 offset=1 ]"
        )

    start_line_no = start + 1
    end_line_no = end

    # 格式化输出：行号用文件里的真实行号，不是每段从 1 数
    format_content = "\n".join(
        f"{start_line_no + i}| {line}" for i, line in enumerate(lines[start:end])
    )

    footer = f"[ 第 {start_line_no}–{end_line_no} 行 / 共 {total_lines} 行 ]"

    remaining = total_lines - end_line_no
    if remaining > 0:
        # 说清为什么只给了这些，模型才知道该放大 limit 还是可以继续往下翻
        if truncated:
            why = f"本次已到 {MAX_PAGE_CHARS} 字符上限"
        elif limit_note:
            why = limit_note
        else:
            why = f"本次只读了 {end - start} 行"
        footer += (
            f"\n[ 还有 {remaining} 行未读到（{why}），"
            f"用 offset={end_line_no + 1} 继续读 ]"
        )

    return f"{format_content}\n\n{footer}"