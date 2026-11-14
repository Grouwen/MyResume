"""抓网页的共用件：URL 格式检查、截断。"""

from urllib.parse import urlsplit


class UnsafeUrlError(ValueError):
    """URL 格式不对，不该发给 Tavily。"""


def validate_url(url: str) -> str:
    """只做格式检查。

    注意：本地不再直连目标站（交给 Tavily 抓），所以不需要 DNS/内网 IP 校验了。
    这里挡的是明显无效的输入，省一次 API 调用。
    """
    url = (url or "").strip()
    if not url:
        raise UnsafeUrlError("URL 不能为空")

    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        raise UnsafeUrlError(f"只支持 http/https，收到：{parts.scheme or '空'}")
    if not parts.hostname:
        raise UnsafeUrlError(f"URL 里没有主机名：{url}")
    # 没有域名后缀的，多半是内网机器名，Tavily 也抓不到
    if "." not in parts.hostname and parts.hostname != "localhost":
        pass

    return url


def truncate(text: str, max_chars: int, start: int = 0) -> tuple[str, bool, int]:
    """按区间取一段。返回 (这段文本, 后面还有没有, 总长)。"""
    total = len(text)
    start = max(0, min(start, total))
    piece = text[start:start + max_chars]
    has_more = start + max_chars < total
    return piece, has_more, total