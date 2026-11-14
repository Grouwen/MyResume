"""自动加载 app/agent/tool 包下所有被 @tool 装饰的工具。"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from types import ModuleType

from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)


class ToolLoadError(RuntimeError):
    """工具加载失败。"""


class ToolRegis:
    def __init__(self, package_name: str) -> None:
        self.package_name = package_name
        self._tools: list[BaseTool] | None = self._collect()

    def load(self) -> list[BaseTool]:
        if self._tools is None:
            self._tools = self._collect()
        return list(self._tools)

    def reload(self) -> list[BaseTool]:
        """开发期热更新：重新执行各模块代码后再收集一次。"""
        for module in self._modules():
            try:
                importlib.reload(module)
            except Exception as e:
                raise ToolLoadError(
                    f"工具模块重载失败：{module.__name__} -> {type(e).__name__}: {e}"
                ) from e
        self._tools = None
        return self.load()

    def get(self, name: str) -> BaseTool:
        tools = self.load()
        for item in tools:
            if item.name == name:
                return item
        raise ToolLoadError(f"工具不存在：{name}（已加载：{[t.name for t in tools]}）")

    # ---------------- 内部实现 ----------------
    def _modules(self) -> list[ModuleType]:
        package = self._import(self.package_name, "工具包")
        if not hasattr(package, "__path__"):
            raise ToolLoadError(f"{self.package_name} 不是一个包（缺少 __path__），无法扫描")

        names = sorted(
            info.name
            for info in pkgutil.walk_packages(
                package.__path__,
                prefix=f"{package.__name__}.",
                onerror=self._raise_walk_error,   # 子包导入失败也别被 pkgutil 吞掉
            )
        )
        return [package] + [self._import(n, "工具模块") for n in names]

    @staticmethod
    def _raise_walk_error(module_name: str) -> None:
        raise ToolLoadError(f"扫描子包失败（无法导入以继续遍历）：{module_name}")

    def _import(self, module_name: str, kind: str) -> ModuleType:
        try:
            return importlib.import_module(module_name)
        except Exception as e:
            raise ToolLoadError(
                f"{kind}导入失败：{module_name} -> {type(e).__name__}: {e}"
            ) from e

    @staticmethod
    def _defined_in(obj: BaseTool, module_name: str) -> bool:
        """只收本模块里 @tool 出来的，排除从别处 import 进来的工具。"""
        func = getattr(obj, "coroutine", None) or getattr(obj, "func", None)
        owner = getattr(func, "__module__", None)
        return owner is None or owner == module_name

    def _collect(self) -> list[BaseTool]:
        tools: list[BaseTool] = []
        owner_of: dict[str, str] = {}
        seen: set[int] = set()

        for module in self._modules():
            for attr_name, obj in vars(module).items():
                if not isinstance(obj, BaseTool) or id(obj) in seen:
                    continue
                if not self._defined_in(obj, module.__name__):
                    continue
                seen.add(id(obj))

                if not obj.name:
                    raise ToolLoadError(f"工具缺少 name：{module.__name__}.{attr_name}")
                if obj.name in owner_of:
                    raise ToolLoadError(
                        f"工具名重复：{obj.name}"
                        f"（{owner_of[obj.name]} 与 {module.__name__}.{attr_name}），LLM 工具名必须唯一"
                    )
                owner_of[obj.name] = f"{module.__name__}.{attr_name}"
                tools.append(obj)
                logger.debug("加载工具 %s <- %s", obj.name, module.__name__)

        if not tools:
            raise ToolLoadError(f"{self.package_name} 下未发现任何 @tool 工具")

        logger.info("已加载 %d 个工具：%s", len(tools), [t.name for t in tools])
        return tools

if __name__ == '__main__':
    tool_regis = ToolRegis("app.agent.tool")
    r = tool_regis.load()
    print(r)
    print(len(r))