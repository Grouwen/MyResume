from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from langgraph.checkpoint.memory import MemorySaver
from starlette.middleware.cors import CORSMiddleware

from app.agent.core.plug_regis import PlugRegis
from app.agent.core.skill_regis import SkillRegis
from app.agent.core.tool_regis import ToolRegis
from app.agent.main_agent import build_main_agent, main_agent_no_checkpointer
from app.api.exception_handler import register_exception_handlers
from app.api.router.chat_router import chat_router
from app.api.router.session_router import session_router
from app.config.checkpoint_config import checkpoint_config
from app.core.constants import LOG_FILE_PATH, PROMPTS_PATH, AGENT_WORK_SPACE_PATH
from app.core.loger import setup_logging, get_logger
from app.infrastructure.checkpoint import open_checkpointer
from app.infrastructure.httpx_regis import init_httpx_regis, HttpxSpec
from app.infrastructure.llm.llm_factory import LLMFactory
from app.infrastructure.pgsql_engine import init_pgsql_session_factory, close_pgsql_engine
from app.oper.httpx_oper import HttpxOper

logger = get_logger(__name__)


def ensure_dir():
    # log文件目录
    LOG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # prompt目录
    PROMPTS_PATH.mkdir(parents=True, exist_ok=True)
    # agent工作目录
    AGENT_WORK_SPACE_PATH.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 前置准备
    ensure_dir()
    setup_logging(LOG_FILE_PATH)

    # 退出顺序由 AsyncExitStack 保证：checkpointer → pg engine → httpx
    async with AsyncExitStack() as stack:
        # ---- httpx 连接池 ----
        httpx_regis = init_httpx_regis(
            HttpxSpec(name="default", timeout=30.0),
            HttpxSpec(name="llm", timeout=300.0),
        )
        stack.push_async_callback(httpx_regis.aclose)

        # ---- 业务库（SQLAlchemy） ----
        pgsql_session_factory = await init_pgsql_session_factory()
        stack.push_async_callback(close_pgsql_engine)

        # ---- 注册中心 ----
        tool_regis = ToolRegis("app.agent.tool")
        plug_regis = PlugRegis(
            "app.agent.plug",
            reserved_names={tool.name for tool in tool_regis.load()},
        )
        skill_regis = SkillRegis("app.agent.skill")

        # ---- LangGraph checkpointer（同一个 PG 库，独立小池） ----
        if checkpoint_config.backend == "postgres":
            checkpointer = await stack.enter_async_context(open_checkpointer())
        else:
            logger.warning("CHECKPOINT_BACKEND=memory：重启后对话历史会丢失")
            checkpointer = MemorySaver()

        app.state.checkpointer = checkpointer
        app.state.main_agent = build_main_agent(checkpointer)
        app.state.main_agent_no_checkpointer = main_agent_no_checkpointer

        # ---- 挂载到 app.state ----
        app.state.httpx_oper = HttpxOper(httpx_regis.get("default"))
        app.state.llm_factory = LLMFactory(httpx_regis.get("llm"))
        app.state.plug_regis = plug_regis
        app.state.skill_regis = skill_regis
        app.state.tool_regis = tool_regis
        app.state.pgsql_session_factory = pgsql_session_factory

        yield


app = FastAPI(description="简历助手", lifespan=lifespan)

app.include_router(router=chat_router)
app.include_router(router=session_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)