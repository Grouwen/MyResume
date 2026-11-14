from json import JSONDecodeError

from httpx import TimeoutException, HTTPStatusError
from langchain.tools import tool
from langgraph.prebuilt import ToolRuntime
from pydantic import BaseModel, Field

from app.agent.core.agent_exceptions import ToolException, ToolExceptionType
from app.config.tavily_config import tavily_config
from app.graph.context.main_agent_context import MainAgentContext

TAVILY_URL = "https://api.tavily.com/search"


class SearchInput(BaseModel):
    query: str = Field(description="搜索关键词，用自然语言写完整问题效果更好")
    max_results: int = Field(
        default=5,
        description="返回几条结果，1-10，不填用默认值",
    )


@tool(args_schema=SearchInput)
async def search(query: str,
                 runtime: ToolRuntime[MainAgentContext],
                 max_results: int = 5) -> str:
    """在互联网上搜索信息，返回标题、URL 和摘要。

    只给线索，不给网页全文。想读某个页面的具体内容，
    拿到 URL 后再调用 fetch。
    """
    query = (query or "").strip()
    if not query:
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            "query 不能为空",
            is_retryable=True,
            suggestion="想清楚要搜什么，再调一次",
        )

    limit = max(1, min(int(max_results), 10))

    httpx_oper = runtime.context.httpx_oper

    try:
        resp = await httpx_oper.post_json(
            TAVILY_URL,
            json={
                "api_key": tavily_config.api_key,
                "query": query,
                "country":"china",
                "language": "zh-cn",
                "max_results": limit,
                "search_depth": "basic",
                "include_answer": False,
                "include_raw_content": False
            },
            timeout=30,
        )
    except TimeoutException as e:
        raise ToolException(
            ToolExceptionType.TOOL_RUNNING_ERROR,
            "搜索超时",
            is_retryable=True,
            suggestion="可以换个更短的关键词再试一次",
        ) from e
    except HTTPStatusError as e:
        code = e.response.status_code
        raise ToolException(
            ToolExceptionType.TOOL_RUNNING_ERROR,
            f"搜索服务返回 {code}",
            is_retryable=code >= 500 or code == 429,
            suggestion="换个关键词重试；若持续失败，说明搜索服务不可用，先别搜了",
        ) from e
    except JSONDecodeError as e:
        raise ToolException(
            ToolExceptionType.TOOL_RUNNING_ERROR,
            f"json格式化失败",
            is_retryable=True,
            suggestion="尝试使用其他tool或重试",
        ) from e

    results = resp.get("results") or []

    if not results:
        return f"没有搜到「{query}」的结果。换个说法或换个角度再搜。"

    lines = [f"搜索「{query}」得到 {len(results)} 条结果："]
    for i, item in enumerate(results, 1):
        title = (item.get("title") or "无标题").strip()
        url = (item.get("url") or "").strip()
        snippet = (item.get("content") or "").strip()
        snippet = snippet[:500] + ("…" if len(snippet) > 500 else "")
        lines.append(f"\n[{i}] {title}\nURL: {url}\n摘要: {snippet}")

    # lines.append("\n注意：摘要只是片段，不能当事实依据。要引用具体内容，先 fetch 对应 URL。")
    return "\n".join(lines)