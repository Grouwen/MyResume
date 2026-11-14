from pathlib import Path
import re
import difflib
import re
import unicodedata
from bisect import bisect_right
import aiofiles
import unicodedata
from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from pydantic import BaseModel, Field

from app.agent.core.agent_exceptions import ToolException, ToolExceptionType
from app.graph.context.main_agent_context import MainAgentContext
from app.util.agent_util import verify_file_path

_SMART_SINGLE = re.compile(r"[\u2018\u2019\u201A\u201B]")
_SMART_DOUBLE = re.compile(r"[\u201C\u201D\u201E\u201F]")
_DASHES = re.compile(r"[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]")
_SPACES = re.compile(r"[\u00A0\u2002-\u200A\u202F\u205F\u3000]")
_LINE = re.compile(r"[^\n]*\n|[^\n]+")

class EditInput(BaseModel):
    file_path: str = Field(description="文件路径")
    old_str: str = Field(description="要修改的字符串")
    new_str: str = Field(description="修改内容")
    replace_all: bool = Field(default=False, description="old_str多处匹配时是否替换所有处。")

# TODO: 批量替换
@tool(args_schema=EditInput)
async def edit(
        file_path: str,
        old_str: str,
        new_str: str,
        runtime: ToolRuntime[MainAgentContext],
        replace_all: bool = False
):
    """修改指定文件的内容"""
    agent_work_space: Path = runtime.context.agent_work_space

    # 校验
    accept,reason = verify_file_path(file_path,agent_work_space,True,False)
    if not accept:
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            reason,
            True,
            "file_path 只支持相对路径且必须为文件，修改file_path"
        )

    # 读文件
    async with aiofiles.open(agent_work_space / file_path,"rb") as f:
        content = await f.read()
        content = content.decode("utf-8")

    # 改内容
    update_text, occurrences = await replace(content, old_str, new_str, replace_all)

    # 保存
    async with aiofiles.open(agent_work_space / file_path,"wb") as f:
        await f.write(update_text.encode("utf-8"))

    return f"成功修改 {file_path} 中 {occurrences}处内容"


async def replace(content: str,
                  old_str: str,
                  new_str: str,
                  replace_all: bool):
    if not old_str:
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            "old_str为空",
            is_retryable=True,
            suggestion="填写old_str"
        )

    target = normalize(old_str)
    if not target:
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            f"old_str在归一化后为空: {old_str!r}",
            is_retryable=True,
            suggestion="修改old_str"
        )

    # 只在“原始内容的归一化文本”上扫一次，后面所有替换都不再重新查找
    normalized = normalize(content)
    spans = _find_all(normalized, target)

    if not spans:
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            "未匹配到old_str",
            is_retryable=True,
            suggestion="确认old_str是否有效"
        )

    # 唯一性按归一化口径统计，弯引号和直引号视为同一个文本
    if len(spans) > 1 and not replace_all:
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            f"old_str 在文本中出现 {len(spans)}次",
            is_retryable=True,
            suggestion="提供更多上下文来使old_str唯一，或设置replace_all=True"
        )

    # 内部统一用 LF，再按目标文件的行尾风格还原
    new_text = new_str.replace("\r\n", "\n").replace("\r", "\n")
    if "\r\n" in content:
        new_text = new_text.replace("\n", "\r\n")

    # 原文能精确命中时优先走精确替换：完全不经过归一化，保真度最高
    if len(spans) == 1:
        index = content.find(old_str)
        if index != -1:
            return (
                content[:index] + new_text + content[index + len(old_str):],
                1,
            )

    # 归一化命中：把 normalized 坐标映射回原文坐标，只替换命中区间
    original_spans = _map_spans(spans, normalized, content)
    return _apply_spans(content, original_spans, new_text), len(original_spans)


def _subs(text: str) -> str:
    """只做 1:1 的字符替换，保证长度不变、映射不失效。"""
    text = _SMART_SINGLE.sub("'", text)
    text = _SMART_DOUBLE.sub('"', text)
    text = _DASHES.sub("-", text)
    return _SPACES.sub(" ", text)


def _normalize_line(line: str) -> str:
    """单行归一化，保留行尾。和 _normalize_line_with_map 的产出严格一致。"""
    has_eol = line.endswith("\n")
    body = line[:-1] if has_eol else line
    body = _subs(unicodedata.normalize("NFKC", body).rstrip())
    return body + ("\n" if has_eol else "")


def normalize(text: str) -> str:
    """按行归一化后再拼接。

    拆行处理是为了后面能按“行”建立坐标映射；NFKC 不会跨 \\n 组合，
    所以与原来整段归一化的结果一致。
    """
    return "".join(_normalize_line(line) for line in _LINE.findall(text))


def _normalize_line_with_map(line: str) -> tuple[str, list[tuple[int, int]]]:
    """归一化单行，并返回 normalized 每个字符对应的 original 区间。"""
    has_eol = line.endswith("\n")
    body = line[:-1] if has_eol else line

    nfkc = unicodedata.normalize("NFKC", body)
    if nfkc == body or (
            len(nfkc) == len(body)
            and not any(unicodedata.combining(ch) for ch in body)
    ):
        # 常见情况：已经是 NFKC，或长度不变的 1:1 兼容字符（全角→半角等）
        mapping: list[tuple[int, int]] = [(i, i + 1) for i in range(len(body))]
    else:
        # 极少见：NFKC 改变了长度（ﬁ→fi、①→1、㈱→(株)）或组合字符重排
        mapping = _align_nfkc(body, nfkc)

    # 行尾空白在归一化里被删掉，映射同步收缩
    keep = len(nfkc.rstrip())
    nfkc = _subs(nfkc[:keep])
    mapping = mapping[:keep]

    if has_eol:
        nfkc += "\n"
        # 原文的 "\n" 一定在行尾；CRLF 的 "\r" 落在区间内部，替换时会一并覆盖
        mapping.append((len(line) - 1, len(line)))

    return nfkc, mapping


def _align_nfkc(original: str, normalized: str) -> list[tuple[int, int]]:
    """NFKC 改变长度时，用序列对齐给 normalized 的每个字符找 original 区间。"""
    matcher = difflib.SequenceMatcher(None, original, normalized, autojunk=False)
    mapping: list[tuple[int, int] | None] = [None] * len(normalized)

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(j2 - j1):
                mapping[j1 + k] = (i1 + k, i1 + k + 1)
        elif tag == "replace":
            for k in range(j1, j2):
                mapping[k] = (i1, i2)
        elif tag == "insert":
            for k in range(j1, j2):
                mapping[k] = (i1, i1)
        # delete：原文被删掉的字符，normalized 里没有对应位置

    last = 0
    for k in range(len(normalized)):
        if mapping[k] is None:
            mapping[k] = (last, last)
        last = mapping[k][1]
    return mapping  # type: ignore[return-value]


def _find_all(text: str, needle: str) -> list[tuple[int, int]]:
    """一次性找出所有非重叠命中区间。"""
    spans: list[tuple[int, int]] = []
    start = 0
    while True:
        index = text.find(needle, start)
        if index == -1:
            return spans
        spans.append((index, index + len(needle)))
        start = index + len(needle)


def _line_starts(lines: list[str]) -> list[int]:
    starts: list[int] = []
    position = 0
    for line in lines:
        starts.append(position)
        position += len(line)
    return starts


def _map_spans(spans: list[tuple[int, int]],
               normalized: str,
               content: str) -> list[tuple[int, int]]:
    """把 normalized 的命中区间映射回原始 content 下标。"""
    normalized_lines = _LINE.findall(normalized)
    original_lines = _LINE.findall(content)
    if len(normalized_lines) != len(original_lines):
        # 归一化不改变 \n 数量，正常不会发生；真发生就报错，不写坏文件
        raise ToolException(
            ToolExceptionType.TOOL_RUNNING_ERROR,
            "归一化后行数与原文不一致",
            is_retryable=False,
            suggestion="请把这段内容拆小后重试"
        )

    normalized_starts = _line_starts(normalized_lines)
    original_starts = _line_starts(original_lines)

    # 只给“命中的行”建映射，避免大文件全量建表
    line_maps: dict[int, list[tuple[int, int]]] = {}
    mapped: list[tuple[int, int]] = []

    for span in spans:
        line_index = bisect_right(normalized_starts, span[0]) - 1
        original_start = None
        original_end = None

        while (line_index < len(normalized_lines)
               and normalized_starts[line_index] < span[1]):
            line_start = normalized_starts[line_index]
            local_start = max(span[0], line_start) - line_start
            local_end = min(span[1], line_start + len(normalized_lines[line_index])) - line_start

            if local_end > local_start:
                if line_index not in line_maps:
                    line_maps[line_index] = _normalize_line_with_map(
                        original_lines[line_index]
                    )[1]
                mapping = line_maps[line_index]

                segment_start = original_starts[line_index] + min(
                    mapping[i][0] for i in range(local_start, local_end)
                )
                segment_end = original_starts[line_index] + max(
                    mapping[i][1] for i in range(local_start, local_end)
                )
                if original_start is None:
                    original_start = segment_start
                original_end = segment_end

            line_index += 1

        if original_start is not None:
            mapped.append((original_start, original_end))

    # NFKC 展开字符可能让多个命中落到同一原始区间，合并后再替换
    merged: list[tuple[int, int]] = []
    for start, end in sorted(mapped):
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _apply_spans(content: str,
                 spans: list[tuple[int, int]],
                 new_text: str) -> str:
    """按原始坐标一次性拼接，命中区间之外的字符原样保留。"""
    parts: list[str] = []
    cursor = 0
    for start, end in spans:
        parts.append(content[cursor:start])
        parts.append(new_text)
        cursor = end
    parts.append(content[cursor:])
    return "".join(parts)