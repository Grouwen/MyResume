"""在工作区内按行搜索代码：支持字面量/正则、glob 过滤和上下文行。"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from langchain.tools import tool
from langgraph.prebuilt import ToolRuntime
from pydantic import BaseModel, Field

from app.agent.core.agent_exceptions import ToolException, ToolExceptionType
from app.graph.context.main_agent_context import MainAgentContext
from app.util.agent_util import verify_file_path_async

from ._common import (
    MAX_FILE_BYTES,
    MAX_LINE_CHARS,
    MAX_SCAN_FILES,
    check_int,
    iter_entries,
    match_glob,
    relative_posix,
    split_patterns,
)


class GrepCodeInput(BaseModel):
    pattern: str = Field(
        description="要搜索的文本或正则，按行匹配；不支持跨行"
    )
    path: str = Field(
        default=".",
        description="搜索目录或单个文件，默认工作区根目录",
    )
    glob: str = Field(
        default="",
        description=(
            "只搜索匹配的文件，例如 *.py 或 **/*.py；多个用逗号分隔。"
            "为空时搜索所有文本文件。"
        ),
    )
    case_sensitive: bool = Field(
        default=False,
        description="是否区分大小写，默认 false",
    )
    regex: bool = Field(
        default=True,
        description="pattern 是否按正则解释；false 时按字面量搜索",
    )
    context: int = Field(
        default=0,
        description="每个命中前后显示几行，0-3，默认 0",
    )
    max_results: int = Field(
        default=100,
        description="最多返回多少处命中，1-200，默认 100",
    )


def _grep_sync(
    root: Path,
    base: Path,
    compiled: re.Pattern[str],
    patterns: tuple[str, ...],
    context: int,
    max_results: int,
    is_file: bool,
) -> tuple[list[str], int, bool, int, int, bool]:
    output: list[str] = []
    match_count = 0
    scanned = 0
    skipped = 0
    truncated = False
    file_limit_hit = False

    source = [(root, False)] if is_file else iter_entries(root)

    for file_path, _ in source:
        scanned += 1
        if scanned > MAX_SCAN_FILES:
            file_limit_hit = True
            break

        rel = relative_posix(file_path, base)

        if not is_file and patterns and not match_glob(rel, patterns):
            continue

        try:
            if file_path.stat().st_size > MAX_FILE_BYTES:
                skipped += 1
                continue
            raw = file_path.read_bytes()
        except OSError:
            skipped += 1
            continue

        # 二进制文件不搜；.git 已在遍历时跳过
        if b"\x00" in raw[:1024]:
            skipped += 1
            continue

        lines = raw.decode("utf-8", errors="replace").splitlines()

        for index, line in enumerate(lines):
            if not compiled.search(line):
                continue

            match_count += 1

            if context:
                start = max(0, index - context)
                end = min(len(lines), index + context + 1)

                for ctx_index in range(start, end):
                    marker = ":" if ctx_index == index else "-"
                    snippet = lines[ctx_index][:MAX_LINE_CHARS]
                    output.append(f"{rel}:{ctx_index + 1}{marker} {snippet}")
            else:
                output.append(f"{rel}:{index + 1}: {line[:MAX_LINE_CHARS]}")

            if match_count >= max_results:
                truncated = True
                break

        if truncated:
            break

    return output, match_count, truncated, scanned, skipped, file_limit_hit


@tool(args_schema=GrepCodeInput)
async def grep_code(
    pattern: str,
    runtime: ToolRuntime[MainAgentContext],
    path: str = ".",
    glob: str = "",
    case_sensitive: bool = False,
    regex: bool = True,
    context: int = 0,
    max_results: int = 100,
) -> str:
    """在工作区内按行搜索代码，返回“文件:行号: 内容”。

    支持字面量或正则、按 glob 过滤文件、显示上下文行。
    自动跳过 .git、node_modules、.venv 等目录，以及二进制文件和超过 1MB 的文件。
    """
    if pattern == "":
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            "pattern 不能为空",
            is_retryable=False,
            suggestion="传要搜索的关键字，例如 TODO 或 def main",
        )

    if "\n" in pattern or "\r" in pattern:
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            "pattern 不能包含换行：本工具按行搜索",
            is_retryable=False,
            suggestion="跨行搜索请拆成多个单行 pattern，或用 read 直接看文件",
        )

    context = check_int(context, "context", 0, 3)
    max_results = check_int(max_results, "max_results", 1, 200)

    try:
        patterns = split_patterns(glob)
    except ValueError as e:
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            str(e),
            is_retryable=False,
            suggestion="glob 传相对模式，例如 *.py、**/*.ts；多个用逗号分隔",
        ) from e

    flags = 0 if case_sensitive else re.IGNORECASE

    try:
        compiled = re.compile(pattern if regex else re.escape(pattern), flags)
    except re.error as e:
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            f"正则不合法：{e}",
            is_retryable=False,
            suggestion="检查括号和转义；只是找普通文本就把 regex 设为 false",
        ) from e

    base = runtime.context.agent_work_space.resolve()
    rel_path = (path or "").strip() or "."

    accept, reason = await verify_file_path_async(
        rel_path,
        base,
        must_exist=False,
    )
    if not accept:
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            reason,
            is_retryable=False,
            suggestion="path 只支持工作区内的相对路径",
        )

    full_path = (base / rel_path).resolve()

    if not await asyncio.to_thread(full_path.exists):
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            f"路径不存在：{rel_path}",
            is_retryable=False,
            suggestion="先用 ls 确认目录，再传相对路径",
        )

    is_file = await asyncio.to_thread(full_path.is_file)
    is_dir = await asyncio.to_thread(full_path.is_dir)

    if not is_file and not is_dir:
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            f"路径既不是文件也不是目录：{rel_path}",
            is_retryable=False,
            suggestion="传一个目录或普通文件",
        )

    output, count, truncated, scanned, skipped, file_limit_hit = await asyncio.to_thread(
        _grep_sync,
        full_path,
        base,
        compiled,
        patterns,
        context,
        max_results,
        is_file,
    )

    scope = rel_path
    if patterns:
        scope += "，glob=" + ", ".join(patterns)

    if not output:
        lines = [
            f'没有找到匹配 "{pattern}" 的内容。',
            f"搜索范围：{scope}",
            f"扫描文件：{scanned}",
        ]
        if skipped:
            lines.append(f"跳过 {skipped} 个二进制/超大/不可读文件。")
        if file_limit_hit:
            lines.append(f"扫描文件数达到 {MAX_SCAN_FILES}，结果可能不完整。")
        return "\n".join(lines)

    lines = [f'匹配 "{pattern}"（{scope}），共 {count} 处：']
    lines.extend(output)

    if truncated:
        lines.append(
            f"... 已达到 max_results={max_results}，结果可能被截断。"
        )
    if file_limit_hit:
        lines.append(
            f"... 扫描文件数达到 {MAX_SCAN_FILES}，结果可能不完整。"
        )
    if skipped:
        lines.append(f"... 跳过 {skipped} 个二进制/超大/不可读文件。")

    return "\n".join(lines)