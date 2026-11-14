"""技能注册中心：只负责三件事——找目录、扫技能、缓存正文。

布局（包名固定 app.agent.skill）：
  app/agent/skill/<技能名>/SKILL.md
  frontmatter 必须有 name / description，name 必须和目录名一致
"""

import importlib
from app.core.loger import get_logger
import re
from dataclasses import dataclass
from pathlib import Path

logger = get_logger(__name__)

SKILL_FILE = "SKILL.md"

# 只认文件开头 --- 包起来的那段
_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)


class SkillLoadError(RuntimeError):
    """技能加载失败。"""


@dataclass(frozen=True)
class SkillInfo:
    name: str                     # 技能名
    description: str              # 一句话用途，你渲染进系统提示词用的
    body: str                     # SKILL.md 去掉 frontmatter 的正文，第 2 层才给模型
    path: Path                    # 技能目录绝对路径
    resources: tuple[str, ...]    # 目录下除 SKILL.md 外的文件（正斜杠相对路径）


def _parse_skill_file(md_path: Path) -> tuple[dict[str, str], str]:
    """拆出 frontmatter 和正文。返回 (meta, body)。"""
    try:
        text = md_path.read_text(encoding="utf-8")
    except Exception as e:
        raise SkillLoadError(
            f"读取失败：{md_path} -> {type(e).__name__}: {e}"
        ) from e

    match = _FRONTMATTER.match(text)
    if not match:
        raise SkillLoadError(
            f"{md_path} 开头缺少 frontmatter，格式：\n"
            "---\nname: 技能名\ndescription: 一句话用途\n---"
        )

    meta: dict[str, str] = {}
    for raw_line in match.group(1).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # 续行/嵌套写法不认，直接炸。静默截断最难查，宁可启动就报错
        if raw_line[:1] in {" ", "\t"}:
            raise SkillLoadError(
                f"{md_path} 的 frontmatter 不支持多行写法，"
                "name 和 description 各写一行"
            )
        key, sep, value = raw_line.partition(":")
        if not sep:
            continue
        meta[key.strip()] = value.strip().strip('"').strip("'")

    return meta, text[match.end():].lstrip("\n")


def _resolve_skill_root(package_name: str) -> Path:
    """包名 -> 磁盘目录。只认包名，不认路径。

    不收路径的原因：路径受当前工作目录影响，换个 cwd 就偏了。
    包名走 import 那套查找规则，你能 import 进来，就一定能找到它躺在哪。
    """
    name = package_name.strip()
    if not name:
        raise SkillLoadError("技能包名不能为空，例：SkillRegis('app.agent.skill')")
    if "/" in name or "\\" in name:
        raise SkillLoadError(
            f"只收包名不收路径：{package_name} -> 写成 'app.agent.skill'"
        )

    try:
        package = importlib.import_module(name)
    except ImportError as e:
        raise SkillLoadError(
            f"技能包导入失败：{name} -> {type(e).__name__}: {e}"
        ) from e

    locations = list(getattr(package, "__path__", []))
    if not locations:
        raise SkillLoadError(f"{name} 是模块不是包（没有 __path__），技能得放在包下面")
    return Path(locations[0]).resolve()


class SkillRegis:
    def __init__(self, package_name: str) -> None:
        self.skill_root = _resolve_skill_root(package_name)
        # 构造即加载：包名写错、SKILL.md 写错，启动时就炸，别等第一轮对话
        # 直调 _scan 不调 self.load：load 是公开方法，将来子类重写会在构造期被虚调用
        self._skills: dict[str, SkillInfo] | None = self._scan()

    # ---------------- 对外接口 ----------------
    def load(self) -> dict[str, SkillInfo]:
        """构造时已扫过盘，这里只回缓存；reload 之后才会重新扫。"""
        if self._skills is None:
            self._skills = self._scan()
        return self._skills

    def reload(self) -> dict[str, SkillInfo]:
        """开发期改了 SKILL.md 想马上生效，调这个。"""
        self._skills = None
        return self.load()

    def get(self, name: str) -> SkillInfo:
        skills = self.load()
        skill = skills.get(name)
        if skill is None:
            raise SkillLoadError(
                f"技能不存在：{name}（可用：{sorted(skills) or '无'}）"
            )
        return skill

    # ---------------- 内部实现 ----------------
    def _scan(self) -> dict[str, SkillInfo]:
        # 目录不存在 = 配置错，炸
        if not self.skill_root.is_dir():
            raise SkillLoadError(f"技能目录不存在：{self.skill_root}")

        skills: dict[str, SkillInfo] = {}
        for skill_dir in sorted(self.skill_root.iterdir()):
            # 下划线/点开头的是工作目录，不是技能
            if not skill_dir.is_dir() or skill_dir.name.startswith(("_", ".")):
                continue

            md_path = skill_dir / SKILL_FILE
            if not md_path.is_file():
                raise SkillLoadError(f"技能目录缺少 {SKILL_FILE}：{skill_dir}")

            meta, body = _parse_skill_file(md_path)
            name = meta.get("name", "").strip()
            description = meta.get("description", "").strip()

            if not name:
                raise SkillLoadError(f"{md_path} 的 frontmatter 缺少 name")
            if not description:
                raise SkillLoadError(f"{md_path} 的 frontmatter 缺少 description")
            if not body.strip():
                raise SkillLoadError(f"{md_path} 正文是空的，只有 frontmatter")
            if name != skill_dir.name:
                raise SkillLoadError(
                    f"技能名和目录名对不上：name={name} 目录={skill_dir.name}"
                )
            if name in skills:
                raise SkillLoadError(f"技能名重复：{name}")

            skills[name] = SkillInfo(
                name=name,
                description=description,
                body=body,
                path=skill_dir,
                resources=self._resources(skill_dir),
            )
            logger.info(f"加载技能 {name} <- {md_path}")

        # 目录存在但一个技能都没有 = 正常（还没写），不炸
        if not skills:
            logger.info(f"技能目录为空，未加载任何技能：{self.skill_root}")
        else:
            logger.info(f"已加载 {len(skills)} 个技能：{list(skills)}")
        return skills

    @staticmethod
    def _resources(skill_dir: Path, limit: int = 30) -> tuple[str, ...]:
        """技能目录下除 SKILL.md 之外的文件，给模型当第 3 层的线索。"""
        files: list[str] = []
        for path in sorted(skill_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(skill_dir)
            rel_str = rel.as_posix()          # 统一正斜杠，别让模型看到 Windows 反斜杠
            if rel.parts[0] in {"__pycache__", "__init__.py"}:
                continue
            if rel_str == SKILL_FILE:         # 必须比字符串，Path 和 str 永远不相等
                continue
            files.append(rel_str)
            if len(files) >= limit:
                break
        return tuple(files)


if __name__ == "__main__":
    regis = SkillRegis("app.agent.skill")
    print(f"技能目录：{regis.skill_root}")
    for skill in regis.load().values():
        print(f"{skill.name}: {skill.description}（附加文件 {len(skill.resources)} 个）")