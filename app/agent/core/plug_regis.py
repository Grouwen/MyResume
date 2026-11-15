"""插件注册中心：扫 app/agent/plug 下带 PLUG_META 的子包，收里面的 @tool。

插件布局：
    app/agent/plug/<插件包>/__init__.py
        PLUG_META = {
            "name": ..., "description": ..., "version": ...,
            # 可选：工具代码所在目录，相对插件根目录；不写 = 插件根目录本身
            "tools_path": "tools",
        }
    app/agent/plug/<插件包>/tools/xxx.py     # 写了 tools_path="tools" 时，工具放这里
        @tool 装饰的工具
    app/agent/plug/<插件包>/xxx.py           # 不写 tools_path 时，工具直接放插件根目录
        @tool 装饰的工具

tools_path 只换工具代码的存放位置，插件根目录永远是 PLUG_META 所在的插件包本身
（比如 github_research/），工具目录必须是它的子目录。
"""

import importlib
from app.core.loger import get_logger
import pkgutil
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from jinja2 import Template
from langchain_core.tools import BaseTool

logger = get_logger(__name__)


class PlugLoadError(RuntimeError):
    """插件加载失败。"""


@dataclass
class PlugInfo:
    name: str                     # 插件名，来自 PLUG_META["name"]
    description: str              # 一句话用途
    version: str                  # 版本号
    package_name: str = ""        # 真实模块路径，调试用
    tools_path: str = ""          # 工具目录，相对插件根；空 = 插件根目录本身
    tools_dir: str = ""           # 解析后的工具目录绝对路径，调试用
    tools: list[BaseTool] = field(default_factory=list)


class PlugRegis:
    def __init__(self, package_name: str, *, reserved_names: set[str] | None = None) -> None:
        self.package_name = package_name
        # 外部已占用的工具名（通常是基础工具），插件里不许再出现同名工具
        self._reserved_names: frozenset[str] = frozenset(reserved_names or ())
        # 构造即加载：插件写错、工具重名，启动时就炸，别等第一轮对话
        # 直调 _discover 不调 self.load：load 是公开方法，将来子类重写会在构造期被虚调用
        self._plugs: dict[str, PlugInfo] | None = self._discover()

    # ---------------- 对外接口 ----------------
    def load(self) -> dict[str, PlugInfo]:
        """构造时已扫过盘，这里只回缓存；reload 之后才会重新扫。"""
        if self._plugs is None:
            self._plugs = self._discover()
        return self._plugs

    def reload(self) -> dict[str, PlugInfo]:
        """开发期改了插件想马上生效，调这个。"""
        self._plugs = None
        return self.load()

    def get(self, name: str) -> PlugInfo:
        plugs = self.load()
        plug = plugs.get(name)
        if plug is None:
            raise PlugLoadError(
                f"插件不存在：{name}（已加载：{sorted(plugs) or '无'}）"
            )
        return plug

    def list_tools(self) -> list[BaseTool]:
        """所有插件里的工具汇总，喂给 bind_tools 用的。"""
        tools: list[BaseTool] = []
        for plug in self.load().values():
            tools.extend(plug.tools)
        return tools

    # ---------------- 内部实现 ----------------
    def _discover(self) -> dict[str, PlugInfo]:
        package = self._import(self.package_name, "插件包")
        if not hasattr(package, "__path__"):
            raise PlugLoadError(f"{self.package_name} 不是包（没有 __path__），扫不了")

        plugs: dict[str, PlugInfo] = {}
        for info in pkgutil.walk_packages(
                package.__path__,
                prefix=f"{package.__name__}.",
                onerror=self._raise_walk_error,   # 子包导入失败也别被 pkgutil 吞掉
        ):
            if not info.ispkg:
                continue

            plug_pkg = self._import(info.name, "插件包")
            meta = getattr(plug_pkg, "PLUG_META", None)
            if not meta:
                continue  # 没有 PLUG_META 的子包不是插件，跳过

            name = str(meta.get("name", "")).strip()
            if not name:
                raise PlugLoadError(f"{info.name} 的 PLUG_META 缺少 name")
            if name in plugs:
                raise PlugLoadError(f"插件名重复：{name}（{info.name}）")

            # 新增：没写 enabled 就是 True，老插件不用改
            enabled = meta.get("enabled", True)
            if not isinstance(enabled, bool):
                raise PlugLoadError(
                    f"{info.name} 的 PLUG_META.enabled 必须是 True 或 False"
                    f"（不加引号），现在是 {enabled!r}"
                )
            if not enabled:
                logger.info("插件已禁用，跳过：%s（%s）", name, info.name)
                continue

            # 新增：没写 tools_path 就用插件根目录，工具照旧放插件包里
            tools_path = self._normalize_tools_path(meta.get("tools_path"), info.name)
            tools_root = self._tools_root(plug_pkg, tools_path)

            plugs[name] = PlugInfo(
                name=name,
                description=str(meta.get("description", "")).strip(),
                version=str(meta.get("version", "0.0.0")).strip(),
                package_name=info.name,
                tools_path=tools_path,
                tools_dir=self._module_dir(tools_root),
                tools=self._collect_tools(tools_root),
            )
            logger.info("加载插件 %s <- %s", name, info.name)

        self._check_tool_name_unique(plugs)

        self._check_reserved_names(plugs)

        # 日志全在文件内部打，app.py 那边不用再写循环
        for plug in plugs.values():
            logger.info(
                "加载plug-> %s: %s 版本号:%s 工具目录:%s tool数量:%d",
                plug.name, plug.description, plug.version,
                plug.tools_dir or "插件根目录", len(plug.tools),
            )

        if not plugs:
            logger.info("没有发现任何插件：%s", self.package_name)
        else:
            logger.info("已加载 %d 个插件：%s", len(plugs), list(plugs))
        return plugs

    def _tools_root(self, plug_pkg, tools_path: str):
        """拿到工具代码的根模块：tools_path 为空就是插件包自己。"""
        if not tools_path:
            return plug_pkg

        module_name = f"{plug_pkg.__name__}.{tools_path}"
        return self._import(
            module_name,
            f"工具目录（PLUG_META.tools_path={tools_path!r}，目录里需要 __init__.py）",
        )

    def _collect_tools(self, tools_root) -> list[BaseTool]:
        """扫工具根模块下的所有模块，收本模块 @tool 出来的工具。"""
        # tools_path 指向单个 .py 文件而不是包：只收这个模块里的工具
        if not hasattr(tools_root, "__path__"):
            tools: list[BaseTool] = []
            self._extend_tools(tools, set(), tools_root)
            return tools

        tools: list[BaseTool] = []
        seen: set[int] = set()

        for info in pkgutil.walk_packages(
                tools_root.__path__,
                prefix=f"{tools_root.__name__}.",
                onerror=self._raise_walk_error,
        ):
            if info.ispkg:
                continue

            module = self._import(info.name, "插件模块")
            self._extend_tools(tools, seen, module)

        return tools

    def _extend_tools(self, tools: list[BaseTool], seen: set[int], module) -> None:
        for attr_name, obj in vars(module).items():
            if not isinstance(obj, BaseTool) or id(obj) in seen:
                continue
            # 只收本模块 @tool 出来的，排除从别处 import 进来的
            if not self._defined_in(obj, module.__name__):
                continue
            seen.add(id(obj))

            if not obj.name:
                raise PlugLoadError(f"工具缺少 name：{module.__name__}.{attr_name}")
            tools.append(obj)

    @staticmethod
    def _normalize_tools_path(raw, plug_module: str) -> str:
        """校验并规范化 PLUG_META.tools_path，返回相对插件根目录的点分模块路径。

        None / "" / "."  -> ""           工具就放插件根目录，老插件不用改
        "tools" / "./tools" / "tools/"   -> "tools"
        "sub/tools" / "sub\\tools"        -> "sub.tools"
        """
        if raw is None:
            return ""
        if not isinstance(raw, str):
            raise PlugLoadError(
                f"{plug_module} 的 PLUG_META.tools_path 必须是字符串"
                f"（相对插件根目录的目录名），现在是 {raw!r}"
            )

        text = raw.strip().replace("\\", "/")
        if not text or text == ".":
            return ""

        if text.startswith("/"):
            raise PlugLoadError(
                f"{plug_module} 的 PLUG_META.tools_path 必须是相对插件根目录的路径，"
                f"不能用绝对路径：{raw!r}"
            )

        parts = [p for p in PurePosixPath(text).parts if p not in ("", ".")]
        if ".." in parts:
            raise PlugLoadError(
                f"{plug_module} 的 PLUG_META.tools_path 不能用 .. 跳出插件目录：{raw!r}"
            )
        if not parts:
            return ""
        return ".".join(parts)

    @staticmethod
    def _module_dir(mod) -> str:
        """模块所在目录的绝对路径，给 PlugInfo.tools_dir 调试用。"""
        path = getattr(mod, "__file__", None)
        return str(Path(path).parent) if path else ""

    @staticmethod
    def _defined_in(obj: BaseTool, module_name: str) -> bool:
        """只收本模块里 @tool 出来的，排除从别处 import 进来的工具。"""
        func = getattr(obj, "coroutine", None) or getattr(obj, "func", None)
        owner = getattr(func, "__module__", None)
        return owner is None or owner == module_name

    @staticmethod
    def _check_tool_name_unique(plugs: dict[str, PlugInfo]) -> None:
        """工具名跨插件必须唯一，否则 bind_tools 后 LLM 分不清调哪个。"""
        owner: dict[str, str] = {}
        for plug in plugs.values():
            for tool in plug.tools:
                if tool.name in owner:
                    raise PlugLoadError(
                        f"工具名重复：{tool.name}"
                        f"（{owner[tool.name]} 与 {plug.name}），LLM 工具名必须唯一"
                    )
                owner[tool.name] = plug.name

    @staticmethod
    def _raise_walk_error(module_name: str) -> None:
        raise PlugLoadError(f"扫描子包失败（无法导入以继续遍历）：{module_name}")

    def _import(self, module_name: str, kind: str):
        try:
            return importlib.import_module(module_name)
        except Exception as e:
            raise PlugLoadError(
                f"{kind}导入失败：{module_name} -> {type(e).__name__}: {e}"
            ) from e

    def _check_reserved_names(self, plugs: dict[str, PlugInfo]) -> None:
        """插件工具不能和外部已占用的工具名（基础工具）重名，否则 bind_tools 会炸。"""
        if not self._reserved_names:
            return

        for plug in plugs.values():
            for tool in plug.tools:
                if tool.name in self._reserved_names:
                    raise PlugLoadError(
                        f"工具名冲突：插件 {plug.name}（{plug.package_name}）的工具 {tool.name} "
                        f"已被基础工具占用，LLM 工具名必须全局唯一"
                    )