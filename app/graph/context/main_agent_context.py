from dataclasses import dataclass
from pathlib import Path

from langchain_core.language_models import BaseChatModel

from app.agent.core.plug_regis import PlugRegis
from app.agent.core.skill_regis import SkillRegis
from app.agent.core.tool_regis import ToolRegis
from app.oper.httpx_oper import HttpxOper


@dataclass(frozen=True)
class MainAgentContext:
    agent_work_space: Path # agent工作区路径
    agent_md_content: str # agent.md文件内容

    system_prompt_template: str # 系统提示词模板

    llm_model: BaseChatModel # llm

    plug_regis: PlugRegis # plug注册中心
    skill_regis: SkillRegis # skill注册中心
    tool_regis: ToolRegis  # tool注册中心

    httpx_oper: HttpxOper # httpx_oper
