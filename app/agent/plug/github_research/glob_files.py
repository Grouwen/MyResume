"""按 glob 模式递归查找工作区内的文件/目录，返回相对路径。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from langchain.tools import tool
from langgraph.prebuilt import ToolRuntime
from pydantic import BaseModel, Field

from app.agent.core.agent_exceptions import ToolException, ToolExceptionType
from app.graph.context.main_agent_context import MainAgentContext
from app.util.agent_util import verify_file_path_async

from ._common import (
    MAX_SCAN_ENTRIES,
    check_int,
    iter_entries,
    match_glob,
    relative_posix,
    split_patterns,
)


class GlobFilesInput(BaseModel):
    pattern: str = Field(
        description=(
            "文件匹配模式，例如 **/*.py、**/package.json、src/**/*.ts；"
            "多个用逗号分隔"
        )
    )
    path: str = Field(
        default=".",
        description="在哪个目录下查找，默认工作区根目录",
    )
    include_dirs: bool = Field(
        default=False,
        description="是否同时返回匹配的目录，默认 false",
    )
    max_results: int = Field(
        default=500,
        description="最多返回多少个路径，1-2000，默认 500",
    )


def _glob_sync(
    root: Path,
    base: Path,
    patterns: tuple[str, ...],
    include_dirs: bool,
    max_results: int,
) -> tuple[list[str], int, bool, bool]:
    results: list[str] = []
    scanned = 0
    truncated = False
    scan_limit_hit = False

    for file_path, is_dir in iter_entries(root, include_dirs=include_dirs):
        scanned += 1
        if scanned > MAX_SCAN_ENTRIES:
            scan_limit_hit = True
            break

        rel = relative_posix(file_path, base)

        if not match_glob(rel, patterns):
            continue

        results.append(rel + ("/" if is_dir else ""))

        if len(results) >= max_results:
            truncated = True
            break

    results.sort()
    return results, scanned, truncated, scan_limit_hit


@tool(args_schema=GlobFilesInput)
async def glob_files(
    pattern: str,
    runtime: ToolRuntime[MainAgentContext],
    path: str = ".",
    include_dirs: bool = False,
    max_results: int = 500,
) -> str:
    """按 glob 模式递归查找文件，返回工作区内的相对路径。

    适合找 README、依赖清单、CI 配置、测试目录等；自动跳过 .git、node_modules、
    .venv 等依赖/构建目录，不会跟随符号链接。
    """
    try:
        patterns = split_patterns(pattern)
    except ValueError as e:
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            str(e),
            is_retryable=False,
            suggestion="pattern 传相对模式，例如 **/*.py；多个用逗号分隔",
        ) from e

    if not patterns:
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            "pattern 不能为空",
            is_retryable=False,
            suggestion="传一个 glob 模式，例如 **/*.py 或 **/package.json",
        )

    max_results = check_int(max_results, "max_results", 1, 2000)

    base = runtime.context.agent_work_space.resolve()
    rel_path = (path or "").strip() or "."

    accept, reason = await verify_file_path_async(
        rel_path,
        base,
        must_exist=True,
        is_dir=True,
    )
    if not accept:
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            reason,
            is_retryable=False,
            suggestion=(
                "path 只支持工作区内的目录；找单个文件内容用 read 或 grep_code"
            ),
        )

    root = (base / rel_path).resolve()

    results, scanned, truncated, scan_limit_hit = await asyncio.to_thread(
        _glob_sync,
        root,
        base,
        patterns,
        include_dirs,
        max_results,
    )

    scope = rel_path
    noun = "文件/目录" if include_dirs else "文件"

    if not results:
        lines = [
            f"没有找到匹配 {', '.join(patterns)} 的{noun}。",
            f"搜索目录：{scope}",
            f"扫描条目：{scanned}",
        ]
        if scan_limit_hit:
            lines.append(f"扫描条目数达到 {MAX_SCAN_ENTRIES}，结果可能不完整。")
        return "\n".join(lines)

    lines = [f"匹配 {', '.join(patterns)}，共 {len(results)} 项："]
    lines.extend(results)

    if truncated:
        lines.append(
            f"... 已达到 max_results={max_results}，结果可能被截断。"
        )
    if scan_limit_hit:
        lines.append(
            f"... 扫描条目数达到 {MAX_SCAN_ENTRIES}，结果可能不完整。"
        )

    return "\n".join(lines)