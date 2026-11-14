from fastapi import Request, Depends
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.core.plug_regis import PlugRegis
from app.agent.core.skill_regis import SkillRegis
from app.agent.core.tool_regis import ToolRegis
from app.infrastructure.llm.llm_factory import LLMFactory
from app.oper.httpx_oper import HttpxOper
from app.repository.account_verify_pg_repo import AccountVerifyPgRepo
from app.repository.running_chat_pg_repo import RunningChatPgRepo
from app.repository.session_pg_repo import SessionPgRepo
from app.repository.user_pg_repo import UserPgRepo
from app.service.chat_service import ChatService
from langgraph.checkpoint.base import BaseCheckpointSaver
from app.service.session_service import SessionService
from app.service.user_service import UserService

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


# 同一次请求内 FastAPI 会缓存 get_pg_session，下面各仓储共享同一个 session（同一笔事务）
async def get_user_pg_repo(session: AsyncSession = Depends(get_pg_session)) -> UserPgRepo:
    return UserPgRepo(session=session)


async def get_account_verify_pg_repo(session: AsyncSession = Depends(get_pg_session)) -> AccountVerifyPgRepo:
    return AccountVerifyPgRepo(session=session)


async def get_session_pg_repo(session: AsyncSession = Depends(get_pg_session)) -> SessionPgRepo:
    return SessionPgRepo(session=session)


async def get_running_chat_pg_repo(session: AsyncSession = Depends(get_pg_session)) -> RunningChatPgRepo:
    return RunningChatPgRepo(session=session)


# ---------------- Service ----------------
async def get_chat_service(
        llm_factory: LLMFactory = Depends(get_llm_factory),
        plug_regis: PlugRegis = Depends(get_plug_regis),
        skill_regis: SkillRegis = Depends(get_skill_regis),
        tool_regis: ToolRegis = Depends(get_tool_regis),
        httpx_oper: HttpxOper = Depends(get_httpx_oper),
        session_pg_repo: SessionPgRepo = Depends(get_session_pg_repo),
        running_chat_pg_repo: RunningChatPgRepo = Depends(get_running_chat_pg_repo),
        pg_session_factory: async_sessionmaker[AsyncSession] = Depends(get_pg_session_factory),
        main_agent: CompiledStateGraph = Depends(get_main_agent),
) -> ChatService:
    return ChatService(llm_factory=llm_factory,
                       plug_regis=plug_regis,
                       skill_regis=skill_regis,
                       tool_regis=tool_regis,
                       httpx_oper=httpx_oper,
                       session_pg_repo=session_pg_repo,
                       running_chat_pg_repo=running_chat_pg_repo,
                       pg_session_factory=pg_session_factory,
                       main_agent=main_agent)

async def get_session_service(
        session_pg_repo: SessionPgRepo = Depends(get_session_pg_repo),
        running_chat_pg_repo: RunningChatPgRepo = Depends(get_running_chat_pg_repo),
        checkpointer: BaseCheckpointSaver = Depends(get_checkpointer),
        graph: CompiledStateGraph = Depends(get_main_agent),
) -> SessionService:
    return SessionService(
        session_pg_repo=session_pg_repo,
        running_chat_pg_repo=running_chat_pg_repo,
        checkpointer=checkpointer,
        graph=graph,
    )

async def get_user_service(
        user_pg_repo: UserPgRepo = Depends(get_user_pg_repo),
        account_verify_pg_repo: AccountVerifyPgRepo = Depends(get_account_verify_pg_repo),
) -> UserService:
    return UserService(
        user_pg_repo=user_pg_repo,
        account_verify_pg_repo=account_verify_pg_repo,
    )