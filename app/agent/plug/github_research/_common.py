"""grep_code / glob_files 共用的工作区遍历与 glob 匹配工具。"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Iterator

from app.agent.core.agent_exceptions import ToolException, ToolExceptionType

# 这些目录通常不是“项目源码”，递归扫描时直接跳过，避免把上下文和磁盘打爆
IGNORED_DIRS = frozenset({
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "site-packages",
    "node_modules",
    "bower_components",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    ".idea",
    ".vscode",
    ".cache",
    "dist",
    "build",
    "target",
    "coverage",
    "htmlcov",
    ".next",
    ".nuxt",
    ".svelte-kit",
    "vendor",
})

# 单文件超过 1MB 基本是打包产物/数据文件，代码搜索跳过
MAX_FILE_BYTES = 1_000_000
# 单行最多显示这么多字符，避免压缩后的 JS/CSS 一行灌满上下文
MAX_LINE_CHARS = 300
# grep 一次最多扫描的文件数
MAX_SCAN_FILES = 20_000
# glob 一次最多遍历的条目数（文件 + 目录）
MAX_SCAN_ENTRIES = 50_000


def check_int(value: int, name: str, low: int, high: int) -> int:
    """把参数转成整数并检查范围；越界直接报错，不静默改写。"""
    try:
        value = int(value)
    except (TypeError, ValueError) as e:
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            f"{name} 必须是整数",
            is_retryable=True,
            suggestion=f"{name} 取值 {low}–{high}",
        ) from e

    if not low <= value <= high:
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            f"{name} 必须在 {low}–{high} 之间，当前为 {value}",
            is_retryable=True,
            suggestion=f"{name} 取值 {low}–{high}",
        )

    return value


def split_patterns(raw: str) -> tuple[str, ...]:
    """把 "*.py, **/*.ts" 拆成模式元组；空字符串表示不过滤。"""
    patterns: list[str] = []

    for part in (
        (raw or "")
        .replace(";", ",")
        .replace("；", ",")
        .replace("，", ",")
        .split(",")
    ):
        item = part.strip().replace("\\", "/")

        if not item or item == ".":
            continue

        if item.startswith("./"):
            item = item[2:]

        item = item.rstrip("/")
        if not item:
            continue

        if item.startswith("/") or (len(item) > 1 and item[1] == ":"):
            raise ValueError(f"glob 不能是绝对路径：{part.strip()}")

        if ".." in PurePosixPath(item).parts:
            raise ValueError(f"glob 不能包含 ..：{part.strip()}")

        if item not in patterns:
            patterns.append(item)

    return tuple(patterns)


def _segment_regex(segment: str) -> str:
    """把一段 glob 转成不带首尾锚点的正则；* 和 ? 不跨 /。"""
    parts: list[str] = []
    index = 0

    while index < len(segment):
        char = segment[index]

        if char == "*":
            parts.append("[^/]*")
            index += 1
            continue

        if char == "?":
            parts.append("[^/]")
            index += 1
            continue

        if char == "[":
            end = index + 1
            if end < len(segment) and segment[end] in "!^":
                end += 1
            if end < len(segment) and segment[end] == "]":
                end += 1
            while end < len(segment) and segment[end] != "]":
                end += 1

            if end >= len(segment):
                parts.append(r"\[")
                index += 1
                continue

            content = segment[index + 1:end]
            if content.startswith("!"):
                content = "^" + content[1:]
            parts.append("[" + content + "]")
            index = end + 1
            continue

        parts.append(re.escape(char))
        index += 1

    return "".join(parts)


@lru_cache(maxsize=256)
def _glob_regex(pattern: str) -> re.Pattern[str]:
    segments = pattern.split("/")
    parts: list[str] = []

    for index, segment in enumerate(segments):
        if segment == "**":
            # 中间的 ** 匹配零到多级目录；末尾 ** 匹配剩余全部内容
            if index == len(segments) - 1:
                parts.append(".*")
            else:
                parts.append("(?:[^/]+/)*")
            continue

        parts.append(_segment_regex(segment))

        if index != len(segments) - 1:
            parts.append("/")

    body = "".join(parts)

    # 没有 / 的模式按文件名匹配，任意层级都算，例如 *.py 匹配 src/a.py
    if "/" not in pattern:
        body = r"(?:.*/)?" + body

    return re.compile("^" + body + "$")


def match_glob(rel_path: str, patterns: tuple[str, ...]) -> bool:
    """按相对路径匹配 glob；patterns 为空表示全部匹配。"""
    if not patterns:
        return True

    return any(
        _glob_regex(pattern).match(rel_path)
        for pattern in patterns
    )


def iter_entries(
    root: Path,
    include_dirs: bool = False,
) -> Iterator[tuple[Path, bool]]:
    """遍历目录，产出 (路径, 是否目录)。

    跳过常见依赖/构建目录和符号链接，避免扫进 node_modules 或工作区外。
    """
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in IGNORED_DIRS
            and not os.path.islink(os.path.join(dirpath, name))
        )

        if include_dirs:
            for name in dirnames:
                yield Path(dirpath) / name, True

        for name in sorted(filenames):
            file_path = Path(dirpath) / name
            if file_path.is_symlink():
                continue
            yield file_path, False


def relative_posix(path: Path, base: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.as_posix()