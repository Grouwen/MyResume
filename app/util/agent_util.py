import asyncio
from pathlib import Path

from langchain_core.tools import BaseTool
from langgraph.runtime import Runtime

from app.agent.core.plug_regis import PlugRegis
from app.agent.core.tool_regis import ToolRegis
from app.graph.context.main_agent_context import MainAgentContext


def verify_file_path(file_path: str,
                     base_path: Path,
                     must_exist: bool,
                     is_dir: bool = False) -> tuple[bool, str | None]:
    """成功返回 (True, None)；失败返回 (False, 失败原因)。

    内部要 resolve + stat，都是阻塞调用；协程里请用 verify_file_path_async。
    """
    raw = file_path.strip()
    if not raw:
        return False, "路径为空"

    p = Path(raw)
    if p.is_absolute() or p.drive or p.root:
        return False, f"不允许使用绝对路径"

    base = base_path.resolve()
    full = (base / p).resolve()
    if not full.is_relative_to(base):
        return False, f"路径超出允许目录"

    if must_exist:
        if not full.exists():
            return False, "路径不存在"
        if is_dir and not full.is_dir():
            return False, "路径为文件而非目录"
        if not is_dir and not full.is_file():
            return False, "路径为目录而非文件"

    return True, None


async def verify_file_path_async(file_path: str,
                                 base_path: Path,
                                 must_exist: bool,
                                 is_dir: bool = False) -> tuple[bool, str | None]:
    """verify_file_path 的异步版：校验会打 resolve/stat，丢线程池免得卡住事件循环。"""
    return await asyncio.to_thread(verify_file_path, file_path, base_path, must_exist, is_dir)


def get_active_tools(tool_regis:ToolRegis,plug_regis:PlugRegis,
                     loaded_plugs: dict[str, str]) -> list[BaseTool]:
    """基础工具 + 已加载 plug 里的工具。"""
    tools: list[BaseTool] = list(tool_regis.load())
    all_plug = plug_regis.load()

    for plug_name in loaded_plugs:
        plug = all_plug.get(plug_name)
        if plug is None:
            continue
        tools.extend(plug.tools)

    # bind_tools 要求工具名唯一，跨这两个注册表也得查
    seen: set[str] = set()
    for tool in tools:
        if tool.name in seen:
            raise ValueError(f"工具重名：{tool.name}，bind_tools 会失败")
        seen.add(tool.name)

    return tools
