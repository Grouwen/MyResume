# app/agent/tool/clone_repo.py
"""把 Git 仓库浅克隆到 agent 工作区：只看默认分支最近 1 个提交。"""

import asyncio
import ipaddress
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from langchain.tools import tool
from langgraph.prebuilt import ToolRuntime
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

from app.agent.core.agent_exceptions import ToolException, ToolExceptionType
from app.graph.context.main_agent_context import MainAgentContext
from app.util import config_util
from app.util.agent_util import verify_file_path_async

OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
REPO_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
CREDENTIAL_RE = re.compile(r"(?i)(https?://)[^/@\s]+@")

CLONE_DEPTH = 1


class CloneConfig(BaseSettings):
    """默认只允许主流代码托管站；需要别的源就在 .env 加 CLONE_ALLOWED_HOSTS。"""

    allowed_hosts: str = "github.com,gitlab.com,gitee.com,bitbucket.org"

    model_config = config_util.get_setting_config("CLONE_")


clone_config = CloneConfig()


class CloneRepoInput(BaseModel):
    url: str = Field(
        description="Git 仓库地址，例如 https://github.com/owner/repo.git"
    )
    directory: str = Field(
        default="",
        description="克隆到工作区下的相对目录，默认使用仓库名；目录已存在会报错，不会覆盖",
    )
    timeout: int = Field(
        default=120,
        description="超时秒数，30-600，默认 120",
    )


def _normalize_host(host: str) -> str:
    host = (host or "").strip().lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def _is_forbidden_host(host: str) -> bool:
    host = host.strip("[]").lower().rstrip(".")

    if host in {"localhost", "localhost.localdomain", "ip6-localhost"}:
        return True

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False

    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _allowed_hosts() -> set[str]:
    return {
        _normalize_host(item)
        for item in (clone_config.allowed_hosts or "").split(",")
        if item.strip()
    }


def _assert_host_allowed(host: str) -> None:
    host = _normalize_host(host)

    if _is_forbidden_host(host):
        raise ValueError("不允许克隆本机/内网地址")

    allowed = _allowed_hosts()
    if "*" not in allowed and host not in allowed:
        raise ValueError(
            f"不允许的仓库主机：{host}；默认允许 {clone_config.allowed_hosts}"
        )


def _name_from_path(path: str) -> str:
    name = (path or "").rstrip("/").rsplit("/", 1)[-1]

    if name.endswith(".git"):
        name = name[:-4]

    name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-")
    return name[:100]


def _normalize_clone_url(raw_url: str) -> tuple[str, str]:
    """返回 (clone_url, 默认目录名)。"""
    raw = (raw_url or "").strip()

    if not raw:
        raise ValueError("url 不能为空")
    if len(raw) > 2048:
        raise ValueError("url 太长")
    if raw.startswith("-"):
        raise ValueError("url 不能以 - 开头")
    if any(ch in raw for ch in "\x00\r\n\t"):
        raise ValueError("url 不能包含换行/制表符")

    if raw.startswith(("github.com/", "www.github.com/")):
        raw = "https://" + raw

    scp_match = re.fullmatch(r"git@([^:/\s]+):(.+)", raw)
    if scp_match:
        host, path = scp_match.group(1), scp_match.group(2)
        _assert_host_allowed(host)

        name = _name_from_path(path)
        if not name:
            raise ValueError("无法从 url 解析仓库名")

        return raw, name

    parts = urlsplit(raw)
    scheme = (parts.scheme or "").lower()

    if scheme not in {"http", "https", "ssh", "git"}:
        raise ValueError("只支持 http/https/ssh/git 协议的仓库地址")
    if not parts.hostname:
        raise ValueError("url 里没有主机名")

    host = _normalize_host(parts.hostname)
    _assert_host_allowed(host)

    # GitHub 网页链接统一还原成仓库根地址，支持 /tree/main、/issues/1 等
    if scheme in {"http", "https"} and host == "github.com":
        segments = [part for part in parts.path.split("/") if part]

        if len(segments) < 2:
            raise ValueError("GitHub 链接里缺少 owner/repo")

        owner, repo = segments[0], segments[1]
        if repo.endswith(".git"):
            repo = repo[:-4]

        if not OWNER_RE.fullmatch(owner) or not REPO_RE.fullmatch(repo):
            raise ValueError("owner/repo 格式不合法")

        clone_url = f"{scheme}://{parts.netloc}/{owner}/{repo}.git"
        return clone_url, repo

    name = _name_from_path(parts.path)
    if not name:
        raise ValueError("无法从 url 解析仓库名")

    return raw, name


def _redact(text: str) -> str:
    return CREDENTIAL_RE.sub(r"\1***@", text or "")


def _run_git(args: list[str], timeout: int) -> subprocess.CompletedProcess:
    """同步执行 git，调用方丢线程池。

    应用在 Windows 下用 SelectorEventLoop，asyncio 子进程不可用，
    所以这里用 subprocess.run + asyncio.to_thread。
    """
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    env["GIT_LFS_SKIP_SMUDGE"] = "1"
    env.setdefault(
        "GIT_SSH_COMMAND",
        "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new",
    )

    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
        check=False,
    )


async def _git_value(args: list[str]) -> str:
    try:
        result = await asyncio.to_thread(_run_git, args, 10)
    except (subprocess.TimeoutExpired, OSError):
        return ""

    if result.returncode != 0:
        return ""

    return (result.stdout or "").strip()


async def _remove_dir(path: Path) -> None:
    if await asyncio.to_thread(path.exists):
        await asyncio.to_thread(shutil.rmtree, path, True)


def _top_level_entries(target: Path) -> tuple[list[str], int]:
    items: list[tuple[bool, str]] = []

    with os.scandir(target) as it:
        for entry in it:
            if entry.name == ".git":
                continue

            try:
                is_dir = entry.is_dir()
            except OSError:
                is_dir = False

            items.append((is_dir, entry.name))

    items.sort(key=lambda item: (not item[0], item[1].lower()))
    shown = items[:20]

    return [
        f"{name}/" if is_dir else name
        for is_dir, name in shown
    ], len(items)


@tool(args_schema=CloneRepoInput)
async def clone_repo(
    url: str,
    runtime: ToolRuntime[MainAgentContext],
    directory: str = "",
    timeout: int = 120,
) -> str:
    """把 Git 仓库浅克隆到本地工作区，只拉默认分支最近 1 个提交。

    始终使用仓库默认分支，且只保留最近 1 个提交。
    返回保存位置（agent_work_space 内的相对路径），可直接传给 ls/read/edit。
    不会覆盖已存在的目录。
    """
    try:
        clone_url, repo_name = _normalize_clone_url(url)
    except ValueError as e:
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            str(e),
            is_retryable=False,
            suggestion=(
                "传一个仓库根地址，例如 https://github.com/owner/repo.git；"
                "不要传 file://、本机路径或内网地址"
            ),
        ) from e

    try:
        timeout = int(timeout)
    except (TypeError, ValueError) as e:
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            "timeout 必须是整数",
            is_retryable=True,
            suggestion="timeout 传 30 到 600 之间的秒数，默认 120",
        ) from e

    if not 30 <= timeout <= 600:
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            f"timeout 必须在 30–600 秒之间，当前为 {timeout}",
            is_retryable=True,
            suggestion="默认 120 秒；仓库太大就适当加大",
        )

    directory = (directory or "").strip() or repo_name
    workspace: Path = runtime.context.agent_work_space

    accept, reason = await verify_file_path_async(
        directory,
        workspace,
        must_exist=False,
    )
    if not accept:
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            reason,
            is_retryable=False,
            suggestion="directory 只支持工作区内的相对路径",
        )

    base = workspace.resolve()
    target = (base / directory).resolve()

    if target == base:
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            "directory 不能是工作区根目录",
            is_retryable=False,
            suggestion="换一个子目录名，例如 repo-name",
        )

    if shutil.which("git") is None:
        raise ToolException(
            ToolExceptionType.TOOL_RUNNING_ERROR,
            "服务器未安装 git",
            is_retryable=False,
            suggestion="联系管理员安装 git 后再试",
        )

    if await asyncio.to_thread(target.exists):
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            f"目录 {directory} 已存在，不会覆盖",
            is_retryable=False,
            suggestion="换一个 directory；已有目录请先用 ls 查看",
        )

    try:
        await asyncio.to_thread(
            target.parent.mkdir,
            parents=True,
            exist_ok=True,
        )
        await asyncio.to_thread(target.mkdir, exist_ok=False)
    except FileExistsError as e:
        raise ToolException(
            ToolExceptionType.INVALID_ARGUMENTS,
            f"目录 {directory} 已存在，不会覆盖",
            is_retryable=False,
            suggestion="换一个 directory",
        ) from e
    except OSError as e:
        raise ToolException(
            ToolExceptionType.TOOL_RUNNING_ERROR,
            f"创建目录失败：{e}",
            is_retryable=True,
            suggestion="确认工作区目录可写",
        ) from e

    # 硬性条件：默认分支 + 最近 1 个提交。
    # --single-branch 不配 --branch 时，只拉远端 HEAD 指向的默认分支。
    args = [
        "clone",
        "--quiet",
        "--depth", str(CLONE_DEPTH),
        "--single-branch",
        "--no-tags",
    ]
    args.extend(["--", clone_url, str(target)])

    try:
        result = await asyncio.to_thread(_run_git, args, timeout)
    except subprocess.TimeoutExpired as e:
        await _remove_dir(target)
        raise ToolException(
            ToolExceptionType.TOOL_RUNNING_ERROR,
            f"克隆超时（>{timeout} 秒）",
            is_retryable=True,
            suggestion="加大 timeout，或换更小的仓库",
        ) from e
    except FileNotFoundError as e:
        await _remove_dir(target)
        raise ToolException(
            ToolExceptionType.TOOL_RUNNING_ERROR,
            "未找到 git 命令",
            is_retryable=False,
            suggestion="联系管理员安装 git 后再试",
        ) from e
    except OSError as e:
        await _remove_dir(target)
        raise ToolException(
            ToolExceptionType.TOOL_RUNNING_ERROR,
            f"启动 git 失败：{e}",
            is_retryable=True,
            suggestion="确认服务器 git 环境",
        ) from e

    if result.returncode != 0:
        await _remove_dir(target)

        detail = _redact(
            (result.stderr or result.stdout or "").strip()
        ).replace("\r", "")

        if not detail:
            detail = "git 没有返回错误信息"
        if len(detail) > 1200:
            detail = detail[-1200:]

        low = detail.lower()

        if any(
            key in low
            for key in (
                "authentication failed",
                "could not read username",
                "permission denied",
                "access denied",
                "terminal prompts disabled",
            )
        ):
            raise ToolException(
                ToolExceptionType.TOOL_RUNNING_ERROR,
                f"git clone 认证失败：{detail}",
                is_retryable=False,
                suggestion=(
                    "私有仓库需要凭据；不要在对话里贴 token，"
                    "改用部署环境的 git 凭据或 SSH"
                ),
            )

        if any(
            key in low
            for key in (
                "repository not found",
                "does not appear to be a git repository",
                "invalid repository",
                "not found",
            )
        ):
            raise ToolException(
                ToolExceptionType.TOOL_RUNNING_ERROR,
                f"仓库不存在或地址错误：{detail}",
                is_retryable=False,
                suggestion=(
                    "确认传的是仓库根地址，"
                    "例如 https://github.com/owner/repo.git"
                ),
            )

        if any(
            key in low
            for key in (
                "could not resolve host",
                "failed to connect",
                "connection timed out",
                "unable to access",
                "network is unreachable",
            )
        ):
            raise ToolException(
                ToolExceptionType.TOOL_RUNNING_ERROR,
                f"网络错误：{detail}",
                is_retryable=True,
                suggestion="稍后重试；持续失败就确认地址或换网络",
            )

        raise ToolException(
            ToolExceptionType.TOOL_RUNNING_ERROR,
            f"git clone 失败（exit {result.returncode}）：{detail}",
            is_retryable=True,
            suggestion="确认 URL；重试一次，持续失败就别再试",
        )

    if not await asyncio.to_thread(target.exists):
        raise ToolException(
            ToolExceptionType.TOOL_RUNNING_ERROR,
            "git 返回成功但本地目录不存在",
            is_retryable=True,
            suggestion="重试一次；持续失败就检查磁盘权限",
        )

    # 二次校验：必须只有最近 1 个提交；空仓库允许 0 个
    commit_count = await _git_value(
        ["-C", str(target), "rev-list", "--count", "HEAD"]
    )
    if commit_count not in {"", "0", "1"}:
        await _remove_dir(target)
        raise ToolException(
            ToolExceptionType.TOOL_RUNNING_ERROR,
            (
                f"克隆结果包含 {commit_count} 个提交，"
                "不满足“只拉最近 1 个提交”的硬性条件"
            ),
            is_retryable=False,
            suggestion=(
                "该仓库或服务器不支持浅克隆，换一个源；"
                "或手动处理后只保留当前版本"
            ),
        )

    try:
        rel = target.relative_to(base).as_posix()
    except ValueError:
        rel = directory

    return rel