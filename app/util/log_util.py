"""日志里的正文处理：INFO 只留摘要，正文只在 DEBUG 出现。"""

from __future__ import annotations

PREVIEW_CHARS = 120        # INFO 摘要里正文片段的长度上限
DEBUG_BODY_CHARS = 20_000  # DEBUG 全文的硬上限，防止一条日志几十 MB


def preview(text: str | None, limit: int = PREVIEW_CHARS) -> str:
    """压成单行并截断：既不让一条日志被几十行换行拆碎，也不整段落盘。"""
    if not text:
        return ""
    flat = " ".join(str(text).split())
    if len(flat) <= limit:
        return flat
    return f"{flat[:limit]}…(len={len(flat)})"


def debug_body(text: str | None) -> str:
    """DEBUG 用：保留原始换行（代码块好读），但设硬上限。"""
    body = text or ""
    if len(body) <= DEBUG_BODY_CHARS:
        return body
    return f"{body[:DEBUG_BODY_CHARS]}…[已截断，原长 {len(body)}]"