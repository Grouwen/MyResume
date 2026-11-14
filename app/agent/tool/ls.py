"""列出目录：可按后缀过滤文件，目录始终保留，方便模型继续往下走。"""

import asyncio
import os
from pathlib import Path

from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from pydantic import BaseModel, Field

from app.agent.core.agent_exceptions import ToolException, ToolExceptionType
from app.graph.context.main_agent_context import MainAgentContext
from app.util.agent_util import verify_file_path_async

# 单次最多列这么多，防止大目录一次性灌进上下文
MAX_LIMIT = 2000
DEFAULT_LIMIT = 500


def _parse_suffixes(raw: str) -> tuple[str, ...]:
    """把 "py" / ".py" / "*.py" / "py,txt" / "tar.gz" 统一成 (".py", ".txt")。

    返回空元组表示不过滤（列出所有文件）。
    """
    suffixes: list[str] = []
    for part in raw.replace(";", ",").replace("；", ",").replace("，", ",").split(","):
        # 兼容模型爱写的 glob 和路径前缀：**/*.py、*.py
        item = part.strip().lower().replace("\\", "/").rsplit("/", 1)[-1].lstrip("*")
        if not item or item == ".":
            continue
        if not item.startswith("."):
            item = "." + item
        if item not in suffixes:
            suffixes.append(item)
    return tuple(suffixes)


def _match_suffix(name: str, suffixes: tuple[str, ...]) -> bool:
    """按文件名结尾匹配后缀。

    故意不用 Path.suffix：它只认最后一段（a.tar.gz -> .gz），点文件还会被当成没有后缀
    （.env -> ""），导致 suffix=env、suffix=tar.gz 静默匹配不到任何文件。
    """
    if not suffixes:
        return True
    low = name.lower()
    return any(low.endswith(sfx) for sfx in suffixes)


def _scan_dir(full_path: Path) -> list[tuple[bool, str]]:
    """一次 scandir 拿到 (是否目录, 名字)，省掉每个条目再 is_dir() 打一次 stat。"""
    entries: list[tuple[bool, str]] = []
    with os.scandir(full_path) as it:
        for entry in it:
            try:
                is_dir = entry.is_dir()
            except OSError:
                # 断链符号链接之类，当文件处理，列出来就够了
                is_dir = False
            entries.append((is_dir, entry.name))
    return entries


def _check_limit(limit: int) -> int:
    """limit 越界直接报错，不静默改写：0 被偷偷换成 1，模型会以为拿到的是自己要的结果。"""
    limit = int(limit)
    if not 1 <= limit <= MAX_LIMIT:
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            f"limit 必须在 1–{MAX_LIMIT} 之间，当前为 {limit}",
            is_retryable=True,
            suggestion=f"把 limit 改成 1 到 {MAX_LIMIT} 之间的整数，不知道填什么就用 {DEFAULT_LIMIT}",
        )
    return limit


class LsInput(BaseModel):
    dir_path: str = Field(default="", description="目录路径。为空时，列出当前目录。")
    suffix: str = Field(
        default="",
        description=(
            "只列出指定后缀的文件，例如 .py 或 py；多个用逗号分隔，如 py,txt。"
            "支持 tar.gz 这种多段后缀，也支持 .env 这种点文件。"
            "为空时列出所有文件。目录始终会列出。"
        ),
    )
    limit: int = Field(
        default=DEFAULT_LIMIT,
        description=(
            f"最多列出多少项（目录和文件合计），默认 {DEFAULT_LIMIT}，"
            f"取值 1–{MAX_LIMIT}，越界会报参数错误。"
        ),
    )


@tool(args_schema=LsInput)
async def ls(
        runtime: ToolRuntime[MainAgentContext],
        dir_path: str = "",
        suffix: str = "",
        limit: int = DEFAULT_LIMIT,
) -> str:
    """列出指定目录下的文件与目录。可用 suffix 只看某类后缀的文件。"""
    agent_work_space: Path = runtime.context.agent_work_space

    # 赋初始值
    dir_path = dir_path.strip() or "."

    # 兼容
    dir_path = dir_path.lstrip("/\\") or "."

    # 参数校验先做，出错时不用碰磁盘
    suffixes = _parse_suffixes(suffix)
    limit = _check_limit(limit)

    # 校验 + 扫描都在线程池里跑：resolve/exists/scandir 都是阻塞系统调用，
    # 直接在协程里 await 会把事件循环卡住（大目录、网络盘上尤其明显）
    accept, reason = await verify_file_path_async(dir_path, agent_work_space, True, True)
    if not accept:
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            reason,
            True,
            "修改 dir_path，dir_path 只支持相对路径且必须为目录"
        )

    try:
        entries = await asyncio.to_thread(_scan_dir, agent_work_space / dir_path)
    except OSError as e:
        raise ToolException(
            ToolExceptionType.TOOL_RUNNING_ERROR,
            f"读取目录失败：{e}",
            is_retryable=True,
            suggestion="确认目录权限；持续失败就换目录",
        ) from e

    if not entries:
        return "[ 目录为空 ]"

    total_dirs = sum(1 for is_dir, _ in entries if is_dir)
    total_files = len(entries) - total_dirs

    # 后缀只过滤文件；目录保留，否则模型没法继续往下走
    matched = (
        [(is_dir, name) for is_dir, name in entries if is_dir or _match_suffix(name, suffixes)]
        if suffixes else entries
    )
    matched.sort(key=lambda e: (not e[0], e[1].lower()))

    matched_dirs = sum(1 for is_dir, _ in matched if is_dir)
    matched_files = len(matched) - matched_dirs

    # 过滤条件一个文件都没命中时必须明说，否则模型会以为"过滤生效了，文件就这些"
    notes: list[str] = []
    if suffixes and matched_files == 0:
        suffix_text = ', '.join(suffixes)
        if total_files and total_dirs:
            notes.append(
                f"[ 注意：目录里共 {total_files} 个文件，没有一个后缀匹配 {suffix_text}，"
                f"上面只有目录 ]"
            )
        elif total_files:
            notes.append(f"[ 目录里共 {total_files} 个文件，没有一个后缀匹配 {suffix_text} ]")
        else:
            notes.append(f"[ 注意：目录里没有文件，只有 {total_dirs} 个子目录 ]")

    if not matched:
        return "\n".join(notes) if notes else "[ 目录为空 ]"

    shown = matched[:limit]
    shown_dirs = sum(1 for is_dir, _ in shown if is_dir)
    shown_files = len(shown) - shown_dirs
    result = "\n".join(f"{name}/" if is_dir else name for is_dir, name in shown)

    if len(matched) > limit:
        scope = f"，已按后缀 {', '.join(suffixes)} 过滤" if suffixes else ""
        # 目录排在前面，文件可能整段被 limit 挤掉，得给个能马上照做的说法
        if matched_files and shown_files == 0:
            hint = "放大 limit 或缩小 dir_path" + ("" if suffixes else "、加 suffix 过滤")
            hint += "（文件都排在截断之后）"
        else:
            hint = "放大 limit 或缩小 dir_path" + ("、加 suffix 过滤" if not suffixes else "")
        result += (
            f"\n···\n[ 共 {len(matched)} 项（目录 {matched_dirs}，文件 {matched_files}）{scope}，"
            f"仅显示前 {len(shown)} 项（目录 {shown_dirs}，文件 {shown_files}）；{hint} ]"
        )

    if notes:
        result += "\n" + "\n".join(notes)

    return result