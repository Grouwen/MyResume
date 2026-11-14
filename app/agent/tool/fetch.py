import time
from json import JSONDecodeError
from typing import Optional

from httpx import HTTPStatusError, TimeoutException
from langchain.tools import tool
from langgraph.prebuilt import ToolRuntime
from pydantic import BaseModel, Field

from app.agent.core.agent_exceptions import ToolException, ToolExceptionType
from app.config.tavily_config import tavily_config
from app.graph.context.main_agent_context import MainAgentContext
from app.util.web_util import UnsafeUrlError, truncate, validate_url

TAVILY_EXTRACT_URL = "https://api.tavily.com/extract"

# 缓存
_CACHE: dict[str, tuple[float, str]] = {}
_CACHE_TTL = 1800      # 30 分钟。extract 比 search 贵，缓存久一点
_CACHE_MAX = 64

class FetchInput(BaseModel):
    url: str = Field(description="要打开的网址，必须是 http:// 或 https:// 开头")
    start_index: int = Field(
        default=0,
        description="从第几个字符开始读，默认 0。上次返回说还有内容时，传上次结束的位置",
    )
    max_chars: int = Field(
        default=8000,
        description="这次最多返回多少字符，默认 8000，可调到 20000",
    )


def _cache_get(url: str) -> Optional[str]:
    hit = _CACHE.get(url)
    if not hit:
        return None
    ts, text = hit
    if time.time() - ts > _CACHE_TTL:
        _CACHE.pop(url, None)
        return None
    return text


def _cache_put(url: str, text: str) -> None:
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)), None)
    _CACHE[url] = (time.time(), text)


async def _tavily_extract(url: str, httpx_oper) -> str:
    """调 Tavily Extract，返回该 URL 的正文。"""
    try:
        data = await httpx_oper.post_json(
            TAVILY_EXTRACT_URL,
            json={
                "urls": [url],                 # 单 URL 也传数组，接口就是这么要的
                "format": "markdown",          # 默认就是 markdown，显式写清楚
                "extract_depth": tavily_config.extract_depth,
                "include_images": False,       # 图片对模型没用
                "include_usage": True,         # 返回 credits，方便排查花了多少
                # 不传 query：传了会按相关性裁 chunk，只回片段不回全文
            },
            headers={"Authorization": f"Bearer {tavily_config.api_key}"},
            timeout=30,
        )
    except TimeoutException as e:
        raise ToolException(
            ToolExceptionType.TOOL_RUNNING_ERROR,
            f"提取 {url} 超时",
            is_retryable=True,
            suggestion="这个页面太大或太慢，换一个来源，或者别硬等",
        ) from e
    except HTTPStatusError as e:
        code = e.response.status_code
        if code == 401:
            raise ToolException(
                ToolExceptionType.TOOL_RUNNING_ERROR,
                "Tavily API key 无效",
                is_retryable=False,
                suggestion="告诉用户搜索/抓取服务未配置好，别重复调用",
            ) from e
        if code in {429, 432, 433}:
            raise ToolException(
                ToolExceptionType.TOOL_RUNNING_ERROR,
                f"Tavily 额度或频率受限（{code}）",
                is_retryable=False,
                suggestion="现在抓不了网页了，先用搜索结果里的摘要回答，并告诉用户受限",
            ) from e
        if code == 400:
            raise ToolException(
                ToolExceptionType.INVALID_ARGUMENTS,
                f"URL 被 Tavily 拒绝：{url}",
                is_retryable=False,
                suggestion="这个链接格式有问题，换一个",
            ) from e
        raise ToolException(
            ToolExceptionType.TOOL_RUNNING_ERROR,
            f"Tavily 服务返回 {code}",
            is_retryable=code >= 500,
            suggestion="稍后重试；连续失败就别抓了",
        ) from e
    except JSONDecodeError as e:
        raise ToolException(
            ToolExceptionType.TOOL_RUNNING_ERROR,
            "Tavily 返回的不是合法 JSON",
            is_retryable=True,
            suggestion="重试一次；还失败就换来源",
        ) from e
    except Exception as e:
        raise ToolException(
            ToolExceptionType.TOOL_RUNNING_ERROR,
            f"调用 Tavily 失败：{str(e)}",
            is_retryable=True,
            suggestion="重试一次；还失败就换来源",
        ) from e

    # 200 也可能全失败，必须看 failed_results
    results = data.get("results") or []
    if not results:
        failed = data.get("failed_results") or []
        reason = failed[0].get("error", "未知原因") if failed else "没有返回内容"
        raise ToolException(
            ToolExceptionType.TOOL_RUNNING_ERROR,
            f"{url} 提取失败：{reason}",
            is_retryable=False,
            suggestion="这个页面抓不到，换一个来源，别重复试同一个 URL",
        )

    text = (results[0].get("raw_content") or "").strip()
    if not text:
        raise ToolException(
            ToolExceptionType.TOOL_RUNNING_ERROR,
            f"{url} 抓到了但正文是空的",
            is_retryable=False,
            suggestion="这个页面可能是空壳或需要登录，换一个来源",
        )

    return text


@tool(args_schema=FetchInput)
async def fetch(url: str,
                runtime: ToolRuntime[MainAgentContext],
                start_index: int = 0,
                max_chars: int = 8000) -> str:
    """打开一个网址，返回网页正文（markdown 格式）。

    长文会分段返回，看返回末尾的提示决定要不要用 start_index 继续读。
    """
    try:
        url = validate_url(url)
    except UnsafeUrlError as e:
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            str(e),
            is_retryable=False,
            suggestion="只能打开公网 http/https 页面，换个链接",
        ) from e

    limit = max(1000, min(int(max_chars), 20000))
    start = max(0, int(start_index))

    # 缓存的是全文，截断/翻页在缓存外面做
    full_text = _cache_get(url)

    if full_text is None:
        full_text = await _tavily_extract(url, runtime.context.httpx_oper)
        _cache_put(url, full_text)

    piece, has_more, total = truncate(full_text, limit, start)

    head = f"来源：{url}\n全文 {total} 字，本次返回第 {start + 1}-{start + len(piece)} 字\n\n"
    tail = ""
    if has_more:
        tail = (
            f"\n\n[后面还有 {total - start - len(piece)} 字没显示。"
            f"需要就再调 fetch，传 start_index={start + len(piece)}]"
        )
    return head + piece + tail