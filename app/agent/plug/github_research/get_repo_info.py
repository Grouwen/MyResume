# app/agent/tool/github_repo.py
import os
import re
from datetime import datetime, timezone
from json import JSONDecodeError
from urllib.parse import quote, urlsplit

from httpx import HTTPStatusError, TimeoutException
from langchain.tools import tool
from langgraph.prebuilt import ToolRuntime
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

from app.agent.core.agent_exceptions import ToolException, ToolExceptionType
from app.graph.context.main_agent_context import MainAgentContext

GITHUB_API = "https://api.github.com"

# GitHub 用户名：字母数字或连字符，不能以连字符开头/结尾，最长 39
OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
# 仓库名：字母数字、点、下划线、连字符，最长 100
REPO_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


class GithubConfig(BaseSettings):
    """可选配置：GITHUB_TOKEN，用于把匿名 60 次/小时提到 5000 次/小时。"""

    token: str = ""



github_config = GithubConfig()


class GithubRepoInput(BaseModel):
    url: str = Field(
        description="GitHub 仓库链接，例如 https://github.com/langchain-ai/langgraph"
    )


def _parse_repo_url(raw_url: str) -> tuple[str, str]:
    raw = (raw_url or "").strip()
    if not raw:
        raise ValueError("url 不能为空")

    # 允许直接传 github.com/owner/repo
    if raw.startswith(("github.com/", "www.github.com/")):
        raw = "https://" + raw

    parsed = urlsplit(raw)
    host = (parsed.hostname or "").lower()

    if host not in {"github.com", "www.github.com", "api.github.com"}:
        raise ValueError("只支持 github.com 的仓库链接")

    parts = [p for p in parsed.path.split("/") if p]

    if host == "api.github.com":
        if len(parts) < 3 or parts[0] != "repos":
            raise ValueError("API 链接应形如 https://api.github.com/repos/owner/repo")
        owner, repo = parts[1], parts[2]
    else:
        if len(parts) < 2:
            raise ValueError("链接里缺少 owner/repo")
        owner, repo = parts[0], parts[1]

    if repo.endswith(".git"):
        repo = repo[:-4]

    if repo in {".", ".."}:
        raise ValueError("owner/repo 格式不合法")
    if not OWNER_RE.fullmatch(owner) or not REPO_RE.fullmatch(repo):
        raise ValueError("owner/repo 格式不合法")

    return owner, repo


def _fmt_time(value) -> str:
    if not value:
        return "未知"
    return str(value).replace("T", " ").replace("Z", " UTC")


def _fmt_rate_limit(response) -> str:
    reset = response.headers.get("x-ratelimit-reset")
    if reset and reset.isdigit():
        dt = datetime.fromtimestamp(int(reset), tz=timezone.utc).astimezone()
        return f"GitHub API 限额已用完，{dt:%Y-%m-%d %H:%M:%S %Z} 恢复"
    return "GitHub API 限额已用完"


@tool(args_schema=GithubRepoInput)
async def get_repo_info(url: str, runtime: ToolRuntime[MainAgentContext]) -> str:
    """根据 GitHub 仓库链接获取仓库信息（Star、Fork、语言、License、主题、更新时间等）。

    只查询仓库元信息，不读取代码内容；支持 github.com/owner/repo 及其子页面链接。
    """
    try:
        owner, repo = _parse_repo_url(url)
    except ValueError as e:
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            str(e),
            is_retryable=False,
            suggestion="传一个 GitHub 仓库链接，例如 https://github.com/owner/repo",
        ) from e

    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = github_config.token or os.getenv("GITHUB_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    api_url = f"{GITHUB_API}/repos/{quote(owner, safe='')}/{quote(repo, safe='')}"

    try:
        data = await runtime.context.httpx_oper.get_json(
            api_url,
            headers=headers,
            timeout=20,
        )
    except TimeoutException as e:
        raise ToolException(
            ToolExceptionType.TOOL_RUNNING_ERROR,
            "请求 GitHub 超时",
            is_retryable=True,
            suggestion="稍后重试一次；还失败就告诉用户 GitHub 暂时不可用",
        ) from e
    except HTTPStatusError as e:
        code = e.response.status_code

        if code == 404:
            raise ToolException(
                ToolExceptionType.TOOL_RUNNING_ERROR,
                f"仓库 {owner}/{repo} 不存在或无权访问",
                is_retryable=False,
                suggestion="确认链接是否正确；私有仓库需要配置 GITHUB_TOKEN",
            ) from e

        if code == 401:
            raise ToolException(
                ToolExceptionType.TOOL_RUNNING_ERROR,
                "GITHUB_TOKEN 无效或已过期",
                is_retryable=False,
                suggestion="检查 GITHUB_TOKEN；也可以先不配 token，用匿名限额查询公开仓库",
            ) from e

        if code in {403, 429}:
            remaining = e.response.headers.get("x-ratelimit-remaining")
            if remaining == "0":
                raise ToolException(
                    ToolExceptionType.TOOL_RUNNING_ERROR,
                    _fmt_rate_limit(e.response),
                    is_retryable=False,
                    suggestion="配置 GITHUB_TOKEN 提高限额，或等限额恢复后再试",
                ) from e
            raise ToolException(
                ToolExceptionType.TOOL_RUNNING_ERROR,
                f"GitHub 拒绝访问（{code}）",
                is_retryable=False,
                suggestion="确认仓库是否私有；私有仓库需要有效的 GITHUB_TOKEN",
            ) from e

        raise ToolException(
            ToolExceptionType.TOOL_RUNNING_ERROR,
            f"GitHub 返回 {code}",
            is_retryable=code >= 500,
            suggestion="稍后重试；连续失败就别再查这个仓库了",
        ) from e
    except JSONDecodeError as e:
        raise ToolException(
            ToolExceptionType.TOOL_RUNNING_ERROR,
            "GitHub 返回的不是合法 JSON",
            is_retryable=True,
            suggestion="重试一次；还失败就告诉用户 GitHub 接口异常",
        ) from e
    except Exception as e:
        raise ToolException(
            ToolExceptionType.TOOL_RUNNING_ERROR,
            f"请求 GitHub 失败：{e}",
            is_retryable=True,
            suggestion="重试一次；还失败就换一种方式确认仓库信息",
        ) from e

    if not isinstance(data, dict):
        raise ToolException(
            ToolExceptionType.TOOL_RUNNING_ERROR,
            "GitHub 返回的数据格式不符合预期",
            is_retryable=True,
            suggestion="重试一次；还失败就告诉用户 GitHub 接口异常",
        )

    full_name = data.get("full_name") or f"{owner}/{repo}"
    description = (data.get("description") or "").strip()
    if len(description) > 500:
        description = description[:500] + "…"

    owner_info = data.get("owner") or {}
    license_info = data.get("license") or {}
    license_name = license_info.get("spdx_id") or license_info.get("name") or "无"
    if license_name == "NOASSERTION":
        license_name = license_info.get("name") or "未标注"

    stars = data.get("stargazers_count") or 0
    forks = data.get("forks_count") or 0
    watchers = data.get("subscribers_count") or 0
    open_issues = data.get("open_issues_count") or 0
    size = data.get("size") or 0

    lines = [
        f"仓库：{full_name}",
        f"描述：{description or '（无）'}",
        f"链接：{data.get('html_url') or f'https://github.com/{owner}/{repo}'}",
    ]

    if data.get("homepage"):
        lines.append(f"主页：{data['homepage']}")

    lines.append(
        f"所有者：{owner_info.get('login', owner)}（{owner_info.get('type', '未知')}）"
    )
    lines.append(
        f"语言：{data.get('language') or '未知'} | "
        f"License：{license_name} | "
        f"默认分支：{data.get('default_branch') or '未知'}"
    )
    lines.append(
        f"Star：{stars:,} | Fork：{forks:,} | Watcher：{watchers:,} | "
        f"Open Issues：{open_issues:,}（含 PR）"
    )

    topics = [t for t in (data.get("topics") or []) if isinstance(t, str)]
    if topics:
        lines.append("主题：" + "、".join(topics[:20]))

    lines.append(
        f"创建：{_fmt_time(data.get('created_at'))} | "
        f"更新：{_fmt_time(data.get('updated_at'))} | "
        f"最近推送：{_fmt_time(data.get('pushed_at'))}"
    )

    status = []
    if data.get("archived"):
        status.append("已归档")
    if data.get("fork"):
        status.append("Fork 仓库")
    if data.get("is_template"):
        status.append("模板仓库")
    if data.get("disabled"):
        status.append("已禁用")

    lines.append(
        f"可见性：{data.get('visibility') or ('private' if data.get('private') else 'public')} | "
        f"大小：{size:,} KB | "
        f"状态：{'、'.join(status) or '正常'}"
    )

    return "\n".join(lines)