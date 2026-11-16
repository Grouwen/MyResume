"""跨轮预算回归测试：真实图 + MemorySaver + 假 LLM，不打真实接口。

验证点：
1. 同一 thread 连续多轮，每轮预算独立（turn_llm_steps 每轮从 0 起）
2. 预算耗尽那轮：budget_stop=True 且注入收尾指令
3. 用户回「继续」的下一轮：额度恢复，能重新 bind_tools 干活
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import MemorySaver
from pydantic import Field

import app.graph.node.main_agent.call_llm_node as cln
from app.agent.core.plug_regis import PlugRegis
from app.agent.core.skill_regis import SkillRegis
from app.agent.core.tool_regis import ToolRegis
from app.agent.main_agent import build_main_agent
from app.graph.context.main_agent_context import MainAgentContext
from app.graph.state.main_agent.input_state import InputMainAgentState

SEEN: list = []


class FakeLLM(BaseChatModel):
    plan: list = Field(default_factory=list)
    idx: int = 0

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        SEEN.append(list(messages))
        if self.idx < len(self.plan):
            msg = self.plan[self.idx]
        else:
            msg = AIMessage("done")
        self.idx += 1
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def bind_tools(self, tools, **kwargs):
        return self


def tool_call(i: int) -> AIMessage:
    return AIMessage(content="", tool_calls=[{
        "name": "ls", "args": {"dir_path": "."}, "id": f"call_{i}", "type": "tool_call",
    }])


async def main():
    cln.MAX_LLM_STEPS_PER_TURN = 2  # 缩小上限，2 次决策后收口

    graph = build_main_agent(MemorySaver())
    tool_regis = ToolRegis("app.agent.tool")
    plug_regis = PlugRegis(
        "app.agent.plug",
        reserved_names={tool.name for tool in tool_regis.load()},
    )
    ctx = MainAgentContext(
        agent_work_space=Path("agent_work_space").resolve(),
        agent_md_content="",
        system_prompt_template="你是助手\n{% for s in skills %}{{ s.name }}{% endfor %}",
        llm_model=FakeLLM(),
        plug_regis=plug_regis,
        skill_regis=SkillRegis("app.agent.skill"),
        tool_regis=tool_regis,
        httpx_oper=None,
    )
    config = {"configurable": {"thread_id": "test:budget"}, "recursion_limit": 50}

    async def turn(plan, label):
        SEEN.clear()
        ctx.llm_model.plan = plan
        ctx.llm_model.idx = 0
        await graph.ainvoke(InputMainAgentState(user_input="测试"), config=config, context=ctx)
        snap = await graph.aget_state(config)
        v = snap.values
        stopped = any(
            getattr(m, "content", "") == cln.BUDGET_STOP_PROMPT
            for m in (SEEN[-1] if SEEN else [])
        )
        print(f"{label}: budget_stop={v.get('budget_stop')} "
              f"turn_llm_steps={v.get('turn_llm_steps')} 收尾指令注入={stopped}")
        return v, stopped

    v1, s1 = await turn([tool_call(1), AIMessage("第一轮完成")], "轮1")
    assert v1["budget_stop"] is False and v1["turn_llm_steps"] == 2, v1

    v2, s2 = await turn([tool_call(2), AIMessage("第二轮完成")], "轮2(继续)")
    assert v2["budget_stop"] is False, f"旧 bug：第二轮被锁死 {v2}"
    assert v2["turn_llm_steps"] == 2, f"计数未复位: {v2['turn_llm_steps']}"

    v3, s3 = await turn([tool_call(3), tool_call(4), tool_call(5)], "轮3(超预算)")
    assert v3["budget_stop"] is True and s3, v3

    v4, s4 = await turn([tool_call(6), AIMessage("继续后真的干完了")], "轮4(再继续)")
    assert v4["budget_stop"] is False, f"回复继续后仍未恢复额度 {v4}"
    assert v4["turn_llm_steps"] == 2, v4["turn_llm_steps"]

    print("\nPASS：预算按轮独立，超限只汇报+询问，回「继续」立即恢复工具与额度")


if __name__ == "__main__":
    asyncio.run(main())