from fastapi import Request, Depends
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.core.plug_regis import PlugRegis
from app.agent.core.skill_regis import SkillRegis
from app.agent.core.tool_regis import ToolRegis
from app.infrastructure.llm.llm_factory import LLMFactory
from app.oper.httpx_oper import HttpxOper
from app.repository.pg_repository import PgRepository
from app.service.chat_service import ChatService
from langgraph.checkpoint.base import BaseCheckpointSaver
from app.service.session_service import SessionService

async def get_llm_factory(request: Request) -> LLMFactory:
    return request.app.state.llm_factory


async def get_plug_regis(request: Request) -> PlugRegis:
    return request.app.state.plug_regis


async def get_skill_regis(request: Request) -> SkillRegis:
    return request.app.state.skill_regis


async def get_tool_regis(request: Request) -> ToolRegis:
    return request.app.state.tool_regis


async def get_httpx_oper(request: Request) -> HttpxOper:
    return request.app.state.httpx_oper

async def get_checkpointer(request: Request) -> BaseCheckpointSaver:
    return request.app.state.checkpointer

# ---------------- Agent ----------------
async def get_main_agent(request: Request) -> CompiledStateGraph:
    return request.app.state.main_agent


# ---------------- Repository ----------------
async def get_pg_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    return request.app.state.pgsql_session_factory


async def get_pg_session(request: Request):
    pgsql_session_factory = request.app.state.pgsql_session_factory
    async with pgsql_session_factory.begin() as session:
        yield session


async def get_pg_repository(session: AsyncSession = Depends(get_pg_session)) -> PgRepository:
    return PgRepository(session=session)


# ---------------- Service ----------------
async def get_chat_service(
        llm_factory: LLMFactory = Depends(get_llm_factory),
        plug_regis: PlugRegis = Depends(get_plug_regis),
        skill_regis: SkillRegis = Depends(get_skill_regis),
        tool_regis: ToolRegis = Depends(get_tool_regis),
        httpx_oper: HttpxOper = Depends(get_httpx_oper),
        pg_repository: PgRepository = Depends(get_pg_repository),
        pg_session_factory: async_sessionmaker[AsyncSession] = Depends(get_pg_session_factory),
        main_agent: CompiledStateGraph = Depends(get_main_agent),
) -> ChatService:
    return ChatService(llm_factory=llm_factory,
                       plug_regis=plug_regis,
                       skill_regis=skill_regis,
                       tool_regis=tool_regis,
                       httpx_oper=httpx_oper,
                       pg_repository=pg_repository,
                       pg_session_factory=pg_session_factory,
                       main_agent=main_agent)

async def get_session_service(
        pg_repository: PgRepository = Depends(get_pg_repository),
        checkpointer: BaseCheckpointSaver = Depends(get_checkpointer),
        graph: CompiledStateGraph = Depends(get_main_agent),
) -> SessionService:
    return SessionService(
        pg_repository=pg_repository,
        checkpointer=checkpointer,
        graph=graph,
    )